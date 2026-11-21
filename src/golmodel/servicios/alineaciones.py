"""Alineaciones y ausencias de titulares (fuente: ESPN). Responde: «¿están los principales de cada equipo?».

Qué hay y qué NO hay (medido el 2026-11-20): ESPN no publica lesiones de fútbol, pero sí la alineación oficial (~1 h antes) y las
estadísticas de temporada de cada jugador. Por eso un «principal» se define por uso (titularidades y goles+asistencias), y una
ausencia es «no está en la convocatoria» o «está en la banca»: el motivo (lesión, sanción, descanso) no se conoce y no se inventa.

Cómo se usa: avisa en el panel y en los boletos. NO mueve las probabilidades: en el 1X2 manda el mercado (α = 0) y el mercado ya
incorpora la alineación una vez publicada; un ajuste propio sin calibrar solo añadiría ruido. La señal sirve para NO apostar
a ciegas cuando falta un titular clave, que es justo lo que cambia la decisión.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from golmodel.ingesta.normalizacion import ResolverEquipos
from golmodel.ingesta.proveedores import espn as E

log = logging.getLogger("golmodel.alineaciones")
_INTENTOS: dict[int, datetime] = {}      # equipo_id → último intento de bajar su plantilla (en memoria del proceso)

UMBRAL_PRINCIPAL = 0.60          # titularidades / partidos del equipo para ser «principal»
MIN_PARTIDOS = 4                 # con menos partidos jugados no se juzga a nadie (inicio de temporada, plantilla nueva)
PESO_ESTRELLA = 0.25             # un jugador que aporta ≥ 25% de los goles+asistencias del equipo es clave aunque falte uno solo
MIN_ALINEACION_TITULARES = 9


def _candidatos(conn, ahora: datetime, horizonte_h: float) -> list[dict]:
    return conn.execute("""
        SELECT p.id, p.inicio, p.local_id, p.visitante_id, p.liga_id, p.espn_id, l.codigo, l.odds_api_sport_key, l.solo_mercado
        FROM partidos p JOIN ligas l ON l.id = p.liga_id
        WHERE l.activa AND p.estado = 'programado' AND p.inicio > %s AND p.inicio < %s ORDER BY p.inicio""",
                        (ahora - timedelta(minutes=30), ahora + timedelta(hours=horizonte_h))).fetchall()


def _resolver_para(conn, cache: dict, solo_mercado: bool) -> ResolverEquipos:
    k = "mk" if solo_mercado else "liga"
    if k not in cache:
        # En competiciones solo-mercado nunca se acepta un parecido «por similitud»: solo nombre idéntico o tabla explícita.
        cache[k] = ResolverEquipos(conn, similitud_min=1.01 if solo_mercado else 0.90)
    return cache[k]


def sincronizar(conn, fuente, ahora: datetime, *, ventana_min: float = 100, horizonte_h: float = 36,
                refresco_plantilla_h: float = 20) -> dict:
    """Empareja partidos con eventos de ESPN, guarda las alineaciones ya publicadas (partidos dentro de `ventana_min`) y refresca las
    plantillas con titularidades. Un fallo de ESPN (red, formato) no tumba nada: se cuenta y se sigue."""
    res = {"partidos": 0, "emparejados": 0, "alineaciones": 0, "plantillas": 0, "sin_emparejar": [], "errores": 0}
    cands = _candidatos(conn, ahora, horizonte_h)
    res["partidos"] = len(cands)
    resolvers: dict = {}
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in cands:
        liga = E.liga_espn(p["codigo"], p["odds_api_sport_key"])
        if liga:
            grupos[(liga, p["inicio"].strftime("%Y%m%d"))].append(p)
    equipos_espn: dict[int, tuple[str, str]] = {}                       # equipo_id nuestro → (liga espn, id de equipo en espn)
    emparejados: list[tuple[dict, E.EventoESPN, str]] = []
    for (liga, dia), partidos in grupos.items():
        try:
            eventos = fuente.eventos(liga, partidos[0]["inicio"])
        except Exception:                                                # noqa: BLE001
            res["errores"] += 1
            continue
        for p in partidos:
            r = _resolver_para(conn, resolvers, bool(p["solo_mercado"]))
            try:
                with conn.transaction():                                 # un fallo de SQL aquí no deja rota la transacción de la etapa
                    ev = _evento_de(conn, r, p, eventos)
            except Exception:                                            # noqa: BLE001
                log.warning("alineaciones: no se pudo emparejar el partido %s", p["id"], exc_info=True)
                res["errores"] += 1
                continue
            if ev is None:
                res["sin_emparejar"].append(f"{p['codigo']} #{p['id']}")
                continue
            res["emparejados"] += 1
            equipos_espn[p["local_id"]] = (liga, ev.local_id)
            equipos_espn[p["visitante_id"]] = (liga, ev.visitante_id)
            emparejados.append((p, ev, liga))
            if p["espn_id"] != ev.id:
                conn.execute("UPDATE partidos SET espn_id = %s WHERE id = %s", (ev.id, p["id"]))
    # alineaciones publicadas (solo dentro de la ventana previa al partido y mientras no haya una guardada)
    for p, ev, liga in emparejados:
        if p["inicio"] - ahora > timedelta(minutes=ventana_min):
            continue
        if conn.execute("SELECT 1 FROM alineaciones WHERE partido_id = %s AND fuente = 'espn' AND confirmada LIMIT 1", (p["id"],)).fetchone():
            continue
        try:
            lados = fuente.alineacion(liga, ev.id)
        except Exception:                                                # noqa: BLE001
            res["errores"] += 1
            continue
        if not lados:
            continue                                                     # aún no la publican
        with conn.transaction():
            for clave_lado, eq in (("local", p["local_id"]), ("visitante", p["visitante_id"])):
                lado = lados[clave_lado]
                for j in lado.jugadores:
                    conn.execute("""INSERT INTO alineaciones (partido_id, equipo_id, jugador_nombre, titular, confirmada, fuente, capturada_en,
                                                              jugador_externo, posicion, formacion)
                                    VALUES (%s, %s, %s, %s, true, 'espn', %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                                 (p["id"], eq, j.nombre, j.titular, ahora, j.id, j.posicion or None, lado.formacion))
        res["alineaciones"] += 1
    # plantillas con titularidades (una vez al día por equipo)
    for eq, (liga, tid) in equipos_espn.items():
        vigente = conn.execute("SELECT 1 FROM plantilla_jugadores WHERE equipo_id = %s AND actualizado_en > %s LIMIT 1",
                               (eq, ahora - timedelta(hours=refresco_plantilla_h))).fetchone()
        if vigente or ahora - _INTENTOS.get(eq, datetime.min.replace(tzinfo=ahora.tzinfo)) < timedelta(hours=3):
            continue                                                     # vigente, o ya se intentó hace poco (no se insiste cada 10 min)
        _INTENTOS[eq] = ahora
        try:
            jugadores = fuente.plantilla(liga, tid)
        except Exception:                                                # noqa: BLE001
            res["errores"] += 1
            continue
        if not jugadores:
            continue                                                     # ESPN sin plantilla de ese equipo: no cuenta como refrescada
        with conn.transaction():
            for j in jugadores:
                conn.execute("""INSERT INTO plantilla_jugadores (equipo_id, jugador_externo, nombre, posicion, apariciones, titularidades,
                                                                  goles, asistencias, tiros_arco, rojas, amarillas, estado, actualizado_en)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (equipo_id, jugador_externo) DO UPDATE SET nombre = EXCLUDED.nombre,
                                  posicion = EXCLUDED.posicion, apariciones = EXCLUDED.apariciones,
                                  titularidades = EXCLUDED.titularidades, goles = EXCLUDED.goles, asistencias = EXCLUDED.asistencias,
                                  tiros_arco = EXCLUDED.tiros_arco, rojas = EXCLUDED.rojas, amarillas = EXCLUDED.amarillas,
                                  estado = EXCLUDED.estado, actualizado_en = EXCLUDED.actualizado_en""",
                             (eq, j.id, j.nombre, j.posicion or None, j.apariciones, j.titularidades, j.goles, j.asistencias,
                              j.tiros_arco, j.rojas, j.amarillas, j.estado, ahora))
        res["plantillas"] += 1
    return res


