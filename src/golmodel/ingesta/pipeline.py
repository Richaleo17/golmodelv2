"""Botones de actualización: etapas en orden, una falla no detiene las demás.

"Actualizar datos": resultados+xG+calendario -> liquidación y CLV -> reajuste (semanal si
pasaron >= 7 días) -> lesiones -> alineaciones -> cuotas -> recálculo Módulo 1 y 2.
"Actualizar solo cuotas": cuotas -> recálculo.   "Actualizar alineaciones": alineaciones -> recálculo.
"""
from __future__ import annotations

import logging
import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from golmodel.ingesta.competiciones import elegir_competiciones
from golmodel import db, repositorio as repo
from golmodel.ingesta import estadisticas as est_partido
from golmodel.ingesta import historico_api
from golmodel.ingesta.normalizacion import ResolverEquipos
from golmodel.ingesta.proveedores.cadena import Cadena
from golmodel.ingesta.tipos import PartidoExterno, Calidad, Capacidad, Solicitud
from golmodel.modelos.contexto import emparejar_jugador
from golmodel.servicios import liquidacion, modelo as svc_modelo, prediccion

log = logging.getLogger(__name__)

ETAPAS = {
    "actualizar_todo": ["resultados_xg", "liquidacion_clv", "reajuste_modelo", "lesiones", "alineaciones",
                        "cuotas", "recalculo_modulos"],
    "solo_cuotas": ["cuotas", "recalculo_modulos"],
    "solo_alineaciones": ["alineaciones", "recalculo_modulos"],
    "recalcular": ["recalculo_modulos"],          # con las cuotas ya guardadas: 0 consultas a APIs
}


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Estado:
    conn: object
    cfg: dict
    ahora: datetime
    resolver: ResolverEquipos
    ligas: dict
    modelo: tuple | None = None
    resumen: dict = field(default_factory=dict)

    @property
    def zona(self) -> ZoneInfo:
        return ZoneInfo(self.cfg["zona_horaria"])

    def hoy(self) -> date:
        return self.ahora.astimezone(self.zona).date()

    def dias(self) -> list[date]:
        return [self.hoy() + timedelta(days=k) for k in self.cfg["recalculo"]["dias"]]


