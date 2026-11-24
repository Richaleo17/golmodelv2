"""NBA «mercado primero»: actualizar (ESPN), semana (líneas, probabilidades, lesiones, descanso), dashboard y piernas para boletos.

La probabilidad sale de la línea del mercado (DraftKings vía ESPN) con la dispersión medida en el backtest; el modelo propio no se
usa porque no mejoró al mercado (modelo.py). Las lesiones y el descanso se MUESTRAN y avisan, pero no mueven la probabilidad: el
mercado ya los incorpora y no hay con qué validar un ajuste propio (el backtest no incluye lesiones).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from golmodel.motor_nba import modelo as M

log = logging.getLogger("golmodel.nba")
ET = ZoneInfo("America/New_York")
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

# Backtest walk-forward (scripts/backtest_nba.py; 3,944 juegos 2023-24 a 2025-26, línea de cierre de 9 casas). Referencia para comparar
# lo que la app vaya midiendo con sus propios juegos.
REFERENCIA_BACKTEST = {
    "juegos": 3944,
    "favorito_por_spread": [
        {"spread": "0-3", "gana": 0.527}, {"spread": "3-5", "gana": 0.596}, {"spread": "5-7", "gana": 0.658},
        {"spread": "7-9", "gana": 0.769}, {"spread": "9-12", "gana": 0.785}, {"spread": "12+", "gana": 0.893}],
    "sigma_margen": M.SIGMA_MARGEN, "sigma_total": M.SIGMA_TOTAL,
    "modelo_vs_mercado": "el modelo propio NO mejora a la línea de cierre (peso ≈ 0); apostar contra la línea cubrió 49-50% (−5 a −8% a −110)",
}
BUCKETS = ((0, 3), (3, 5), (5, 7), (7, 9), (9, 12), (12, 99))


# ------------------------------------------------------------------------------------------------------------------ actualizar
def actualizar(conn, fuente, cfg: dict, ahora: datetime) -> dict:
    """Calendario (ayer … +2 días, hora de Nueva York), líneas, resultados, lesiones y promedios de los lesionados que importan.
    Un fallo de ESPN se cuenta y se sigue. La línea se actualiza solo mientras el juego está programado: la última guardada es la de cierre."""
    n = cfg.get("nba") or {}
    res = {"juegos": 0, "con_linea": 0, "lesiones": 0, "jugadores": 0, "errores": []}
    hoy = ahora.astimezone(ET).date()
    vistos: dict[str, dict] = {}
    for k in range(-1, int(n.get("dias", 3))):
        try:
            for g in fuente.scoreboard(hoy + timedelta(days=k)):
                vistos[g["id"]] = g
        except Exception as e:                                           # noqa: BLE001
            res["errores"].append(f"calendario {k:+d}: {type(e).__name__}")
    for g in vistos.values():
        conn.execute("""
            INSERT INTO nba_juegos (espn_id, inicio, estado, local_id, local, local_abrev, visitante_id, visitante, visitante_abrev,
                                    goles_local, goles_visitante, spread_local, total, ml_local, ml_visitante, linea_proveedor,
                                    linea_capturada_en, actualizado_en)
            VALUES (%(id)s, %(inicio)s, %(estado)s, %(local_id)s, %(local)s, %(local_abrev)s, %(visitante_id)s, %(visitante)s, %(visitante_abrev)s,
                    %(goles_local)s, %(goles_visitante)s, %(spread_local)s, %(total)s, %(ml_local)s, %(ml_visitante)s, %(linea_proveedor)s,
                    CASE WHEN %(spread_local)s::float8 IS NOT NULL THEN %(ahora)s::timestamptz END, %(ahora)s)
            ON CONFLICT (espn_id) DO UPDATE SET
                inicio = EXCLUDED.inicio, estado = EXCLUDED.estado, goles_local = EXCLUDED.goles_local,
                goles_visitante = EXCLUDED.goles_visitante, actualizado_en = EXCLUDED.actualizado_en,
                spread_local = CASE WHEN nba_juegos.estado = 'programado' AND EXCLUDED.estado = 'programado' AND EXCLUDED.spread_local IS NOT NULL
                                    THEN EXCLUDED.spread_local ELSE nba_juegos.spread_local END,
                total = CASE WHEN nba_juegos.estado = 'programado' AND EXCLUDED.estado = 'programado' AND EXCLUDED.total IS NOT NULL
                             THEN EXCLUDED.total ELSE nba_juegos.total END,
                ml_local = CASE WHEN nba_juegos.estado = 'programado' AND EXCLUDED.estado = 'programado' AND EXCLUDED.ml_local IS NOT NULL
                                THEN EXCLUDED.ml_local ELSE nba_juegos.ml_local END,
                ml_visitante = CASE WHEN nba_juegos.estado = 'programado' AND EXCLUDED.estado = 'programado' AND EXCLUDED.ml_visitante IS NOT NULL
                                    THEN EXCLUDED.ml_visitante ELSE nba_juegos.ml_visitante END,
                linea_proveedor = CASE WHEN nba_juegos.estado = 'programado' AND EXCLUDED.estado = 'programado' AND EXCLUDED.spread_local IS NOT NULL
                                       THEN EXCLUDED.linea_proveedor ELSE nba_juegos.linea_proveedor END,
                linea_capturada_en = CASE WHEN nba_juegos.estado = 'programado' AND EXCLUDED.estado = 'programado' AND EXCLUDED.spread_local IS NOT NULL
                                          THEN EXCLUDED.linea_capturada_en ELSE nba_juegos.linea_capturada_en END""", {**g, "ahora": ahora})
        res["juegos"] += 1
        res["con_linea"] += g["spread_local"] is not None
    try:
        lesiones = fuente.lesiones()
    except Exception as e:                                               # noqa: BLE001
        lesiones = None
        res["errores"].append(f"lesiones: {type(e).__name__}")
    if lesiones is not None:                                             # foto completa: se reemplaza (no se borra nada si ESPN falló)
        with conn.transaction():
            conn.execute("DELETE FROM nba_lesiones")
            for b in lesiones:
                conn.execute("""INSERT INTO nba_lesiones (equipo_id, jugador_id, equipo, jugador, estado, comentario, fecha, capturado_en)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (equipo_id, jugador_id) DO NOTHING""",
                             (b["equipo_id"], b["jugador_id"], b["equipo"], b["jugador"], b["estado"], b["comentario"], b["fecha"], ahora))
                res["lesiones"] += 1
    # promedios de los lesionados de equipos que juegan en las próximas 48 h (se piden una vez al día)
    pend = conn.execute("""
        SELECT DISTINCT l.jugador_id FROM nba_lesiones l
        WHERE l.equipo_id IN (SELECT local_id FROM nba_juegos WHERE estado = 'programado' AND inicio < %(h)s
                              UNION SELECT visitante_id FROM nba_juegos WHERE estado = 'programado' AND inicio < %(h)s)
          AND NOT EXISTS (SELECT 1 FROM nba_jugadores j WHERE j.jugador_id = l.jugador_id AND j.actualizado_en > %(v)s)
        LIMIT %(m)s""", {"h": ahora + timedelta(hours=48), "v": ahora - timedelta(hours=24), "m": int(n.get("max_jugadores", 80))}).fetchall()
    for r in pend:
        try:
            j = fuente.jugador(r["jugador_id"])
        except Exception:                                                # noqa: BLE001
            res["errores"].append("jugador")
            continue
        if j:
            conn.execute("""INSERT INTO nba_jugadores (jugador_id, partidos, minutos, puntos, actualizado_en) VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (jugador_id) DO UPDATE SET partidos = EXCLUDED.partidos, minutos = EXCLUDED.minutos,
                                                                   puntos = EXCLUDED.puntos, actualizado_en = EXCLUDED.actualizado_en""",
                         (r["jugador_id"], j["partidos"], j["min"], j["pts"], ahora))
            res["jugadores"] += 1
    res["errores"] = res["errores"][:10]
    return res


# ---------------------------------------------------------------------------------------------------------------------- semana
def _bajas_por_equipo(conn) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in conn.execute("""SELECT l.equipo_id, l.jugador, l.estado, l.comentario, l.fecha, j.minutos, j.puntos, j.partidos
                             FROM nba_lesiones l LEFT JOIN nba_jugadores j ON j.jugador_id = l.jugador_id
                             ORDER BY (l.estado = 'Out') DESC, j.puntos DESC NULLS LAST""").fetchall():
        out.setdefault(r["equipo_id"], []).append({
            "jugador": r["jugador"], "estado": r["estado"], "comentario": r["comentario"], "min": r["minutos"], "pts": r["puntos"],
            "fecha": r["fecha"].isoformat() if r["fecha"] else None, "clave": M.jugador_clave(r["minutos"], r["puntos"])})
    return out


def _juega_ayer(conn, juego: dict, cache: dict) -> dict[str, bool]:
    """Back-to-back: el equipo jugó el día calendario anterior (hora de Nueva York)."""
    dia = juego["inicio"].astimezone(ET).date()
    out = {}
    for lado, eid in (("local", juego["local_id"]), ("visitante", juego["visitante_id"])):
        k = (eid, dia)
        if k not in cache:
            fila = conn.execute("""SELECT 1 FROM nba_juegos WHERE (local_id = %(e)s OR visitante_id = %(e)s) AND espn_id <> %(g)s
                                   AND estado <> 'cancelado' AND (inicio AT TIME ZONE 'America/New_York')::date = %(d)s LIMIT 1""",
                                {"e": eid, "g": juego["espn_id"], "d": dia - timedelta(days=1)}).fetchone()
            cache[k] = fila is not None
        out[lado] = cache[k]
    return out


def _recomendadas(j: dict, esc: dict, mu: float, total: float | None, der: dict | None = None) -> list[dict]:
    """Jugadas con buena probabilidad de este partido (la línea del mercado, la alternativa ≈ 85% y el total alternativo ≈ 85%)."""
    fav_local = mu >= 0
    out = []
    p_fav = M.p_gana_local(-mu) if fav_local else 1 - M.p_gana_local(-mu)
    out.append({"tipo": "ml", "jugada": f"Gana {j['local'] if fav_local else j['visitante']}", "p": round(p_fav, 4)})
    # El favorito CON puntos a favor (p. ej. «Atlanta +6.5»): siempre cae dentro de la escalera; el lado del underdog necesitaría líneas de +20.
    alt = M.mas_cercana(esc["spreads_local" if fav_local else "spreads_visitante"], "p", 0.85, 0.75)
    if alt:
        eq = j["local"] if fav_local else j["visitante"]
        out.append({"tipo": "spread_alt", "jugada": f"{eq} {alt['linea']:+.1f}", "p": alt["p"]})
    if total is not None:
        for clave, txt in (("p_over", "Más de"), ("p_under", "Menos de")):
            t = M.mas_cercana(esc["totales"], clave, 0.85, 0.75)
            if t:
                out.append({"tipo": "total_alt", "jugada": f"{txt} {t['linea']:.1f} puntos", "p": t[clave]})
    if der:
        fav = "local" if fav_local else "visitante"
        nombre = j["local"] if fav_local else j["visitante"]
        e = M.mas_cercana(der["equipos"][fav]["lineas"], "p_over", 0.85, 0.75)                 # el favorito anota al menos N
        if e:
            out.append({"tipo": "equipo_total", "jugada": f"{nombre}: más de {e['linea']:.1f} puntos", "p": e["p_over"]})
        mt = M.mas_cercana(der["1T"]["spreads_local" if fav_local else "spreads_visitante"], "p", 0.85, 0.75)
        if mt:
            out.append({"tipo": "mitad_spread", "jugada": f"1.ª mitad: {nombre} {mt['linea']:+.1f}", "p": mt["p"]})
    return out


def semana(conn, cfg: dict, ahora: datetime, dias: int | None = None) -> dict:
    n = cfg.get("nba") or {}
    dias = dias or int(n.get("dias", 3))
    zona = ZoneInfo(cfg["zona_horaria"])
    bajas = _bajas_por_equipo(conn)
    cache: dict = {}
    juegos = []
    for g in conn.execute("""SELECT * FROM nba_juegos WHERE estado IN ('programado', 'en_juego') AND inicio > %s AND inicio < %s ORDER BY inicio""",
                          (ahora - timedelta(hours=4), ahora + timedelta(days=dias))).fetchall():
        sp, tot = g["spread_local"], g["total"]
        item = {"id": g["espn_id"], "inicio": g["inicio"].isoformat(), "estado": g["estado"],
                "dia_texto": f"{DIAS[g['inicio'].astimezone(zona).weekday()]} {g['inicio'].astimezone(zona):%d/%m}",
                "hora": g["inicio"].astimezone(zona).strftime("%H:%M"),
                "local": {"nombre": g["local"], "abrev": g["local_abrev"]}, "visitante": {"nombre": g["visitante"], "abrev": g["visitante_abrev"]},
                "marcador": None if g["goles_local"] is None else f"{g['goles_local']}-{g['goles_visitante']}",
                "linea": {"spread_local": sp, "total": tot, "ml_local": g["ml_local"], "ml_visitante": g["ml_visitante"],
                          "proveedor": g["linea_proveedor"], "capturada_en": g["linea_capturada_en"].isoformat() if g["linea_capturada_en"] else None},
                "descanso": _juega_ayer(conn, g, cache), "avisos": []}
        niveles = {}
        item["lesiones"] = {}
        for lado, eid in (("local", g["local_id"]), ("visitante", g["visitante_id"])):
            lista = bajas.get(eid, [])
            nivel = M.nivel_lesiones(lista)
            niveles[lado] = nivel
            item["lesiones"][lado] = {"nivel": nivel, "bajas": lista[:8]}
            if nivel in ("medio", "alto"):
                clave = [b["jugador"] for b in lista if b["clave"] and b["estado"] == "Out"] or [b["jugador"] for b in lista if b["clave"]]
                item["avisos"].append(f"{g['local'] if lado == 'local' else g['visitante']}: {'baja importante' if nivel == 'alto' else 'baja a vigilar'} "
                                      f"({', '.join(clave[:3])})")
        if item["descanso"]["local"] or item["descanso"]["visitante"]:
            item["avisos"].append("Back-to-back: " + " y ".join(g[x] for x, k in (("local", "local"), ("visitante", "visitante")) if item["descanso"][k]))
        item["nivel_lesiones"] = "alto" if "alto" in niveles.values() else ("medio" if "medio" in niveles.values() else "bajo")
        if sp is not None:
            mu = -sp
            pl = M.p_gana_local(sp)
            ml = M.sin_margen(g["ml_local"], g["ml_visitante"])
            esc = M.escalera(sp, tot)
            item["prob"] = {"gana_local": round(pl, 4), "gana_visitante": round(1 - pl, 4), "margen_esperado": round(mu, 1), "total_esperado": tot,
                            "ml_mercado_local": round(ml[0], 4) if ml else None,
                            "dif_ml_vs_spread": round(ml[0] - pl, 4) if ml else None}
            item["favorito"] = {"nombre": g["local"] if mu >= 0 else g["visitante"], "p": round(max(pl, 1 - pl), 4)}
            item["escalera"] = esc
            item["derivados"] = M.derivados(sp, tot) if tot is not None else None
            item["recomendadas"] = _recomendadas(g, esc, mu, tot, item["derivados"])
        else:
            item["avisos"].append("Sin línea todavía (las casas la publican uno o dos días antes).")
        juegos.append(item)
    ult = conn.execute("SELECT max(actualizado_en) AS t FROM nba_juegos").fetchone()["t"]
    return {"juegos": juegos, "actualizado_en": ult.isoformat() if ult else None, "metodo": "mercado primero",
            "nota": ("La probabilidad sale de la línea de DraftKings con la dispersión medida en 3 temporadas (σ margen 13.3, σ total 17.8). "
                     "El modelo propio no mejoró al mercado, así que no se usa. Lesiones y descanso avisan, no cambian el número."),
            "referencia": REFERENCIA_BACKTEST}


# ------------------------------------------------------------------------------------------------------------------ dashboard
def dashboard(conn, cfg: dict, ahora: datetime) -> dict:
    """Lo que la app lleva midiendo con sus propios juegos (línea de cierre guardada vs resultado), junto al backtest de referencia."""
    filas = conn.execute("""SELECT spread_local, total, goles_local, goles_visitante FROM nba_juegos
                            WHERE estado = 'finalizado' AND spread_local IS NOT NULL AND goles_local IS NOT NULL
                              AND inicio > %s""", (ahora - timedelta(days=200),)).fetchall()
    out = {"n": len(filas), "referencia": REFERENCIA_BACKTEST, "buckets": [], "mae_margen": None, "mae_total": None, "acierto_favorito": None}
    if not filas:
        return out
    gan, prom, err_m, err_t, nt = 0, 0.0, 0.0, 0.0, 0
    por = {b: [0, 0, 0.0] for b in BUCKETS}
    for f in filas:
        margen = f["goles_local"] - f["goles_visitante"]
        mu = -f["spread_local"]
        fav_gana = (margen > 0) if mu >= 0 else (margen < 0)
        p_fav = max(M.p_gana_local(f["spread_local"]), 1 - M.p_gana_local(f["spread_local"]))
        gan += fav_gana
        prom += p_fav
        err_m += abs(margen - mu)
        if f["total"] is not None:
            err_t += abs(f["goles_local"] + f["goles_visitante"] - f["total"])
            nt += 1
        for b in BUCKETS:
            if b[0] <= abs(mu) < b[1]:
                por[b][0] += 1
                por[b][1] += fav_gana
                por[b][2] += p_fav
    out["acierto_favorito"] = round(gan / len(filas), 4)
    out["prometido_favorito"] = round(prom / len(filas), 4)
    out["mae_margen"] = round(err_m / len(filas), 2)
    out["mae_total"] = round(err_t / nt, 2) if nt else None
    out["buckets"] = [{"spread": f"{a}-{b}" if b < 99 else f"{a}+", "n": v[0], "gana": round(v[1] / v[0], 4), "prometido": round(v[2] / v[0], 4)}
                      for (a, b), v in por.items() if v[0]]
    return out


# ------------------------------------------------------------------------------------------------------------ piernas (Boletos)
def piernas_parlay(conn, cfg: dict, ahora: datetime, hasta: datetime, p_min: float) -> list[dict]:
    """Piernas NBA para «Qué meter» / «Soñadoras» / Boletos de 7: favorito (moneyline), spread alternativo ≈ 85% y total alternativo ≈ 85%.
    Se omiten los partidos con una baja importante (la línea puede ir atrasada)."""
    u = float(cfg["valor"]["umbral_edge"])
    dias = max(1, (hasta - ahora).days + 1)
    salida = []
    for g in semana(conn, cfg, ahora, dias=dias)["juegos"]:
        inicio = datetime.fromisoformat(g["inicio"])
        if g["estado"] != "programado" or inicio <= ahora or inicio >= hasta or "recomendadas" not in g or g["nivel_lesiones"] == "alto":
            continue
        for r in g["recomendadas"]:
            if r["p"] < p_min:
                continue
            cuota = None
            if r["tipo"] == "ml":
                cuota = g["linea"]["ml_local"] if r["jugada"].endswith(g["local"]["nombre"]) else g["linea"]["ml_visitante"]
            salida.append({"partido_id": f"nba:{g['id']}", "dia": inicio.date().isoformat(), "dia_texto": g["dia_texto"], "hora": g["hora"],
                           "inicio": g["inicio"], "liga": "🏀 NBA", "partido": f"{g['local']['nombre']} vs {g['visitante']['nombre']}",
                           "tipo": f"nba_{r['tipo']}", "jugada": r["jugada"],
                           "detalle": {"ml": "gana el partido", "spread_alt": "cubre el spread alternativo (puede perder por menos de los puntos que se le dan)",
                                       "total_alt": "total de puntos del partido", "equipo_total": "puntos de ese equipo en el partido",
                                       "mitad_spread": "spread de la primera mitad"}[r["tipo"]],
                           "p": r["p"], "cuota_justa": 1 / r["p"], "cuota_min": (1 + u) / r["p"], "confianza": None,
                           "fuente": "línea de DraftKings", "cuota": cuota})
    return salida