def _evento_de(conn, r: ResolverEquipos, p: dict, eventos: list[E.EventoESPN]) -> E.EventoESPN | None:
    """Evento de ESPN que corresponde a nuestro partido: mismos dos equipos y salida a menos de 6 h de diferencia."""
    if p["espn_id"]:
        for ev in eventos:
            if ev.id == p["espn_id"]:
                return ev
    for ev in eventos:
        if abs((ev.inicio - p["inicio"]).total_seconds()) > 6 * 3600:
            continue
        try:
            lid = r.resolver("espn", ev.local, p["liga_id"])
            vid = r.resolver("espn", ev.visitante, p["liga_id"])
        except Exception:                                                # noqa: BLE001
            continue
        if lid == p["local_id"] and vid == p["visitante_id"]:
            return ev
    return None


# ------------------------------------------------------------------------------------------------------------- análisis
def principales(conn, equipo_id: int) -> list[dict]:
    """Jugadores que el equipo usa de verdad: ≥ 60% de titularidades sobre los partidos jugados por el equipo."""
    filas = conn.execute("""SELECT jugador_externo, nombre, posicion, apariciones, titularidades, goles, asistencias
                            FROM plantilla_jugadores WHERE equipo_id = %s""", (equipo_id,)).fetchall()
    if not filas:
        return []
    partidos = max(f["apariciones"] for f in filas)
    if partidos < MIN_PARTIDOS:
        return []
    total = sum((f["goles"] or 0) + (f["asistencias"] or 0) for f in filas) or 1
    out = []
    for f in filas:
        contrib = (f["goles"] or 0) + (f["asistencias"] or 0)
        uso = f["titularidades"] / partidos
        if uso >= UMBRAL_PRINCIPAL:
            out.append({"id": f["jugador_externo"], "nombre": f["nombre"], "posicion": f["posicion"] or "", "uso": round(uso, 2),
                        "aporte": round(contrib / total, 2), "goles": f["goles"] or 0, "asistencias": f["asistencias"] or 0})
    return sorted(out, key=lambda x: (-x["aporte"], -x["uso"]))