class Pipeline:
    def __init__(self, cfg: dict, cadenas: dict[Capacidad, Cadena], *, dsn: str | None = None,
                 reloj: Callable[[], datetime] = ahora_utc, registrador=None):
        self.cfg, self.cadenas, self.dsn, self.reloj = cfg, cadenas, dsn, reloj
        self.registrador = registrador

    def _http_football_data(self):
        """Cliente HTTP de football-data.co.uk (el del proveedor de CSV de la cadena), o None si no está."""
        for cad in self.cadenas.values():
            for e in getattr(cad, "eslabones", []):
                if getattr(e.proveedor, "nombre", "") == "football_data_csv":
                    return e.proveedor.http
        return None

    def _proveedor_mercado(self):
        """El proveedor de The Odds API (si la cadena lo trae) para las competiciones solo-mercado."""
        for cad in self.cadenas.values():
            for e in getattr(cad, "eslabones", []):
                if hasattr(e.proveedor, "deportes_futbol"):
                    return e.proveedor
        return None

    # -------------------------------------------------------------- costo
    def costo_estimado(self, tipo: str) -> dict[str, int]:
        conn = db.conectar(self.dsn)
        try:
            ahora = self.reloj()
            hoy = ahora.astimezone(ZoneInfo(self.cfg["zona_horaria"])).date()
            dias = [hoy + timedelta(days=k) for k in self.cfg["recalculo"]["dias"]]
            partidos = repo.partidos_de_dias(conn, dias)
            con_id = sum(1 for p in partidos if p["api_football_id"])
            cerca = sum(1 for p in partidos if p["api_football_id"] and p["inicio"] - ahora < timedelta(hours=2))
            total: dict[str, int] = defaultdict(int)

            def sumar(cap, n=1, **kw):
                cad = self.cadenas.get(cap)
                if cad:
                    for k, v in cad.costo_estimado(Solicitud(cap, kw.pop("liga", "EPL"), **kw)).items():
                        total[k] += v * n
            ligas = self.cfg["ligas_activas"]
            etapas = ETAPAS[tipo]
            if "resultados_xg" in etapas:
                for l in ligas:
                    sumar(Capacidad.FIXTURES, liga=l)
                sumar(Capacidad.CALENDARIO, desde=ahora.date() - timedelta(days=4), hasta=ahora.date() + timedelta(days=1))
            if "lesiones" in etapas:
                sumar(Capacidad.LESIONES, con_id)
            if "alineaciones" in etapas:
                sumar(Capacidad.ALINEACIONES, cerca)
            if "cuotas" in etapas:
                for l in sorted({p["liga"] for p in partidos if not p.get("solo_mercado")}):   # solo ligas con partidos próximos
                    sumar(Capacidad.CUOTAS, liga=l)
                cm = self.cfg.get("competiciones_mercado") or {}
                if cm.get("activo") and self._proveedor_mercado() is not None and tipo != "snapshot_cierre":
                    total["odds_api"] += int(cm.get("max_creditos_por_actualizacion", 0)) + int(cm.get("max_creditos_resultados", 0))   # cota superior
            return dict(total)
        finally:
            conn.close()

    # -------------------------------------------------------------- ejecución
    def ejecutar(self, tipo: str, *, ejecucion_id: int | None = None) -> int:
        etapas = ETAPAS[tipo]
        with db.bloqueo(self.dsn):
            conn = db.conectar(self.dsn)
            try:
                if ejecucion_id is None:
                    ejecucion_id = repo.nueva_ejecucion(conn, tipo, etapas, self.costo_estimado(tipo))
                    conn.commit()
                if self.registrador is not None:
                    self.registrador.estado["ejecucion"] = ejecucion_id
                st = Estado(conn, self.cfg, self.reloj(), ResolverEquipos(
                    conn, similitud_min=self.cfg["normalizacion"]["similitud_min"],
                    margen_min=self.cfg["normalizacion"]["margen_min"]), repo.ligas(conn))
                for nombre in etapas:
                    repo.etapa(conn, ejecucion_id, nombre, "en_curso")
                    conn.commit()
                    try:
                        resumen = getattr(self, f"_etapa_{nombre}")(st) or {}
                        conn.commit()
                        estado = "omitida" if resumen.get("omitida") else "ok"
                        repo.etapa(conn, ejecucion_id, nombre, estado, resumen=resumen)
                    except Exception as e:  # una etapa que falla no detiene las demás
                        conn.rollback()
                        log.exception("Etapa %s falló", nombre)
                        repo.etapa(conn, ejecucion_id, nombre, "error", error=f"{type(e).__name__}: {e}",
                                   resumen={"traza": traceback.format_exc()[-2000:]})
                    conn.commit()
                repo.cerrar_ejecucion(conn, ejecucion_id)
                conn.commit()
            except Exception:
                conn.rollback()
                conn.execute("UPDATE ejecuciones SET estado='error', terminada_en=now() WHERE id=%s", (ejecucion_id,))
                conn.commit()
                raise
            finally:
                if self.registrador is not None:
                    self.registrador.estado["ejecucion"] = None
                conn.close()
        return ejecucion_id

    def _obtener(self, cap: Capacidad, sol: Solicitud):
        cad = self.cadenas.get(cap)
        return None if cad is None else cad.obtener(sol)

    # -------------------------------------------------------------- etapas
    def _etapa_resultados_xg(self, st: Estado) -> dict:
        res = {"partidos": defaultdict(int), "sin_resolver": 0, "xg": defaultdict(int), "avisos": []}
        hoy = st.hoy()
        for codigo in self.cfg["ligas_activas"]:
            liga = st.ligas.get(codigo)
            if liga is None:
                res["avisos"].append(f"La liga {codigo} no existe en la base: reinicia la API para aplicar las "
                                     "migraciones (docker compose up -d --build).")
                continue
            temporada = repo.temporada_de(liga, st.ahora)
            # Ligas sin CSV de football-data (Liga MX Femenil): sin histórico, se carga por temporada desde las APIs.
            hist = historico_api.autocargar(st.conn, self.cadenas.get(Capacidad.RESULTADOS), liga,
                                            self.cfg["historico"]["temporadas"], hoy, st.resolver)
            if hist is not None:
                res.setdefault("historico_api", {})[codigo] = hist["temporadas"]
                res["avisos"] += hist["avisos"]
            # Córners/tiros/árbitro (mercados nuevos): tabla aparte, no toca los partidos ni el modelo de goles.
            stats = est_partido.autocargar(st.conn, self._http_football_data(), self.cfg, liga, hoy, st.ahora)
            if stats is not None:
                res.setdefault("estadisticas", {})[codigo] = {k: stats[k] for k in ("guardadas", "sin_partido", "archivos")}
                res["avisos"] += stats["avisos"]
            for cap in (Capacidad.FIXTURES, Capacidad.RESULTADOS):
                r = self._obtener(cap, Solicitud(cap, codigo, temporada=temporada, desde=hoy - timedelta(days=7),
                                                 hasta=hoy + timedelta(days=7)))
                if r is None:
                    continue
                res["avisos"] += r.avisos
                eq = r.calidad is not Calidad.DEGRADADA
                for ext in r.datos:
                    lid = st.resolver.resolver(r.proveedor, ext.local, liga["id"])
                    vid = st.resolver.resolver(r.proveedor, ext.visitante, liga["id"])
                    if lid is None or vid is None:
                        res["sin_resolver"] += 1
                        continue
                    _, accion = repo.upsert_partido(st.conn, liga, ext, lid, vid, equivalente=eq)
                    res["partidos"][accion] += 1
                if cap is Capacidad.FIXTURES and r.calidad is Calidad.OPTIMA:
                    break           # el principal ya trae resultados; RESULTADOS solo si hubo respaldo
            if liga["usa_xg"]:
                r = self._obtener(Capacidad.XG, Solicitud(Capacidad.XG, codigo, temporada=temporada))
                for x in (r.datos if r else []):
                    lid = st.resolver.resolver(r.proveedor, x.local, liga["id"])
                    vid = st.resolver.resolver(r.proveedor, x.visitante, liga["id"])
                    p = repo.buscar_partido(st.conn, liga["id"], lid, vid, x.inicio) if lid and vid else None
                    if p is None:
                        res["xg"]["sin_partido"] += 1
                        continue
                    res["xg"][repo.set_xg(st.conn, p["id"], x.xg_local, x.xg_visitante, x.familia_xg)] += 1
        res["calendario"] = self._calendario(st)
        return res

    def _calendario(self, st: Estado) -> dict:
        d0 = st.ahora.date() - timedelta(days=4)
        d1 = st.ahora.date() + timedelta(days=1)
        r = self._obtener(Capacidad.CALENDARIO, Solicitud(Capacidad.CALENDARIO, "EPL", desde=d0, hasta=d1))
        if r is None or r.calidad is Calidad.SIN_DATOS:
            return {"estado": "sin datos: la fatiga del CRS queda no disponible"}
        ids_liga = {str(l["api_football_id"]) for l in st.ligas.values() if l["api_football_id"]}
        n = 0
        for c in r.datos:
            eid = st.resolver._alias.get((r.proveedor, c.equipo))
            if eid is None:
                continue            # equipo fuera de nuestras ligas: no interesa
            st.conn.execute("""INSERT INTO calendario_equipos (equipo_id, inicio, competicion, es_liga, es_local, fuente)
                               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (equipo_id, inicio) DO NOTHING""",
                            (eid, c.inicio, c.competicion, c.id_competicion_externa in ids_liga, c.es_local, r.proveedor))
            n += 1
        d = d0
        while d <= d1:
            st.conn.execute("""INSERT INTO calendario_cobertura (dia, fuente) VALUES (%s, %s)
                               ON CONFLICT (dia) DO UPDATE SET capturado_en = now()""", (d, r.proveedor))
            d += timedelta(days=1)
        return {"registros": n}

    def _etapa_liquidacion_clv(self, st: Estado) -> dict:
        return liquidacion.liquidar(st.conn, self.cfg, st.ahora)

    def _etapa_reajuste_modelo(self, st: Estado) -> dict:
        ultima = svc_modelo.ultima_version(st.conn, "semanal")
        semanal = ultima is None or (st.ahora - ultima["creado_en"]) >= timedelta(
            days=self.cfg["modelo"]["calibracion_semanal_dias"])
        cuotas_pre = pd.DataFrame(st.conn.execute("""
            SELECT q.partido_id, q.seleccion, q.cuota::float8 AS cuota FROM cuotas q
            JOIN casas_apuestas c ON c.id = q.casa_id
            WHERE q.tipo = 'prepartido' AND c.clave = %s
        """, (self.cfg["mercado"]["casa_referencia"].lower(),)).fetchall(), columns=["partido_id", "seleccion", "cuota"])
        vid, modelo, hp = svc_modelo.reajustar(st.conn, self.cfg, st.ahora, semanal=semanal, cuotas_pre=cuotas_pre)
        st.modelo = (vid, modelo, hp)
        return {"version_id": vid, "tipo": "semanal" if semanal else "diario", "n_partidos": modelo.n_partidos,
                "xi": hp["xi"], "vida_media_dias": round(modelo.vida_media_dias, 1), "w_xg": hp["w_xg"],
                "rho": round(modelo.rho, 4), "calibrado": hp.get("calibrado", False),
                "alpha": hp.get("alpha", {}), "motivo": hp.get("motivo")}

    def _etapa_lesiones(self, st: Estado) -> dict:
        res = {"bajas": 0, "partidos": 0, "sin_datos": 0, "produccion_jugadores": 0}
        prod = {}
        for codigo in self.cfg["contexto"]["ligas_con_ajuste"]:
            if codigo not in self.cfg["ligas_activas"]:
                continue
            liga = st.ligas[codigo]
            temporada = repo.temporada_de(liga, st.ahora)
            r = self._obtener(Capacidad.XG_JUGADORES, Solicitud(Capacidad.XG_JUGADORES, codigo, temporada=temporada))
            for j in (r.datos if r else []):
                eid = st.resolver.resolver(r.proveedor, j.equipo.split(",")[-1].strip(), liga["id"])
                if eid is None:
                    continue
                jid = st.conn.execute("""
                    INSERT INTO jugadores (nombre, equipo_id, understat_id) VALUES (%s, %s, %s)
                    ON CONFLICT (understat_id) DO UPDATE SET equipo_id = EXCLUDED.equipo_id, nombre = EXCLUDED.nombre
                    RETURNING id""", (j.jugador, eid, j.id_externo)).fetchone()["id"]
                st.conn.execute("""
                    INSERT INTO jugador_produccion (jugador_id, equipo_id, temporada, fuente, minutos, xg, xa, partidos, tiros)
                    VALUES (%s, %s, %s, 'understat', %s, %s, %s, %s, %s)
                    ON CONFLICT (jugador_id, equipo_id, temporada, fuente)
                    DO UPDATE SET minutos = EXCLUDED.minutos, xg = EXCLUDED.xg, xa = EXCLUDED.xa,
                                  partidos = COALESCE(EXCLUDED.partidos, jugador_produccion.partidos),
                                  tiros = COALESCE(EXCLUDED.tiros, jugador_produccion.tiros)""",
                                (jid, eid, temporada, j.minutos, j.xg, j.xa, getattr(j, "partidos", None), getattr(j, "tiros", None)))
                prod.setdefault((eid, temporada), []).append((j.jugador, j.minutos, j.xg, j.xa))
                res["produccion_jugadores"] += 1
        c = self.cfg["contexto"]
        for p in repo.partidos_de_dias(st.conn, st.dias()):
            if not p["api_football_id"]:
                continue
            r = self._obtener(Capacidad.LESIONES, Solicitud(Capacidad.LESIONES, p["liga"],
                                                            ids_partido={"api_football": str(p["api_football_id"])}))
            if r is None or r.calidad is Calidad.SIN_DATOS:
                res["sin_datos"] += 1
                continue
            st.conn.execute("UPDATE contexto_lesiones SET vigente = false WHERE partido_id = %s", (p["id"],))
            res["partidos"] += 1
            for b in r.datos:
                eid = st.resolver.resolver(r.proveedor, b.equipo, p["liga_id"])
                if eid not in (p["local_id"], p["visitante_id"]):
                    continue
                part = None
                jugadores = prod.get((eid, p["temporada"]), [])
                total = sum(x[2] + x[3] for x in jugadores)
                nombre = emparejar_jugador(b.jugador, [x[0] for x in jugadores], c["similitud_min_jugador"])
                if nombre and total > 0:
                    x = next(x for x in jugadores if x[0] == nombre)
                    part = (x[2] + x[3]) / total
                st.conn.execute("""
                    INSERT INTO contexto_lesiones (partido_id, equipo_id, jugador_nombre, tipo, motivo, fuente,
                        capturado_en, participacion, base_participacion)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                                (p["id"], eid, b.jugador, b.tipo, b.motivo, r.proveedor, st.ahora, part,
                                 "xg_xa" if part is not None else None))
                res["bajas"] += 1
        return res

    def _etapa_alineaciones(self, st: Estado) -> dict:
        res = {"partidos": 0, "jugadores": 0}
        for p in repo.partidos_de_dias(st.conn, st.dias()):
            if not p["api_football_id"] or p["inicio"] - st.ahora > timedelta(hours=2):
                continue
            r = self._obtener(Capacidad.ALINEACIONES, Solicitud(Capacidad.ALINEACIONES, p["liga"],
                                                                ids_partido={"api_football": str(p["api_football_id"])}))
            if r is None or not r.datos:
                continue
            res["partidos"] += 1
            for a in r.datos:
                eid = st.resolver.resolver(r.proveedor, a.equipo, p["liga_id"])
                if eid not in (p["local_id"], p["visitante_id"]):
                    continue
                st.conn.execute("""INSERT INTO alineaciones (partido_id, equipo_id, jugador_nombre, titular, confirmada,
                                   fuente, capturada_en) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                (p["id"], eid, a.jugador, a.titular, a.confirmada, r.proveedor, st.ahora))
                res["jugadores"] += 1
        res["nota"] = "las alineaciones se guardan y muestran; aún no modifican λ (fase posterior)"
        return res

    def _etapa_cuotas(self, st: Estado, *, tipo: str = "actual", cap: Capacidad = Capacidad.CUOTAS,
                      partidos: list[dict] | None = None) -> dict:
        res = {"cuotas": 0, "sin_partido": 0, "avisos": [], "calidad": {}}
        hoy = st.hoy()
        ligas_obj = {p["liga"] for p in (partidos if partidos is not None else repo.partidos_de_dias(st.conn, st.dias()))
                     if not p.get("solo_mercado")}
        if not ligas_obj:
            res["avisos"].append("Sin partidos programados en los próximos días: no se consultaron cuotas "
                                 "(así no se gastan créditos).")
        for codigo in sorted(ligas_obj):
            liga = st.ligas[codigo]
            ids = ",".join(p["odds_api_id"] for p in partidos or [] if p.get("odds_api_id")) or None
            sol = Solicitud(cap, codigo, temporada=repo.temporada_de(liga, st.ahora), desde=hoy, hasta=hoy + timedelta(days=max(self.cfg["recalculo"]["dias"])),
                            ids_partido={"odds_api": ids} if ids else {}, para_cierre=cap is Capacidad.CUOTAS_CIERRE)
            r = self._obtener(cap, sol)
            if r is None:
                continue
            res["calidad"][codigo] = r.calidad.value
            res["avisos"] += r.avisos
            grupos: dict = defaultdict(list)
            for q in r.datos:
                if not q.local and q.id_partido_externo and r.proveedor == "api_football":
                    p = st.conn.execute("SELECT * FROM partidos WHERE api_football_id = %s",
                                        (int(q.id_partido_externo),)).fetchone()
                else:
                    lid = st.resolver.resolver(r.proveedor, q.local, liga["id"])
                    vid = st.resolver.resolver(r.proveedor, q.visitante, liga["id"])
                    p = repo.buscar_partido(st.conn, liga["id"], lid, vid, q.inicio or st.ahora) if lid and vid else None
                if p is None:
                    res["sin_partido"] += 1
                    continue
                if q.id_partido_externo and r.proveedor == "odds_api" and not p["odds_api_id"]:
                    st.conn.execute("""UPDATE partidos SET odds_api_id = %s WHERE id = %s
                                       AND NOT EXISTS (SELECT 1 FROM partidos WHERE odds_api_id = %s)""",
                                    (q.id_partido_externo, p["id"], q.id_partido_externo))
                grupos[(p["id"], q.casa, q.capturada_en)].append((q.seleccion, q.cuota))
            for (pid, casa, capt), filas in grupos.items():
                repo.insertar_cuotas(st.conn, pid, repo.casa_id(st.conn, casa), filas, tipo, r.proveedor, capt)
                res["cuotas"] += len(filas)
        if partidos is None and tipo == "actual" and cap is Capacidad.CUOTAS:
            res["mercado"] = self._competiciones_mercado(st)
        return res

    def _competiciones_mercado(self, st: Estado) -> dict:
        """Competiciones SIN modelo propio (Champions, selecciones…): partidos y cuotas desde The Odds API.
        Descubrimiento y calendario son gratis; solo se piden cuotas (2 créditos) de las que tienen partidos."""
        cm = self.cfg.get("competiciones_mercado") or {}
        prov = self._proveedor_mercado()
        res = {"competiciones": 0, "partidos": 0, "cuotas": 0, "creditos": 0, "avisos": []}
        if not cm.get("activo") or prov is None:
            return res
        try:
            deportes = prov.deportes_futbol()
        except Exception as e:                                   # noqa: BLE001 — opcional: no tumba la etapa
            res["avisos"].append(f"Competiciones extra: no se pudo listar deportes ({type(e).__name__}).")
            return res
        modeladas = {v for v in getattr(prov, "ligas_claves", {}).values()}   # tras listar: incluye las descubiertas
        elegidos = elegir_competiciones(deportes, modeladas, cm)          # orden de prioridad = orden de los patrones (Champions primero)
        hasta = st.hoy() + timedelta(days=max(self.cfg["recalculo"]["dias"]))
        costo_por = len(getattr(prov, "regiones", [1])) * len(getattr(prov, "mercados", [1, 2]))
        tope = int(cm.get("max_creditos_por_actualizacion", 16))
        cache: dict = {}
        for d in elegidos:
            try:
                if not prov.eventos_deporte(d["key"], hasta):
                    continue                                      # sin partidos en la ventana: no gasta créditos
                if res["creditos"] + costo_por > tope:
                    res["avisos"].append(f"Tope de {tope} créditos: faltan {d.get('title', d['key'])} y siguientes.")
                    break
                cuotas = prov.cuotas_deporte(d["key"], hasta)
            except Exception as e:                                # noqa: BLE001 — una competición no tumba las demás
                res["avisos"].append(f"{d.get('title', d['key'])}: {type(e).__name__}")
                continue
            res["creditos"] += costo_por
            liga = repo.asegurar_liga_mercado(st.conn, d["key"], d.get("title") or d["key"])
            res["competiciones"] += 1
            vistos: set[int] = set()
            grupos: dict = defaultdict(list)
            for q in cuotas:
                lid = repo.equipo_mercado(st.conn, liga["id"], q.local, cache)
                vid = repo.equipo_mercado(st.conn, liga["id"], q.visitante, cache)
                pid, accion = repo.upsert_partido(st.conn, liga, PartidoExterno(
                    "odds_api", liga["codigo"], q.inicio, q.local, q.visitante, "programado", id_externo=q.id_partido_externo),
                    lid, vid)
                if pid not in vistos:
                    vistos.add(pid)
                    res["partidos"] += 1
                grupos[(pid, q.casa, q.capturada_en)].append((q.seleccion, q.cuota))
            for (pid, casa, capt), filas in grupos.items():
                repo.insertar_cuotas(st.conn, pid, repo.casa_id(st.conn, casa), filas, "actual", "odds_api", capt)
                res["cuotas"] += len(filas)
        res["resultados"] = self._resultados_selecciones(st, prov, deportes, cm, cache)
        return res

    def _resultados_selecciones(self, st: Estado, prov, deportes: list[dict], cm: dict, cache: dict) -> int:
        """Resultados de los últimos 3 días de las competiciones de SELECCIONES (2 créditos c/u, con su propio tope):
        sin esto los partidos quedaban «programado» para siempre y el modelo de selecciones no veía un 7-0 o un 5-0."""
        from golmodel.servicios.pronostico_selecciones import PATRONES_SELECCIONES
        tope = int(cm.get("max_creditos_resultados", 6))
        gastado, n = 0, 0
        for d in deportes:
            k = str(d["key"]).lower()
            if not any(x in k for x in PATRONES_SELECCIONES) or "women" in k or gastado + 2 > tope:
                continue
            try:
                partidos = prov.resultados_deporte(d["key"], 3)
            except Exception:                                     # noqa: BLE001 — opcional
                continue
            gastado += 2
            if not partidos:
                continue
            liga = repo.asegurar_liga_mercado(st.conn, d["key"], d.get("title") or d["key"])
            for p in partidos:
                lid = repo.equipo_mercado(st.conn, liga["id"], p.local, cache)
                vid = repo.equipo_mercado(st.conn, liga["id"], p.visitante, cache)
                _, accion = repo.upsert_partido(st.conn, liga, p, lid, vid)
                n += accion in ("resultado", "nuevo")
        return n

    def _etapa_recalculo_modulos(self, st: Estado) -> dict:
        if st.modelo is None:
            cargado = svc_modelo.cargar_modelo(st.conn)
            if cargado is None:
                raise RuntimeError("No hay versión del modelo: ejecuta 'Actualizar datos' primero")
            st.modelo = cargado
        vid, modelo, hp = st.modelo
        edad = st.ahora - modelo.datos_hasta
        r = prediccion.recalcular(st.conn, self.cfg, modelo, vid, hp, st.dias(), st.ahora)
        if edad > timedelta(days=self.cfg["frescura"]["modelo_max_dias"]):
            r["avisos"].append(f"Modelo con {edad.days} días sin reajuste")
        return r


