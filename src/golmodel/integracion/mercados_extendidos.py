"""BD -> mercados nuevos (córners y props) por partido y «Soñadoras analíticas» del día. Adaptador: la lógica vive en
`modelos/corners.py`, `modelos/props.py`, `motor/dependencia_mercados.py` y `motor/sonadoras_analiticas.py`.

No escribe en las tablas del modelo de goles ni en las de V3: lee la última predicción de producción (λ y ρ de Dixon-Coles) y le
agrega córners y jugadores. Los modelos de córners se ajustan al pedirlos (≈ 1 s por liga) y se guardan en memoria por día.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from golmodel.modelos import corners as mc
from golmodel.modelos import props as mp
from golmodel.modelos.incertidumbre import FactorSamplerJerarquico
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import probabilidad
from golmodel.motor import dependencia_mercados as dm
from golmodel.motor import sonadoras_analiticas as sa
from golmodel.repositorio import cuotas_vigentes

_CACHE_CORNERS: dict = {}
MERCADOS_GOLES = ("1X2:1", "1X2:X", "1X2:2", "OU:1.5:over", "OU:2.5:over", "OU:2.5:under", "OU:3.5:under", "BTTS:si", "BTTS:no",
                  "ANOTA:local:si", "ANOTA:visitante:si", "DC:1X", "DC:X2")


def modelo_corners(conn, liga_id: int, cfg: dict, ahora: datetime) -> tuple[mc.ModeloCorners, float] | None:
    """(modelo, Spearman goles-córners) de la liga, o None si hay pocos datos. Caché por liga y día."""
    clave = (liga_id, ahora.date())
    if clave in _CACHE_CORNERS:
        return _CACHE_CORNERS[clave]
    filas = conn.execute("""
        SELECT p.local_id AS local, p.visitante_id AS visitante, p.inicio, p.goles_local, p.goles_visitante,
               e.corners_local, e.corners_visitante, e.arbitro
        FROM estadisticas_partido e JOIN partidos p ON p.id = e.partido_id
        WHERE p.liga_id = %s AND p.estado = 'finalizado' AND p.inicio < %s""", (liga_id, ahora)).fetchall()
    cc = cfg["corners"]
    if len(filas) < int(cc["min_partidos_liga"]):
        _CACHE_CORNERS[clave] = None
        return None
    df = pd.DataFrame(filas)
    m = mc.ajustar(df, ahora, xi=float(cc["decaimiento_xi"]), l2=float(cc["l2"]), l2_arbitro=float(cc["l2_arbitro"]),
                   min_partidos_arbitro=int(cc["min_partidos_arbitro"]))
    out = (m, mc.correlacion_goles_corners(df))
    _CACHE_CORNERS[clave] = out
    return out


def _partido(conn, partido_id: int) -> dict | None:
    return conn.execute("""
        SELECT p.id AS partido_id, p.inicio, p.liga_id, l.codigo AS liga, p.local_id, p.visitante_id, el.nombre AS local,
               ev.nombre AS visitante, pr.id AS prediccion_id, pr.lambda_local, pr.lambda_visitante, pr.rho
        FROM partidos p JOIN ligas l ON l.id = p.liga_id JOIN equipos el ON el.id = p.local_id JOIN equipos ev ON ev.id = p.visitante_id
        LEFT JOIN LATERAL (SELECT * FROM predicciones x WHERE x.partido_id = p.id AND x.origen = 'produccion'
                           ORDER BY x.creada_en DESC LIMIT 1) pr ON true
        WHERE p.id = %s""", (partido_id,)).fetchone()


def _xga_por_partido(conn, equipo_id: int, ahora: datetime) -> tuple[float | None, float | None]:
    """(xG concedido por partido del equipo, promedio de su liga) en el último año; con goles si no hay xG."""
    r = conn.execute("""
        WITH m AS (SELECT p.liga_id, CASE WHEN p.local_id = %(e)s THEN coalesce(p.xg_visitante, p.goles_visitante)
                                          ELSE coalesce(p.xg_local, p.goles_local) END AS conc
                   FROM partidos p WHERE (p.local_id = %(e)s OR p.visitante_id = %(e)s) AND p.estado = 'finalizado'
                     AND p.inicio BETWEEN %(a)s - interval '365 days' AND %(a)s)
        SELECT avg(conc)::float8 AS xga,
               (SELECT avg(coalesce(q.xg_local, q.goles_local) + coalesce(q.xg_visitante, q.goles_visitante))::float8 / 2
                FROM partidos q WHERE q.liga_id = (SELECT liga_id FROM m LIMIT 1) AND q.estado = 'finalizado'
                  AND q.inicio BETWEEN %(a)s - interval '365 days' AND %(a)s) AS liga
        FROM m""", {"e": equipo_id, "a": ahora}).fetchone()
    return (r["xga"], r["liga"]) if r else (None, None)


def _jugadores(conn, equipo_id: int, cfg: dict) -> list[mp.Jugador]:
    pc = cfg["props"]
    filas = conn.execute("""
        SELECT DISTINCT ON (j.id) j.id, j.nombre, jp.equipo_id, jp.minutos, coalesce(jp.partidos, 0) AS partidos,
               jp.xg::float8 AS xg, jp.tiros
        FROM jugador_produccion jp JOIN jugadores j ON j.id = jp.jugador_id
        WHERE jp.equipo_id = %s AND jp.fuente = 'understat' AND jp.xg IS NOT NULL AND jp.minutos >= %s
        ORDER BY j.id, jp.temporada DESC""", (equipo_id, int(pc["minutos_min_temporada"]))).fetchall()
    js = [mp.Jugador(f["id"], f["nombre"], f["equipo_id"], int(f["minutos"]), int(f["partidos"]), float(f["xg"]),
                     None if f["tiros"] is None else float(f["tiros"])) for f in filas if f["partidos"]]
    js.sort(key=lambda j: -(j.xg / j.minutos))
    return js[: int(pc["max_jugadores_por_equipo"])]


def mercados_partido(conn, partido_id: int, cfg: dict, ahora: datetime, *, con_jugadores: bool = True) -> dict:
    """Córners y props de un partido + el `PartidoExt` para combinarlos con goles. Lo que no se puede calcular se explica."""
    f = _partido(conn, partido_id)
    if f is None:
        raise KeyError(partido_id)
    out = {"partido_id": partido_id, "liga": f["liga"], "local": f["local"], "visitante": f["visitante"],
           "corners": None, "jugadores": [], "avisos": [], "partido_ext": None}
    if f["prediccion_id"] is None:
        out["avisos"].append("Sin predicción del modelo de goles para este partido: córners y props necesitan sus λ.")
        return out
    lam_l, lam_v, rho = float(f["lambda_local"]), float(f["lambda_visitante"]), float(f["rho"])
    corners_ext = None
    spearman = 0.0
    if f["liga"] in cfg["corners"]["ligas"]:
        r = modelo_corners(conn, f["liga_id"], cfg, ahora)
        if r is None:
            out["avisos"].append("Córners: todavía no hay histórico suficiente en esta liga (se carga solo con «Actualizar datos»).")
        else:
            m, spearman = r
            if not (m.conoce(f["local_id"]) and m.conoce(f["visitante_id"])):
                out["avisos"].append("Córners: uno de los equipos no tiene historial de córners (ascendido o nuevo).")
            else:
                d = mc.distribucion(m, f["local_id"], f["visitante_id"])
                cc = cfg["corners"]
                out["corners"] = {
                    "esperados_local": d["mu_local"], "esperados_visitante": d["mu_visitante"],
                    "esperados_total": d["mu_local"] + d["mu_visitante"], "dispersion_k": m.k, "n_partidos_modelo": m.n_partidos,
                    "spearman_goles": spearman,
                    "total": [{"linea": x, "mas": mc.prob_mas_de(d["total"], x), "menos": 1 - mc.prob_mas_de(d["total"], x)}
                              for x in cc["lineas_total"]],
                    "local": [{"linea": x, "mas": mc.prob_mas_de(d["local"], x)} for x in cc["lineas_equipo"]],
                    "visitante": [{"linea": x, "mas": mc.prob_mas_de(d["visitante"], x)} for x in cc["lineas_equipo"]],
                    "version": mc.VERSION}
                corners_ext = (d["mu_local"], d["mu_visitante"], m.k)
    else:
        out["avisos"].append("Córners: la fuente gratuita no publica córners de esta liga.")
    jugadores_ext: dict = {}
    if con_jugadores and f["liga"] in cfg["props"]["ligas"]:
        M = matriz_marcadores(lam_l, lam_v, rho)
        for lado, eid, rid, lam in (("local", f["local_id"], f["visitante_id"], lam_l), ("visitante", f["visitante_id"], f["local_id"], lam_v)):
            xga, prom = _xga_por_partido(conn, rid, ahora)
            fr = mp.factor_rival(xga, prom, cfg)
            pmf_eq = mp.pmf_marginal(M.p, lado)
            for j in _jugadores(conn, eid, cfg):
                pf = mp.perfil(j, lam, fr, cfg)
                if pf.cuota_gol_equipo <= 0:
                    continue
                fila = {"jugador_id": j.jugador_id, "nombre": j.nombre, "lado": lado, "titularidad": pf.titularidad,
                        "minutos_esperados": pf.minutos_esperados, "xg90": pf.xg90, "factor_rival": fr,
                        "p_anota": mp.p_anota(pf, pmf_eq),
                        "tiros_arco": [{"minimo": k, "p": mp.p_tiros_arco(pf, k)} for k in cfg["props"]["tiros_minimos"]]
                        if pf.tiros_arco_esperados is not None else None}
                out["jugadores"].append(fila)
                jugadores_ext[j.jugador_id] = dm.JugadorPartido(j.jugador_id, lado, pf.cuota_gol_equipo, pf.tiros_arco_esperados)
        if not out["jugadores"]:
            out["avisos"].append("Props: sin datos por jugador (Understat). Se llenan con «Actualizar datos» cuando Understat responde.")
    elif con_jugadores:
        out["avisos"].append("Props: solo en ligas con xG por jugador (Premier, Serie A, LaLiga, Bundesliga, Ligue 1).")
    out["partido_ext"] = dm.PartidoExt(partido_id, f["liga"], lam_l, lam_v, rho, corners=corners_ext, jugadores=jugadores_ext)
    out["spearman"] = spearman
    return out


def _cuota_de(df: pd.DataFrame, seleccion: str, cfg: dict) -> float | None:
    if df is None or df.empty:
        return None
    for casa in ("manual", cfg["mercado"]["casa_ejecucion"]):
        x = df[(df["casa"] == casa) & (df["seleccion"] == seleccion)]
        if not x.empty:
            return float(x.iloc[0]["cuota"])
    return None


def candidatas_dia(conn, dia: date, cfg: dict, ahora: datetime) -> tuple[list[sa.CandidataExt], list[dm.PartidoExt], float]:
    from golmodel.servicios.sugerencias import texto_jugada
    partidos = conn.execute("""SELECT p.id FROM partidos p JOIN ligas l ON l.id = p.liga_id
                               WHERE dia_mx(p.inicio) = %s AND p.estado = 'programado' AND NOT l.solo_mercado
                               ORDER BY p.inicio""", (dia,)).fetchall()
    cands, exts, rhos = [], [], []
    for r in partidos:
        m = mercados_partido(conn, r["id"], cfg, ahora)
        ext = m["partido_ext"]
        if ext is None:
            continue
        exts.append(ext)
        if m["corners"]:
            rhos.append(m["spearman"])
        M = matriz_marcadores(ext.lambda_local, ext.lambda_visitante, ext.rho)
        cuotas = cuotas_vigentes(conn, r["id"])
        for s in MERCADOS_GOLES:
            p = probabilidad(M, s)
            cands.append(sa.CandidataExt(r["id"], s, p, _cuota_de(cuotas, s, cfg),
                                         f"{texto_jugada(s, m['local'], m['visitante'])[0]} ({m['local']} vs {m['visitante']})", "goles"))
        if m["corners"]:
            for x in m["corners"]["total"]:
                cands.append(sa.CandidataExt(r["id"], f"CORNERS:total:over:{x['linea']}", x["mas"], None,
                                             f"Más de {x['linea']} córners ({m['local']} vs {m['visitante']})", "corners"))
                cands.append(sa.CandidataExt(r["id"], f"CORNERS:total:under:{x['linea']}", x["menos"], None,
                                             f"Menos de {x['linea']} córners ({m['local']} vs {m['visitante']})", "corners"))
        for j in m["jugadores"]:
            cands.append(sa.CandidataExt(r["id"], f"JUG:{j['jugador_id']}:anota", j["p_anota"], None, f"{j['nombre']} anota", "props"))
            for t in j["tiros_arco"] or []:
                cands.append(sa.CandidataExt(r["id"], f"JUG:{j['jugador_id']}:tiros_arco:{t['minimo']}", t["p"], None,
                                             f"{j['nombre']}: {t['minimo']}+ tiros al arco", "props"))
    spearman = float(sum(rhos) / len(rhos)) if rhos else 0.0
    return cands, exts, spearman


def sonadoras_analiticas_dia(conn, dia: date, ahora: datetime, cfg_v2: dict, cfg_v3: dict, *, cuota_min: float, cuota_max: float) -> dict:
    cands, exts, spearman = candidatas_dia(conn, dia, cfg_v2, ahora)
    cfg = {**cfg_v3, "mercados_extendidos": {**cfg_v2["mercados_extendidos"], "spearman_goles_corners": spearman}}
    vp = cfg_v3["calibracion_tickets"]["version_provisional"]
    sampler = FactorSamplerJerarquico(sigma_g=float(vp["sigma_g"]), sigma_liga=float(vp["sigma_liga"]))
    sal = sa.construir(cands, exts, sampler, cfg, cuota_min=cuota_min, cuota_max=cuota_max, semilla=int(dia.strftime("%Y%m%d")))
    out = sal.a_json()
    out["dia"] = dia.isoformat()
    out["stake_u"] = float(cfg_v3["sonadoras_analiticas"]["stake_pct_banca"]) / float(cfg_v3["bankroll"]["unidad_pct_banca"])
    out["aviso"] = ("Cuota combinada TEÓRICA (producto de cuotas). En un SGP la casa paga menos porque descuenta la correlación: "
                    "compárala con la que te ofrezca. Córners y props no tienen cuota de casa en las fuentes gratuitas.")
    return out
