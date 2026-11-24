"""BD <-> motor NFL: carga de datos (nflverse + The Odds API) y predicciones de la semana para la API/PWA."""
from __future__ import annotations

import gc
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from golmodel.modelos.devig import quitar_margen
from golmodel.motor_nfl import derivados as dv
from golmodel.motor_nfl import evaluacion as ev
from golmodel.motor_nfl import mercados as mk
from golmodel.motor_nfl import modelo as md
from golmodel.motor_nfl import temporada as tp
from golmodel.motor_nfl.datos import NOMBRES, FuenteNFL, epa_por_juego

log = logging.getLogger(__name__)


def _entero(x):
    return None if x is None else int(x)


def temporada_actual(ahora: datetime) -> int:
    return ahora.year if ahora.month >= 3 else ahora.year - 1


def actualizar(conn, fuente: FuenteNFL, cfg: dict, ahora: datetime) -> dict:
    """Calendario/marcadores (todas las temporadas pedidas), EPA (las que falten y la actual) y cuotas en vivo."""
    n = cfg["nfl"]
    actual = temporada_actual(ahora)
    temporadas = list(range(actual - int(n["temporadas_historico"]), actual + 1))
    temporadas_epa = list(range(actual - int(n.get("epa", {}).get("temporadas", 4)), actual + 1))
    res = {"juegos": 0, "epa": {}, "cuotas": 0, "avisos": []}
    filas = fuente.games(temporadas)
    for f in filas:
        conn.execute("""INSERT INTO nfl_juegos (juego_id, temporada, tipo, semana, inicio, local, visitante, puntos_local,
                            puntos_visitante, neutral, spread_cierre, total_cierre, ml_local, ml_visitante, qb_local, qb_visitante)
                        VALUES (%(juego_id)s, %(temporada)s, %(tipo)s, %(semana)s, %(inicio)s, %(local)s, %(visitante)s,
                                %(puntos_local)s, %(puntos_visitante)s, %(neutral)s, %(spread_cierre)s, %(total_cierre)s,
                                %(ml_local)s, %(ml_visitante)s, %(qb_local)s, %(qb_visitante)s)
                        ON CONFLICT (juego_id) DO UPDATE SET inicio = EXCLUDED.inicio, puntos_local = EXCLUDED.puntos_local,
                            puntos_visitante = EXCLUDED.puntos_visitante, spread_cierre = EXCLUDED.spread_cierre,
                            total_cierre = EXCLUDED.total_cierre, ml_local = EXCLUDED.ml_local,
                            ml_visitante = EXCLUDED.ml_visitante,
                            qb_local = COALESCE(EXCLUDED.qb_local, nfl_juegos.qb_local),
                            qb_visitante = COALESCE(EXCLUDED.qb_visitante, nfl_juegos.qb_visitante), actualizado_en = now()""",
                     {**f, "ml_local": _entero(f.get("ml_local")), "ml_visitante": _entero(f.get("ml_visitante")),
                      "qb_local": f.get("qb_local"), "qb_visitante": f.get("qb_visitante")})
    res["juegos"] = len(filas)
    conn.commit()
    if n.get("epa", {}).get("activo", True):
        con_epa = {r["temporada"] for r in conn.execute(
            "SELECT DISTINCT j.temporada FROM nfl_epa_juego e JOIN nfl_juegos j USING (juego_id)")}
        for t in temporadas_epa:
            if t in con_epa and t != actual:
                continue
            try:
                filas_epa = epa_por_juego(fuente.pbp(t))
            except Exception as e:                       # noqa: BLE001 — sin EPA el modelo sigue con Elo
                res["avisos"].append(f"EPA {t}: no disponible ({type(e).__name__}); el modelo usa solo Elo para esa temporada.")
                continue
            ids = {r["juego_id"] for r in conn.execute("SELECT juego_id FROM nfl_juegos WHERE temporada = %s", (t,))}
            k = 0
            for e in filas_epa:
                if e["juego_id"] in ids:
                    conn.execute("""INSERT INTO nfl_epa_juego (juego_id, equipo, epa_of, epa_def, jugadas)
                                    VALUES (%(juego_id)s, %(equipo)s, %(epa_of)s, %(epa_def)s, %(jugadas)s)
                                    ON CONFLICT (juego_id, equipo) DO UPDATE SET epa_of = EXCLUDED.epa_of,
                                        epa_def = EXCLUDED.epa_def, jugadas = EXCLUDED.jugadas""", e)
                    k += 1
            res["epa"][t] = k
            conn.commit()
            del filas_epa
            gc.collect()                                 # una temporada a la vez: libera el play-by-play antes de la siguiente
    try:
        cuotas = fuente.cuotas()
    except Exception as e:                               # noqa: BLE001
        cuotas = []
        res["avisos"].append(f"Cuotas NFL: no disponibles ({type(e).__name__}).")
    for q in cuotas:
        j = conn.execute("""SELECT juego_id FROM nfl_juegos WHERE local = %s AND visitante = %s
                            AND inicio BETWEEN %s AND %s ORDER BY inicio LIMIT 1""",
                         (q["local"], q["visitante"], q["inicio"] - timedelta(hours=36), q["inicio"] + timedelta(hours=36))).fetchone()
        if j is None:
            continue
        conn.execute("UPDATE nfl_juegos SET odds_api_id = %s WHERE juego_id = %s AND odds_api_id IS DISTINCT FROM %s",
                     (q["odds_api_id"], j["juego_id"], q["odds_api_id"]))
        conn.execute("""INSERT INTO nfl_cuotas (juego_id, casa, mercado, seleccion, linea, cuota, capturada_en)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                     (j["juego_id"], q["casa"], q["mercado"], q["seleccion"], q["linea"], q["cuota"], q["capturada_en"]))
        res["cuotas"] += 1
    conn.commit()
    return res


_CACHE_ESTADO: dict = {}


def _estado(conn, cfg: dict, desde_temporada: int | None = None) -> tuple[md.EstadoNFL, list[dict]]:
    """Estado del modelo (todo el histórico, juego a juego) y la predicción previa de cada juego desde `desde_temporada`.
    Se guarda en memoria hasta que cambian los datos (marcadores, EPA) o la configuración del modelo."""
    firma = conn.execute("""SELECT (SELECT count(*) FROM nfl_juegos WHERE puntos_local IS NOT NULL) AS n,
                                   (SELECT max(actualizado_en) FROM nfl_juegos) AS t,
                                   (SELECT count(*) FROM nfl_epa_juego) AS e, current_database() AS db""").fetchone()
    clave = (firma["db"], firma["n"], str(firma["t"]), firma["e"], repr(sorted(cfg["nfl"]["modelo"].items())))
    if _CACHE_ESTADO.get("clave") != clave:          # se guardan TODOS los registros; cada vista filtra su temporada
        _CACHE_ESTADO.update(clave=clave, valor=_estado_sin_cache(conn, cfg, 0))
    est, registros = _CACHE_ESTADO["valor"]
    if desde_temporada is None:
        return est, []
    return est, [r for r in registros if r["temporada"] >= desde_temporada]


def _estado_sin_cache(conn, cfg: dict, desde_temporada: int | None) -> tuple[md.EstadoNFL, list[dict]]:
    juegos = [md.Juego(r["juego_id"], r["temporada"], r["semana"], r["local"], r["visitante"], r["puntos_local"],
                       r["puntos_visitante"], r["neutral"], r["tipo"], r.get("qb_local"), r.get("qb_visitante"))
              for r in conn.execute("SELECT * FROM nfl_juegos WHERE puntos_local IS NOT NULL")]
    epa: dict = defaultdict(dict)
    for r in conn.execute("SELECT juego_id, equipo, epa_of, epa_def, jugadas FROM nfl_epa_juego"):
        epa[r["juego_id"]][r["equipo"]] = (r["epa_of"], r["epa_def"], r["jugadas"])
    return ev.recorrer(juegos, cfg, epa, desde_temporada=desde_temporada)


def _ultimas_cuotas(conn, juego_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT DISTINCT ON (casa, mercado, seleccion) casa, mercado, seleccion, linea::float8 AS linea, cuota::float8 AS cuota, capturada_en
        FROM nfl_cuotas WHERE juego_id = %s ORDER BY casa, mercado, seleccion, capturada_en DESC""", (juego_id,))]