def analizar(conn, partido_id: int) -> dict:
    """{'estado': 'sin_datos'|'pendiente'|'publicada', 'local': {...}, 'visitante': {...}, 'nivel': 'bajo'|'medio'|'alto', 'aviso': texto}."""
    p = conn.execute("SELECT local_id, visitante_id, espn_id FROM partidos WHERE id = %s", (partido_id,)).fetchone()
    if p is None or not p["espn_id"]:
        return {"estado": "sin_datos"}
    filas = conn.execute("""SELECT equipo_id, jugador_externo, jugador_nombre, titular, posicion, formacion, capturada_en
                            FROM alineaciones WHERE partido_id = %s AND fuente = 'espn' AND confirmada ORDER BY capturada_en DESC""",
                         (partido_id,)).fetchall()
    if not filas:
        return {"estado": "pendiente"}
    out: dict = {"estado": "publicada", "capturada_en": filas[0]["capturada_en"].isoformat()}
    niveles = []
    for lado, eq in (("local", p["local_id"]), ("visitante", p["visitante_id"])):
        mias = [f for f in filas if f["equipo_id"] == eq]
        ult = mias[0]["capturada_en"] if mias else None
        mias = [f for f in mias if f["capturada_en"] == ult]
        titulares = {f["jugador_externo"] for f in mias if f["titular"]}
        banca = {f["jugador_externo"] for f in mias if not f["titular"]}
        fuera, en_banca = [], []
        for j in principales(conn, eq):
            if j["id"] in titulares:
                continue
            (en_banca if j["id"] in banca else fuera).append({**j, "motivo": "en la banca" if j["id"] in banca else "fuera de la convocatoria"})
        nivel = nivel_de(fuera, en_banca)
        niveles.append(nivel)
        out[lado] = {"formacion": (mias[0]["formacion"] if mias else None), "titulares": len(titulares), "fuera": fuera,
                     "en_banca": en_banca, "nivel": nivel}
    orden = {"bajo": 0, "medio": 1, "alto": 2}
    out["nivel"] = max(niveles, key=orden.get) if niveles else "bajo"
    out["aviso"] = _aviso(out)
    return out


def nivel_de(fuera: list[dict], en_banca: list[dict]) -> str:
    """Fuera de la convocatoria pesa el doble que en la banca (el de la banca suele entrar). Alto: el arquero habitual no es titular,
    falta el que más aporta (≥ 25% de goles+asistencias) o la suma llega a 2 titulares; medio: al menos medio punto."""
    arquero = any(j["posicion"] == "G" for j in fuera + en_banca)
    estrella = any(j["aporte"] >= PESO_ESTRELLA for j in fuera)
    puntos = len(fuera) + 0.5 * len(en_banca)
    return "alto" if (puntos >= 2 or arquero or estrella) else ("medio" if puntos >= 0.5 else "bajo")


def _aviso(a: dict) -> str:
    partes = []
    for lado in ("local", "visitante"):
        d = a.get(lado) or {}
        malos = [f"{j['nombre']} ({j['motivo']})" for j in (d.get("fuera") or []) + (d.get("en_banca") or [])]
        if malos:
            partes.append(f"{'Local' if lado == 'local' else 'Visitante'}: " + ", ".join(malos))
    return ("Faltan titulares habituales — " + " · ".join(partes)) if partes else "Alineación publicada: están los titulares habituales."



REBAJA_CONFIANZA = {"medio": 0.5, "alto": 1.0}


def aplicar_a_info(info: dict, al: dict) -> dict:
    """Pone la alineación en el partido del panel y, si faltan titulares habituales, lo dice en las razones y baja la confianza
    (los boletos exigen confianza mínima: un partido con un titular clave fuera deja de entrar solo). No toca las probabilidades."""
    if not al or al.get("estado") == "sin_datos":
        return info
    info["alineacion"] = al
    if al.get("estado") == "publicada" and al.get("nivel") in REBAJA_CONFIANZA:
        info["razones"] = [*(info.get("razones") or []), al["aviso"]]
        if info.get("confianza") is not None:
            info["confianza"] = max(0.0, round(float(info["confianza"]) - REBAJA_CONFIANZA[al["nivel"]], 1))
        info["alerta_alineacion"] = al["nivel"]
    return info