# ------------------------------------------------------------------ histórico

def cargar_historico(conn, liga_codigo: str, partidos: pd.DataFrame, cuotas: pd.DataFrame | None = None,
                     fuente: str = "football_data_csv") -> dict:
    """Carga partidos (y cuotas) de football-data como fuente canónica de equipos."""
    liga = repo.ligas(conn)[liga_codigo]
    n = {"partidos": 0, "cuotas": 0}
    ids = {}
    from golmodel.ingesta.tipos import PartidoExterno
    for r in partidos.itertuples(index=False):
        inicio = r.inicio.to_pydatetime() if hasattr(r.inicio, "to_pydatetime") else r.inicio
        temp = repo.temporada_de(liga, inicio)
        lid = repo.equipo_canonico(conn, r.local, liga["pais"], liga["id"], temp, fuente)
        vid = repo.equipo_canonico(conn, r.visitante, liga["pais"], liga["id"], temp, fuente)
        fin = pd.notna(r.goles_local)
        ext = PartidoExterno(fuente, liga_codigo, inicio, r.local, r.visitante,
                             "finalizado" if fin else "programado",
                             goles_local=int(r.goles_local) if fin else None,
                             goles_visitante=int(r.goles_visitante) if fin else None)
        pid, _ = repo.upsert_partido(conn, liga, ext, lid, vid, equivalente=True)
        if hasattr(r, "xg_local") and pd.notna(getattr(r, "xg_local")):
            repo.set_xg(conn, pid, float(r.xg_local), float(r.xg_visitante), "understat")
        ids[(r.local, r.visitante, inicio)] = pid
        n["partidos"] += 1
    repo.marcar_ascendidos(conn, liga["id"])
    if cuotas is not None and not cuotas.empty:
        for (loc, vis, ini, casa, tipo, capt), d in cuotas.groupby(
                ["local", "visitante", "inicio", "casa", "tipo", "capturada_en"]):
            pid = ids.get((loc, vis, ini.to_pydatetime() if hasattr(ini, "to_pydatetime") else ini))
            if pid is None:
                continue
            repo.insertar_cuotas(conn, pid, repo.casa_id(conn, casa), list(zip(d["seleccion"], d["cuota"])),
                                 tipo, fuente, capt)
            n["cuotas"] += len(d)
    return n