def _mercado(cuotas: list[dict], mercado: str, casas: list[str]) -> dict | None:
    """Cuotas de UNA casa (la primera de la lista de preferencia que tenga el mercado completo)."""
    por_casa: dict = defaultdict(dict)
    for q in cuotas:
        if q["mercado"] == mercado:
            por_casa[q["casa"]][q["seleccion"]] = q
    orden = [c for c in casas if c in por_casa] + sorted(c for c in por_casa if c not in casas)
    pares = ("local", "visitante") if mercado != "totals" else ("over", "under")
    for c in orden:
        if all(s in por_casa[c] for s in pares):
            a, b = por_casa[c][pares[0]], por_casa[c][pares[1]]
            pm = quitar_margen([a["cuota"], b["cuota"]], "potencia")
            return {"casa": c, pares[0]: a, pares[1]: b, "p_mercado": {pares[0]: float(pm[0]), pares[1]: float(pm[1])}}
    return None


def semanas_pendientes(conn, temporada: int, ahora: datetime) -> list[dict]:
    """Semanas de la temporada con juegos por jugar (para el filtro), en orden de calendario."""
    return [{"semana": int(r["semana"]), "tipo": r["tipo"], "inicio": r["inicio"].isoformat(), "juegos": int(r["n"])}
            for r in conn.execute("""SELECT semana, min(tipo) AS tipo, min(inicio) AS inicio, count(*) AS n FROM nfl_juegos
                                     WHERE temporada = %s AND puntos_local IS NULL AND inicio >= %s
                                     GROUP BY semana ORDER BY min(inicio)""", (temporada, ahora - timedelta(hours=4)))]


