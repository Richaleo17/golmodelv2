"""BD <-> motor NFL: carga de datos (nflverse + The Odds API) y predicciones de la semana para la API/PWA."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from golmodel.modelos.devig import quitar_margen
from golmodel.motor_nfl import mercados as mk
from golmodel.motor_nfl import modelo as md
from golmodel.motor_nfl.datos import NOMBRES, FuenteNFL, epa_por_juego

log = logging.getLogger(__name__)


def temporada_actual(ahora: datetime) -> int:
    return ahora.year if ahora.month >= 3 else ahora.year - 1


def actualizar(conn, fuente: FuenteNFL, cfg: dict, ahora: datetime) -> dict:
    """Calendario/marcadores (todas las temporadas pedidas), EPA (las que falten y la actual) y cuotas en vivo."""
    n = cfg["nfl"]
    actual = temporada_actual(ahora)
    temporadas = list(range(actual - int(n["temporadas_historico"]), actual + 1))
    res = {"juegos": 0, "epa": {}, "cuotas": 0, "avisos": []}
    filas = fuente.games(temporadas)
    for f in filas:
        conn.execute("""INSERT INTO nfl_juegos (juego_id, temporada, tipo, semana, inicio, local, visitante, puntos_local,
                            puntos_visitante, neutral, spread_cierre, total_cierre)
                        VALUES (%(juego_id)s, %(temporada)s, %(tipo)s, %(semana)s, %(inicio)s, %(local)s, %(visitante)s,
                                %(puntos_local)s, %(puntos_visitante)s, %(neutral)s, %(spread_cierre)s, %(total_cierre)s)
                        ON CONFLICT (juego_id) DO UPDATE SET inicio = EXCLUDED.inicio, puntos_local = EXCLUDED.puntos_local,
                            puntos_visitante = EXCLUDED.puntos_visitante, spread_cierre = EXCLUDED.spread_cierre,
                            total_cierre = EXCLUDED.total_cierre, actualizado_en = now()""", f)
    res["juegos"] = len(filas)
    conn.commit()
    if n.get("epa", {}).get("activo", True):
        con_epa = {r["temporada"] for r in conn.execute(
            "SELECT DISTINCT j.temporada FROM nfl_epa_juego e JOIN nfl_juegos j USING (juego_id)")}
        for t in temporadas:
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


def _estado(conn, cfg: dict) -> md.EstadoNFL:
    juegos = [md.Juego(r["juego_id"], r["temporada"], r["semana"], r["local"], r["visitante"], r["puntos_local"],
                       r["puntos_visitante"], r["neutral"])
              for r in conn.execute("SELECT * FROM nfl_juegos WHERE puntos_local IS NOT NULL")]
    epa: dict = defaultdict(dict)
    for r in conn.execute("SELECT juego_id, equipo, epa_of, epa_def, jugadas FROM nfl_epa_juego"):
        epa[r["juego_id"]][r["equipo"]] = (r["epa_of"], r["epa_def"], r["jugadas"])
    return md.construir_estado(juegos, cfg, epa)


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


def semana(conn, cfg: dict, ahora: datetime) -> dict:
    n = cfg["nfl"]
    est = _estado(conn, cfg)
    sig_m, sig_t = float(n["modelo"]["sigma_margen"]), float(n["modelo"]["sigma_total"])
    if est.n_juegos >= int(n["modelo"]["min_juegos_sigma"]):
        sig_m, sig_t = md.ajustar_sigmas(est)
    proximos = conn.execute("""SELECT * FROM nfl_juegos WHERE puntos_local IS NULL AND inicio >= %s AND inicio < %s
                               ORDER BY inicio""", (ahora - timedelta(hours=4), ahora + timedelta(days=int(n["dias"])))).fetchall()
    casas = list(n["casas_preferidas"])
    umbral = float(n["umbral_edge"])
    juegos = []
    for j in proximos:
        pr = md.prediccion(est, j["local"], j["visitante"], cfg, j["neutral"])
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
            f["valor"] = bool(f.get("p_mercado") is not None and f["p_modelo"] - f["p_mercado"] >= umbral and f.get("ev", -1) > 0)
        juegos.append(salida)
    return {"juegos": juegos, "n_juegos_historico": est.n_juegos, "sigma_margen": sig_m, "sigma_total": sig_t,
            "version": md.VERSION, "aviso": n["aviso"]}
