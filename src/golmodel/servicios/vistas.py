"""Lecturas para la API y la UI (no escriben nada)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from golmodel.modelos import crs as crs_mod
from golmodel.modelos.matriz import Matriz
from golmodel.modelos.mercados import PUSH
from golmodel.motor.valor import clasificar, cuota_justa, cuota_justa_con_push, ev, ev_con_push, umbral_efectivo
from golmodel.servicios.prediccion import vigente
from golmodel import repositorio as repo

ETIQUETA_FUENTE = {"resultados_xg": "Resultados", "reajuste_modelo": "Modelo", "lesiones": "Lesiones", "cuotas": "Cuotas"}


def _matriz(arr: list[float], lh: float, la: float, rho: float) -> Matriz:
    n = int(round(math.sqrt(len(arr))))
    return Matriz(np.array(arr).reshape(n, n), lh, la, rho, False, 0.0)


def limpiar_huerfanas(conn, gracia_min: int = 2) -> int:
    """Marca como interrumpidas las ejecuciones 'en_curso' que ya nadie está corriendo.

    Si el contenedor de la API se reinicia a media actualización, el hilo muere y el registro
    queda 'en_curso' para siempre (y la UI bloquea los botones). Una ejecución viva siempre
    tiene tomado el advisory lock: si el lock está libre, la ejecución es huérfana.
    """
    from golmodel.db import CLAVE_BLOQUEO
    libre = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (CLAVE_BLOQUEO,)).fetchone()["ok"]
    if not libre:
        return 0
    conn.execute("SELECT pg_advisory_unlock(%s)", (CLAVE_BLOQUEO,))
    ids = [r["id"] for r in conn.execute(f"""
        UPDATE ejecuciones SET estado = 'error', terminada_en = now()
        WHERE estado = 'en_curso' AND iniciada_en < now() - interval '{int(gracia_min)} minutes'
        RETURNING id""")]
    if ids:
        conn.execute("""UPDATE ejecucion_etapas SET estado = 'error', terminada_en = now(),
                        error = 'interrumpida: la API se reinició durante la actualización'
                        WHERE ejecucion_id = ANY(%s) AND estado IN ('pendiente','en_curso')""", (ids,))
    return len(ids)


def _ultima_ejecucion(conn) -> dict | None:
    e = conn.execute("SELECT * FROM ejecuciones WHERE estado <> 'en_curso' ORDER BY id DESC LIMIT 1").fetchone()
    if e:
        e["etapas"] = conn.execute("""SELECT etapa, estado, error, resumen FROM ejecucion_etapas
                                      WHERE ejecucion_id = %s ORDER BY orden""", (e["id"],)).fetchall()
    return e


def estado(conn, cfg: dict, ahora: datetime, interruptores: dict | None = None) -> dict:
    zona = ZoneInfo(cfg["zona_horaria"])
    interrumpidas = limpiar_huerfanas(conn)
    fres = {r["etapa"]: r["ultima_ok"] for r in conn.execute("SELECT * FROM v_frescura")}
    frescura = {}
    for etapa, etiqueta in ETIQUETA_FUENTE.items():
        t = fres.get(etapa)
        frescura[etiqueta] = None if t is None else {"utc": t.isoformat(), "local": t.astimezone(zona).strftime("%d/%m %H:%M"),
                                                    "minutos": max(0, int((ahora - t).total_seconds() // 60))}
    cuotas_t = fres.get("cuotas")
    modelo_t = fres.get("reajuste_modelo")
    alertas = []
    if cuotas_t is None or (ahora - cuotas_t) > timedelta(minutes=cfg["frescura"]["cuotas_max_min"]):
        alertas.append("Cuotas vencidas: los picks se marcan 'Cuota no vigente' y no se generan parlays.")
    if modelo_t is None or (ahora - modelo_t) > timedelta(days=cfg["frescura"]["modelo_max_dias"]):
        alertas.append(f"Modelo con más de {cfg['frescura']['modelo_max_dias']} días sin reajuste.")
    f = cfg["fuentes"]
    consumo = repo.consumo_actual(conn)
    limites = {"api_football": ("dia", f["api_football"]["limite_diario"]),
               "odds_api": ("mes", f["odds_api"]["creditos_mensuales"])}
    cons = {p: {"periodo": per, "limite": lim,
                "usado": int((consumo.get(p) or {}).get("hoy_utc" if per == "dia" else "mes_utc") or 0),
                "restante_reportado": (consumo.get(p) or {}).get("restante_reportado")}
            for p, (per, lim) in limites.items()}
    en_curso = conn.execute("SELECT id, tipo, iniciada_en FROM ejecuciones WHERE estado = 'en_curso' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    v = conn.execute("SELECT id, creado_en, tipo_ajuste, hiperparametros FROM versiones_modelo "
                     "WHERE origen = 'produccion' ORDER BY creado_en DESC LIMIT 1").fetchone()
    return {
        "ahora_local": ahora.astimezone(zona).strftime("%d/%m/%Y %H:%M"), "frescura": frescura, "alertas": alertas,
        "consumo_api": cons, "interruptores": interruptores or {}, "ejecucion_en_curso": en_curso,
        "alias_pendientes": conn.execute("SELECT count(*) AS n FROM alias_pendientes WHERE NOT resuelto").fetchone()["n"],
        "conflictos_abiertos": conn.execute("SELECT count(*) AS n FROM conflictos_datos WHERE NOT resuelto").fetchone()["n"],
        "modelo": None if v is None else {
            "version_id": v["id"], "tipo": v["tipo_ajuste"], "creado": v["creado_en"].astimezone(zona).strftime("%d/%m %H:%M"),
            "xi": v["hiperparametros"].get("xi"), "w_xg": v["hiperparametros"].get("w_xg"),
            "calibrado": v["hiperparametros"].get("calibrado", False)},
        "crs_modo_ajuste": cfg["crs"]["ajuste_favorito"]["modo"],
        "ejecuciones_interrumpidas": interrumpidas,
        "ultima_ejecucion": _ultima_ejecucion(conn),
        # Ligas activas sin partidos jugados cargados: sus nombres no se pueden resolver todavía
        "ligas_sin_historico": [r["codigo"] for r in conn.execute("""
            SELECT l.codigo FROM ligas l WHERE l.activa AND l.codigo = ANY(%s) AND NOT EXISTS (
                SELECT 1 FROM partidos p WHERE p.liga_id = l.id AND p.estado = 'finalizado')
            ORDER BY l.codigo""", (cfg["ligas_activas"],))],
        # Cobertura de xG en ligas que lo usan: si cae, el modelo usa solo goles en esos partidos.
        "cobertura_xg": conn.execute("""
            SELECT l.codigo AS liga, count(*) AS partidos, count(p.xg_local) AS con_xg,
                   max(p.inicio) FILTER (WHERE p.xg_local IS NOT NULL) AS ultimo_con_xg
            FROM partidos p JOIN ligas l ON l.id = p.liga_id
            WHERE l.usa_xg AND p.estado = 'finalizado'
            GROUP BY l.codigo ORDER BY l.codigo""").fetchall(),
    }


def ejecucion(conn, eid: int) -> dict | None:
    e = conn.execute("SELECT * FROM ejecuciones WHERE id = %s", (eid,)).fetchone()
    if e is None:
        return None
    e["etapas"] = conn.execute("""SELECT orden, etapa, estado, progreso, iniciada_en, terminada_en, error, resumen
                                  FROM ejecucion_etapas WHERE ejecucion_id = %s ORDER BY orden""", (eid,)).fetchall()
    return e


def dia(conn, cfg: dict, d: date, ahora: datetime) -> list[dict]:
    """Módulo 1: última predicción de cada partido del día con su tabla de valor."""
    zona = ZoneInfo(cfg["zona_horaria"])
    salida = []
    for p in conn.execute("""
        SELECT p.*, l.codigo AS liga, el.nombre AS local, ev.nombre AS visitante
        FROM partidos p JOIN ligas l ON l.id = p.liga_id JOIN equipos el ON el.id = p.local_id
        JOIN equipos ev ON ev.id = p.visitante_id WHERE dia_mx(p.inicio) = %s ORDER BY p.inicio
    """, (d,)).fetchall():
        pr = conn.execute("SELECT * FROM predicciones WHERE partido_id = %s AND origen = 'produccion' "
                          "ORDER BY creada_en DESC LIMIT 1", (p["id"],)).fetchone()
        item = {"partido_id": p["id"], "liga": p["liga"], "local": p["local"], "visitante": p["visitante"],
                "inicio_local": p["inicio"].astimezone(zona).strftime("%H:%M"), "estado": p["estado"],
                "marcador": None if p["goles_local"] is None else f"{p['goles_local']}-{p['goles_visitante']}",
                "prediccion": None, "mercados": [], "picks": []}
        if pr:
            item["prediccion"] = {
                "id": pr["id"], "creada": pr["creada_en"].astimezone(zona).strftime("%d/%m %H:%M"),
                "lambda_local": round(pr["lambda_local"], 3), "lambda_visitante": round(pr["lambda_visitante"], 3),
                "ajuste_local": pr["ajuste_local"], "ajuste_visitante": pr["ajuste_visitante"],
                "bajas": (pr["cr_scores"] or {}).get("_bajas"),
                "crs": {"total": pr["crs_total"], "alerta": pr["crs_alerta"], "completo": pr["crs_completo"],
                        "favorito": pr["crs_favorito"], "veto": pr["crs_veto"],
                        "vetadas": pr["crs_selecciones_vetadas"], "modo_ajuste": pr["crs_modo_ajuste"],
                        "componentes": {k: v for k, v in (pr["cr_scores"] or {}).items() if not k.startswith("_")}},
            }
            if pr["crs_veto"] and pr["matriz_crs"]:
                m = _matriz(pr["matriz"], pr["lambda_local"], pr["lambda_visitante"], pr["rho"])
                item["prediccion"]["sugerencia_handicap"] = crs_mod.sugerencia_handicap(m, pr["crs_favorito"])
            cuotas = repo.cuotas_vigentes(conn, p["id"])
            ejec = {}
            if not cuotas.empty:
                for casa in (cfg["mercado"]["casa_ejecucion"], "manual"):
                    for r in cuotas[cuotas["casa"] == casa].itertuples():
                        ejec[r.seleccion] = (float(r.cuota), r.capturada_en, casa)
            for s in conn.execute("SELECT * FROM predicciones_seleccion WHERE prediccion_id = %s ORDER BY seleccion",
                                  (pr["id"],)).fetchall():
                q = ejec.get(s["seleccion"])
                fila = {"seleccion": s["seleccion"], "p_modelo": s["p_modelo"], "p_mercado": s["p_mercado"],
                        "p_adj": s["p_adj"], "p_push": s["p_push"], "p_modelo_crs": s["p_modelo_crs"],
                        "alpha": s["alpha"], "alpha_calibrado": s["alpha_calibrado"],
                        "filtrado_crs": pr["crs_veto"] and s["seleccion"] in pr["crs_selecciones_vetadas"]}
                p_ref = s["p_adj"] if s["p_adj"] is not None else s["p_modelo"]
                pierde = 1 - p_ref - s["p_push"]
                fila["cuota_justa"] = cuota_justa_con_push(p_ref, pierde) if s["seleccion"] in PUSH else cuota_justa(p_ref)
                if q:
                    vig = vigente(q[1], p["inicio"], ahora, cfg)
                    fila.update(cuota_casa=q[0], casa=q[2], cuota_vigente=vig,
                                cuota_hace_min=int((ahora - q[1]).total_seconds() // 60))
                    if s["p_adj"] is not None:
                        e = ev_con_push(s["p_adj"], pierde, q[0]) if s["seleccion"] in PUSH else ev(s["p_adj"], q[0])
                        # Mismo umbral que usa el motor para crear picks (vista y decisión coinciden)
                        u = umbral_efectivo(cfg, alpha_calibrado=s["alpha_calibrado"] and not s["seleccion"].startswith("DC:"),
                                            referencia_sustituta=s["p_mercado_consenso"])
                        fila.update(ev=e, umbral=u.valor, clasificacion=clasificar(e, u) if vig else "cuota_no_vigente")
                item["mercados"].append(fila)
            # Datos para las gráficas: 1X2 modelo vs mercado, mejor oportunidad y marcadores
            por_sel = {m["seleccion"]: m for m in item["mercados"]}
            item["p1x2"] = {"modelo": [por_sel.get(k, {}).get("p_modelo") for k in ("1X2:1", "1X2:X", "1X2:2")],
                            "mercado": [por_sel.get(k, {}).get("p_mercado") for k in ("1X2:1", "1X2:X", "1X2:2")],
                            "ajustada": [por_sel.get(k, {}).get("p_adj") for k in ("1X2:1", "1X2:X", "1X2:2")]}
            con_ev = [m for m in item["mercados"] if m.get("ev") is not None and m.get("cuota_vigente")
                      and not m["filtrado_crs"]]
            item["mejor"] = max(con_ev, key=lambda m: m["ev"]) if con_ev else None
            mat = np.array(pr["matriz"]).reshape(11, 11)
            item["marcadores"] = [[float(mat[i, j]) for j in range(6)] for i in range(6)]
        item["picks"] = conn.execute("""
            SELECT p.id, p.seleccion, p.cuota_sugerida, p.ev, p.stake_u, p.creado_en, p.crs_total, p.crs_alerta,
                   (SELECT tipo FROM pick_eventos e WHERE e.pick_id = p.id ORDER BY creado_en DESC LIMIT 1) AS estado,
                   l.resultado, l.ganancia_u, l.clv_ev, l.sin_clv
            FROM picks p LEFT JOIN pick_liquidaciones l ON l.pick_id = p.id
            WHERE p.partido_id = %s AND p.origen IN ('paper','real') ORDER BY p.creado_en
        """, (p["id"],)).fetchall()
        salida.append(item)
    return salida


def parlays(conn, d: date) -> list[dict]:
    filas = conn.execute("""SELECT ps.*, l.resultado, l.ganancia_u, l.ganancia_simples_u
                            FROM parlays_sugeridos ps LEFT JOIN parlay_liquidaciones l ON l.parlay_id = ps.id
                            WHERE ps.dia = %s AND ps.origen IN ('paper','real') ORDER BY ps.creado_en DESC, ps.ranking""",
                         (d,)).fetchall()
    for f in filas:
        f["bloques"] = conn.execute("""
            SELECT b.*, el.nombre AS local, ev.nombre AS visitante FROM parlay_bloques b
            JOIN partidos p ON p.id = b.partido_id JOIN equipos el ON el.id = p.local_id
            JOIN equipos ev ON ev.id = p.visitante_id WHERE b.parlay_id = %s ORDER BY b.orden""", (f["id"],)).fetchall()
        f["es_apuesta_del_dia"] = f["ranking"] == 1
    return filas


def clv(conn, cfg: dict) -> dict:
    """Dashboard de CLV + criterio para apostar dinero real."""
    resumen = conn.execute("SELECT r.*, l.codigo AS liga FROM v_clv_resumen r JOIN ligas l ON l.id = r.liga_id").fetchall()
    serie = conn.execute("""
        SELECT p.partido_id, min(pa.inicio) AS inicio, avg(l.clv_ev) AS clv_ev, sum(l.ganancia_u) AS ganancia_u
        FROM picks p JOIN pick_liquidaciones l ON l.pick_id = p.id AND NOT l.sin_clv AND l.clv_ev IS NOT NULL
        JOIN partidos pa ON pa.id = p.partido_id WHERE p.origen IN ('paper','real')
        GROUP BY p.partido_id ORDER BY 2""").fetchall()
    x = np.array([float(r["clv_ev"]) for r in serie])
    n = len(x)
    minimo = cfg["operacion"]["picks_min_paper"]
    media = float(x.mean()) if n else None
    cota = float(media - 1.645 * x.std(ddof=1) / math.sqrt(n)) if n > 1 else None
    listo = bool(n >= minimo and cota is not None and cota > 0)
    return {"resumen": resumen, "serie": serie,
            "criterio": {"partidos_independientes": n, "minimo": minimo, "clv_ev_medio": media,
                         "cota_inferior_95": cota, "listo_para_dinero_real": listo,
                         "explicacion": "Se cuenta un partido una sola vez (sus picks no son independientes). "
                                        "Dinero real solo si n >= mínimo y la cota inferior al 95% del CLV medio > 0."},
            "crs_efectividad": conn.execute("SELECT * FROM v_crs_efectividad ORDER BY crs_alerta").fetchall()}