def semana(conn, cfg: dict, ahora: datetime, semana_sel: int | None = None) -> dict:
    """Juegos por jugar de UNA semana (la más próxima si no se pide otra) con sus mercados. `semanas` lista las que quedan
    (filtro de la app): antes se mostraban los próximos N días y se mezclaban dos semanas (el jueves de la siguiente)."""
    n = cfg["nfl"]
    actual = temporada_actual(ahora)
    semanas = semanas_pendientes(conn, actual, ahora)
    if semana_sel is None or all(s["semana"] != semana_sel for s in semanas):
        semana_sel = semanas[0]["semana"] if semanas else None
    est, registros = _estado(conn, cfg, desde_temporada=actual)
    sig_m, sig_t = float(n["modelo"]["sigma_margen"]), float(n["modelo"]["sigma_total"])
    if est.n_juegos >= int(n["modelo"]["min_juegos_sigma"]):
        sig_m, sig_t = md.ajustar_sigmas(est)
    proximos = conn.execute("""SELECT * FROM nfl_juegos WHERE temporada = %s AND semana = %s AND puntos_local IS NULL AND inicio >= %s
                               ORDER BY inicio""", (actual, semana_sel, ahora - timedelta(hours=4))).fetchall() if semana_sel is not None else []
    casas = list(n["casas_preferidas"])
    umbral = float(n["umbral_edge"])
    alfa = float(n.get("peso_modelo", 1.0))
    juegos = []
    for j in proximos:
        pr = md.prediccion(est, j["local"], j["visitante"], cfg, j["neutral"], j.get("qb_local"), j.get("qb_visitante"))
        mu_m, mu_t = pr["mu_margen"], pr["mu_total"]
        cuotas = _ultimas_cuotas(conn, j["juego_id"])
        ml = mk.moneyline(mu_m, sig_m)
        salida = {"juego_id": j["juego_id"], "semana": j["semana"], "inicio": j["inicio"].isoformat() if j["inicio"] else None,
                  "local": j["local"], "visitante": j["visitante"], "local_nombre": NOMBRES.get(j["local"], j["local"]),
                  "visitante_nombre": NOMBRES.get(j["visitante"], j["visitante"]),
                  "modelo": {**pr, "sigma_margen": sig_m, "sigma_total": sig_t}, "mercados": []}
        h2h = _mercado(cuotas, "h2h", casas)
        for lado in ("local", "visitante"):
            fila = {"mercado": "Moneyline", "seleccion": lado, "linea": None, "p_modelo": ml[lado], "p_push": 0.0,
                    "cuota_justa": 1.0 / ml[lado] if ml[lado] > 0 else None}
            if h2h:
                fila.update(casa=h2h["casa"], cuota=h2h[lado]["cuota"], p_mercado=h2h["p_mercado"][lado],
                            ev=mk.ev(ml[lado], 0.0, h2h[lado]["cuota"]))
            salida["mercados"].append(fila)
        sp = _mercado(cuotas, "spreads", casas)
        linea_sp = sp["local"]["linea"] if sp else (-float(j["spread_cierre"]) if j["spread_cierre"] is not None else None)
        if linea_sp is not None:
            ps = mk.spread(mu_m, sig_m, linea_sp)
            for lado in ("local", "visitante"):
                linea = linea_sp if lado == "local" else -linea_sp
                fila = {"mercado": "Spread", "seleccion": lado, "linea": linea, "p_modelo": ps[lado], "p_push": ps["push"],
                        "cuota_justa": (1 - ps["push"]) / ps[lado] if ps[lado] > 0 else None}
                if sp and sp["local"]["linea"] == linea_sp:
                    fila.update(casa=sp["casa"], cuota=sp[lado]["cuota"], p_mercado=sp["p_mercado"][lado],
                                ev=mk.ev(ps[lado], ps["push"], sp[lado]["cuota"]))
                salida["mercados"].append(fila)
        tt = _mercado(cuotas, "totals", casas)
        linea_t = tt["over"]["linea"] if tt else (float(j["total_cierre"]) if j["total_cierre"] is not None else None)
        if linea_t is not None:
            pt = mk.totales(mu_t, sig_t, linea_t)
            for lado in ("over", "under"):
                fila = {"mercado": "Total", "seleccion": lado, "linea": linea_t, "p_modelo": pt[lado], "p_push": pt["push"],
                        "cuota_justa": (1 - pt["push"]) / pt[lado] if pt[lado] > 0 else None}
                if tt:
                    fila.update(casa=tt["casa"], cuota=tt[lado]["cuota"], p_mercado=tt["p_mercado"][lado],
                                ev=mk.ev(pt[lado], pt["push"], tt[lado]["cuota"]))
                salida["mercados"].append(fila)
        for f in salida["mercados"]:
            # p_final: mezcla con el mercado (walk-forward 2020-25: el Elo solo tiene Brier 0.2223 vs 0.2096 de Las Vegas; la
            # mezcla evita que cualquier diferencia del modelo se marque como «valor»).
            if f.get("p_mercado") is not None:
                f["p_final"] = alfa * f["p_modelo"] + (1 - alfa) * f["p_mercado"]
                f["ev"] = mk.ev(f["p_final"], f["p_push"], f["cuota"])
            else:
                f["p_final"] = f["p_modelo"]
            f["valor"] = bool(f.get("p_mercado") is not None and f["p_final"] - f["p_mercado"] >= umbral and f.get("ev", -1) > 0)
        if linea_sp is not None and linea_t is not None:                 # mercados extra desde la LÍNEA (medidos contra el cierre)
            salida["derivados"] = dv.derivados(-float(linea_sp), float(linea_t))
            salida["recomendadas"] = dv.recomendadas(salida["derivados"], NOMBRES.get(j["local"], j["local"]),
                                                     NOMBRES.get(j["visitante"], j["visitante"]), -float(linea_sp))
        juegos.append(salida)
    return {"juegos": juegos, "semanas": semanas, "semana_sel": semana_sel,
            "n_juegos_historico": est.n_juegos, "sigma_margen": sig_m, "sigma_total": sig_t,
            "version": md.VERSION, "aviso": n["aviso"], "peso_modelo": alfa, "nombres": NOMBRES,
            "hfa_puntos": md.hfa_elo(est, cfg) / float(n["modelo"]["elo_por_punto"]),
            **resumen_temporada(conn, cfg, est, registros, actual, juegos, sig_m)}


def _vegas(conn, temporada: int, sigma: float) -> dict:
    return {r["juego_id"]: ev.p_vegas(r["ml_local"], r["ml_visitante"],
                                      None if r["spread_cierre"] is None else float(r["spread_cierre"]), sigma)
            for r in conn.execute("SELECT juego_id, ml_local, ml_visitante, spread_cierre FROM nfl_juegos WHERE temporada = %s",
                                  (temporada,))}


def destacados(juegos: list[dict], vegas: dict) -> dict:
    """El más seguro, la moneda al aire y la sorpresa (el modelo contra el favorito de Las Vegas) de los próximos juegos."""
    filas = []
    for j in juegos:
        ml = next((m for m in j["mercados"] if m["mercado"] == "Moneyline" and m["seleccion"] == "local"), None)
        if ml is None:
            continue
        p = ml["p_modelo"]
        pick = j["local"] if p >= 0.5 else j["visitante"]
        pv = ml.get("p_mercado", vegas.get(j["juego_id"]))
        filas.append({"juego_id": j["juego_id"], "partido": f"{j['visitante']} @ {j['local']}", "inicio": j["inicio"], "pick": pick,
                      "p_pick": max(p, 1 - p), "p_local_vegas": pv, "margen": abs(j["modelo"]["mu_margen"])})
    if not filas:
        return {}
    out = {"mas_seguro": max(filas, key=lambda f: f["p_pick"]), "moneda_al_aire": min(filas, key=lambda f: f["p_pick"])}
    choques = []
    for f in filas:
        pv = f["p_local_vegas"]
        if pv is None:
            continue
        local = f["partido"].split(" @ ")[1]
        fav_vegas = local if pv >= 0.5 else f["partido"].split(" @ ")[0]
        if fav_vegas != f["pick"]:
            choques.append({**f, "favorito_vegas": fav_vegas, "p_vegas": max(pv, 1 - pv)})
    if choques:
        out["sorpresa"] = max(choques, key=lambda f: f["p_pick"])
    return out


def resumen_temporada(conn, cfg: dict, est: md.EstadoNFL, registros: list[dict], actual: int, juegos: list[dict],
                      sigma: float) -> dict:
    """«¿Cómo va el modelo?» (temporada actual), ranking Elo con su cambio, destacados y proyección Monte Carlo."""
    vegas = _vegas(conn, actual, sigma)
    vegas = {k: v for k, v in vegas.items() if v is not None}
    reg = [r for r in registros if r["temporada"] == actual and r["tipo"] == "REG"]
    out = {"temporada": actual, "desempeno": ev.metricas(reg, vegas), "por_semana": ev.por_semana(reg, vegas),
           "ranking_elo": ev.ranking_elo(est, registros, actual), "destacados": destacados(juegos, vegas)}
    sim = cfg["nfl"].get("simulacion", {})
    if sim.get("activo", True):
        pend = [dict(r) for r in conn.execute("""SELECT local, visitante, neutral FROM nfl_juegos
                                                 WHERE temporada = %s AND tipo = 'REG' AND puntos_local IS NULL""", (actual,))]
        if reg or pend:
            clave = (actual, len(reg), len(pend), reg[-1]["juego_id"] if reg else None, round(sigma, 2))
            if _CACHE_SIM.get("clave") != clave:          # la simulación (≈ 3 s) solo se repite cuando hay resultados nuevos
                _CACHE_SIM.update(clave=clave, valor=tp.simular(est, cfg, reg, pend, sigma,
                                                               n_sims=int(sim.get("simulaciones", 4000)),
                                                               ruido_pts=float(sim.get("ruido_puntos", 1.5))))
            out["proyeccion"] = _CACHE_SIM["valor"]
    return out


_CACHE_SIM: dict = {}


def dashboard(conn, cfg: dict, ahora: datetime) -> dict:
    """Efectividad del modelo NFL para el Dashboard: temporada actual (con la predicción previa a cada juego), su curva
    acumulada semana a semana, el cambio contra la semana anterior y la temporada pasada como punto de comparación."""
    n = cfg["nfl"]
    actual = temporada_actual(ahora)
    est, registros = _estado(conn, cfg, desde_temporada=actual - 1)
    sig = float(n["modelo"]["sigma_margen"])
    if est.n_juegos >= int(n["modelo"]["min_juegos_sigma"]):
        sig = md.ajustar_sigmas(est)[0]
    vegas = {}
    for t in (actual - 1, actual):
        vegas.update({k: v for k, v in _vegas(conn, t, sig).items() if v is not None})
    reg = [r for r in registros if r["temporada"] == actual and r["tipo"] == "REG"]
    previa = [r for r in registros if r["temporada"] == actual - 1]
    m = ev.metricas(reg, vegas)
    curva, acum = [], []
    for w in sorted({r["semana"] for r in reg}):
        acum += [r for r in reg if r["semana"] == w]
        ma = ev.metricas(acum, vegas)
        sem = ev.metricas([r for r in reg if r["semana"] == w], vegas)
        curva.append({"semana": w, "record": sem["record"], "pct_semana": sem["pct_aciertos"], "pct_acum": ma["pct_aciertos"],
                      "brier_acum": ma["brier"], "vegas_acum": ma.get("vegas", {}).get("pct_aciertos")})
    delta = curva[-1]["pct_acum"] - curva[-2]["pct_acum"] if len(curva) >= 2 else None
    mp = ev.metricas(previa, vegas) if previa else {"partidos": 0}
    return {"temporada": actual, "desempeno": m, "curva": curva, "delta_semana": delta,
            "temporada_pasada": {k: mp.get(k) for k in ("partidos", "record", "pct_aciertos", "brier")} | {"temporada": actual - 1},
            "nombres": NOMBRES, "version": md.VERSION}


def piernas_parlay(conn, cfg: dict, ahora: datetime, hasta: datetime, p_min: float) -> list[dict]:
    """Piernas NFL para «Qué meter» y «Soñadoras»: «Gana <equipo>» (moneyline) con la probabilidad final (modelo mezclado
    con Las Vegas cuando hay cuota), solo juegos que no han empezado y con p ≥ p_min. Mismo formato que las de fútbol."""
    from zoneinfo import ZoneInfo
    n = cfg["nfl"]
    zona = ZoneInfo(cfg["zona_horaria"])
    u = float(cfg["valor"]["umbral_edge"])
    alfa = float(n.get("peso_modelo", 1.0))
    est, _ = _estado(conn, cfg, desde_temporada=None)
    sig = float(n["modelo"]["sigma_margen"])
    if est.n_juegos >= int(n["modelo"]["min_juegos_sigma"]):
        sig = md.ajustar_sigmas(est)[0]
    casas = list(n["casas_preferidas"])
    salida = []
    for j in conn.execute("""SELECT * FROM nfl_juegos WHERE puntos_local IS NULL AND inicio > %s AND inicio < %s
                             ORDER BY inicio""", (ahora, hasta)):
        pr = md.prediccion(est, j["local"], j["visitante"], cfg, j["neutral"], j.get("qb_local"), j.get("qb_visitante"))
        ml = mk.moneyline(pr["mu_margen"], sig)
        h2h = _mercado(_ultimas_cuotas(conn, j["juego_id"]), "h2h", casas)
        p_loc = alfa * ml["local"] + (1 - alfa) * h2h["p_mercado"]["local"] if h2h else ml["local"]
        lado = "local" if p_loc >= 0.5 else "visitante"
        p = p_loc if lado == "local" else 1 - p_loc
        if p < p_min:
            continue
        eq = j[lado]
        loc_dt = j["inicio"].astimezone(zona)
        salida.append({"partido_id": f"nfl:{j['juego_id']}", "dia": str(loc_dt.date()),
                       "dia_texto": f"{('lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom')[loc_dt.weekday()]} {loc_dt.day:02d}/{loc_dt.month:02d}",
                       "hora": loc_dt.strftime("%H:%M"), "inicio": loc_dt.isoformat(), "liga": "🏈 NFL",
                       "partido": f"{NOMBRES.get(j['local'], j['local'])} vs {NOMBRES.get(j['visitante'], j['visitante'])}",
                       "tipo": "nfl_gana", "jugada": f"Gana {NOMBRES.get(eq, eq)}",
                       "detalle": "gana el juego (moneyline, incluye tiempo extra)" + ("" if lado == "local" else " · de visita"),
                       "p": p, "cuota_justa": 1 / p, "cuota_min": (1 + u) / p, "confianza": None,
                       "fuente": "modelo NFL + Las Vegas" if h2h else "modelo NFL (sin cuota)",
                       "cuota": h2h[lado]["cuota"] if h2h else None})
        cuotas = _ultimas_cuotas(conn, j["juego_id"])
        sp, tt = _mercado(cuotas, "spreads", casas), _mercado(cuotas, "totals", casas)
        linea_sp = sp["local"]["linea"] if sp else (-float(j["spread_cierre"]) if j["spread_cierre"] is not None else None)
        linea_t = tt["over"]["linea"] if tt else (float(j["total_cierre"]) if j["total_cierre"] is not None else None)
        if linea_sp is None or linea_t is None:
            continue
        base = salida[-1]
        for r in dv.recomendadas(dv.derivados(-float(linea_sp), float(linea_t)), NOMBRES.get(j["local"], j["local"]),
                                 NOMBRES.get(j["visitante"], j["visitante"]), -float(linea_sp)):
            if r["p"] >= p_min:
                salida.append({**base, "tipo": f"nfl_{r['tipo']}", "jugada": r["jugada"], "detalle": r["detalle"], "p": r["p"],
                               "cuota_justa": 1 / r["p"], "cuota_min": (1 + u) / r["p"], "cuota": None, "fuente": "línea de Las Vegas"})
    return salida
