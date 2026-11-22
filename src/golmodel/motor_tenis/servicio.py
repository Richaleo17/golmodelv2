"""BD <-> motor de tenis: carga (tennis-data.co.uk + The Odds API), pronósticos de los próximos partidos, efectividad y
piernas para «Qué meter» / «Soñadoras»."""
from __future__ import annotations

import gc
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from golmodel.motor_tenis import modelo as md
from golmodel.motor_tenis.datos import FuenteTenis, _plano, claves_nombre_completo, resolver, resultados_espn

log = logging.getLogger(__name__)
_CACHE: dict = {}


_INSERTA = """INSERT INTO tenis_partidos (id, tour, fecha, torneo, superficie, ronda, best_of, ganador, perdedor,
                    nombre_g, nombre_p, rank_g, rank_p, comentario, orden, cuota_g, cuota_p)
                VALUES (%(id)s, %(tour)s, %(fecha)s, %(torneo)s, %(superficie)s, %(ronda)s, %(best_of)s, %(ganador)s,
                    %(perdedor)s, %(nombre_g)s, %(nombre_p)s, %(rank_g)s, %(rank_p)s, %(comentario)s, %(orden)s,
                    %(cuota_g)s, %(cuota_p)s)
                ON CONFLICT (id) DO UPDATE SET cuota_g = EXCLUDED.cuota_g, cuota_p = EXCLUDED.cuota_p, comentario = EXCLUDED.comentario"""


def actualizar(conn, fuente: FuenteTenis, cfg: dict, ahora: datetime, momios: bool = True) -> dict:
    """Histórico + resultados recientes + partidos próximos. `momios=False` no gasta créditos de The Odds API (solo ESPN y
    LiveScore, gratis): así se puede correr con cada actualización de cuotas para que el calendario esté al día."""
    t = cfg["tenis"]
    res = {"partidos": {}, "eventos": 0, "torneos": 0, "avisos": []}
    anios = list(range(ahora.year - int(t["temporadas_historico"]), ahora.year + 1))
    con_datos = {(r["tour"], r["anio"]) for r in conn.execute(
        "SELECT DISTINCT tour, extract(year FROM fecha)::int AS anio FROM tenis_partidos WHERE id NOT LIKE 'e:%%' AND id NOT LIKE 'c:%%'")}
    ch = t.get("challenger") or {}
    if ch.get("activo") and "atp" in t["circuitos"]:
        con_ch = {r["anio"] for r in conn.execute(
            "SELECT DISTINCT extract(year FROM fecha)::int AS anio FROM tenis_partidos WHERE id LIKE 'c:%%'")}
        for a in range(ahora.year - int(ch.get("temporadas", 3)), ahora.year + 1):
            if a in con_ch and a < ahora.year:
                continue
            try:
                filas = fuente.temporada_challenger(a)
            except Exception as e:                                 # noqa: BLE001
                res["avisos"].append(f"Challenger {a}: no disponible ({type(e).__name__}).")
                continue
            for f in filas:
                conn.execute(_INSERTA, f)
            res["partidos"][f"challenger{a}"] = len(filas)
            conn.commit()
            del filas
            gc.collect()
    for tour in t["circuitos"]:
        for a in anios:
            if (tour, a) in con_datos and a < ahora.year:
                continue                                          # temporadas cerradas: se bajan una sola vez
            try:
                filas = fuente.temporada(a, tour)
            except Exception as e:                                 # noqa: BLE001 — la temporada en curso puede no existir aún
                res["avisos"].append(f"tennis-data {tour.upper()} {a}: no disponible ({type(e).__name__}).")
                continue
            for f in filas:
                conn.execute(_INSERTA, f)
            res["partidos"][f"{tour}{a}"] = len(filas)
            conn.commit()
            del filas
            gc.collect()
    try:
        torneos = fuente.torneos_activos()[: int(t["cuotas"]["max_torneos"])] if momios else []
    except Exception as e:                                         # noqa: BLE001
        torneos = []
        res["avisos"].append(f"Torneos de tenis: no disponibles ({type(e).__name__}).")
    for sp in torneos:
        try:
            evs = fuente.eventos(sp)
        except Exception as e:                                     # noqa: BLE001
            res["avisos"].append(f"{sp.get('title')}: sin momios ({type(e).__name__}).")
            continue
        res["torneos"] += 1
        for ev in evs:
            conn.execute("""INSERT INTO tenis_eventos (odds_api_id, tour, sport_key, torneo, inicio, jugador1, jugador2, cuota1, cuota2,
                                casa, best_of, capturada_en)
                            VALUES (%(odds_api_id)s, %(tour)s, %(sport_key)s, %(torneo)s, %(inicio)s, %(jugador1)s, %(jugador2)s,
                                %(cuota1)s, %(cuota2)s, %(casa)s, %(best_of)s, %(capturada_en)s)
                            ON CONFLICT (odds_api_id) DO UPDATE SET inicio = EXCLUDED.inicio, cuota1 = EXCLUDED.cuota1,
                                cuota2 = EXCLUDED.cuota2, casa = EXCLUDED.casa, capturada_en = EXCLUDED.capturada_en""", ev)
            res["eventos"] += 1
    if t.get("espn", True):
        try:
            _espn(conn, fuente, cfg, ahora, res)
        except Exception as e:                                     # noqa: BLE001 — ESPN es complemento: nunca tumba la carga
            conn.rollback()
            res["avisos"].append(f"ESPN tenis: no disponible ({type(e).__name__}).")
    conn.execute("DELETE FROM tenis_eventos WHERE inicio < %s", (ahora - timedelta(days=3),))
    conn.commit()
    return res


def _livescore(conn, fuente, cfg: dict, ahora: datetime, res: dict, vistos: dict) -> None:
    """Challenger (LiveScore, un día por llamada): resultados desde una semana después del corte del histórico de Challenger, y los
    próximos días. Agrega sus renglones a `vistos` (ids «l…»), que luego se guardan igual que los de ESPN."""
    t = cfg["tenis"]
    ch = t.get("challenger") or {}
    if not ch.get("activo") or not ch.get("livescore", True):
        return
    r = conn.execute("""SELECT (SELECT max(fecha) FROM tenis_partidos WHERE id LIKE 'c:%%') AS corte,
                               (SELECT max(fecha) FROM tenis_partidos WHERE id LIKE 'e:l%%') AS ult""").fetchone()
    hoy = ahora.date()
    if r["corte"] is None:
        d = hoy
    else:
        d = max(r["corte"] + timedelta(days=7), (r["ult"] or r["corte"]) - timedelta(days=int(t.get("espn_reprocesar_dias", 10))))
    comps = list(ch.get("competencias") or ["ATP Challenger"])
    fallas = 0
    while d <= hoy + timedelta(days=int(t["dias"])):
        try:
            for f in fuente.livescore(d, comps):
                vistos[f["id"]] = f
        except Exception as e:                                     # noqa: BLE001
            fallas += 1
            if fallas <= 3:
                res["avisos"].append(f"LiveScore {d}: {type(e).__name__}")
            if fallas >= 5:                                        # la fuente no responde: no insistir día por día
                res["avisos"].append("LiveScore: se dejó de consultar tras 5 fallas.")
                return
        d += timedelta(days=1)


def _superficies_hist(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT DISTINCT ON (torneo) torneo, superficie FROM tenis_partidos WHERE id NOT LIKE 'e:%%' ORDER BY torneo, fecha DESC"):
        out[_plano(r["torneo"])] = md.superficie_norm(r["superficie"])
    return out


def _espn(conn, fuente, cfg: dict, ahora: datetime, res: dict) -> None:
    """ESPN completa lo que el histórico (Sackmann, congelado) no trae: resultados desde su última fecha hasta hoy, y los partidos de
    los próximos días aunque no tengan momio (salen «solo modelo»). Un torneo que empezó antes del corte ya está en el histórico."""
    t = cfg["tenis"]
    hist = _superficies_hist(conn)
    sup = lambda torneo: superficie_de(torneo, hist, cfg)            # noqa: E731
    hoy = ahora.date()
    fechas: set = set()
    cortes = {}
    for tour in t["circuitos"]:
        r = conn.execute("""SELECT (SELECT max(fecha) FROM tenis_partidos WHERE tour = %s AND id NOT LIKE 'e:%%' AND id NOT LIKE 'c:%%') AS corte,
                                   (SELECT max(fecha) FROM tenis_partidos WHERE tour = %s AND id LIKE 'e:%%' AND id NOT LIKE 'e:l%%') AS ult""",
                         (tour, tour)).fetchone()
        if r["corte"] is None:
            continue
        cortes[tour] = r["corte"]
        d = max(r["corte"], (r["ult"] or r["corte"]) - timedelta(days=int(t.get("espn_reprocesar_dias", 10))))
        d += timedelta(days=(2 - d.weekday()) % 7)                 # miércoles: todo torneo de una semana está en juego ese día
        while d < hoy:                                             # un día por semana basta: el scoreboard trae el cuadro completo
            fechas.add((tour, d))
            d += timedelta(days=7)
    for tour in t["circuitos"]:
        for k in range(0, int(t["dias"]) + 1, 2):
            fechas.add((tour, hoy + timedelta(days=k)))
    vistos: dict = {}
    for tour, d in sorted(fechas, key=lambda x: (x[1], x[0])):
        try:
            for f in fuente.espn(tour, d):
                vistos[f["id"]] = f
        except Exception as e:                                     # noqa: BLE001
            res["avisos"].append(f"ESPN {tour.upper()} {d}: {type(e).__name__}")
    _livescore(conn, fuente, cfg, ahora, res, vistos)
    filas = list(vistos.values())
    cortes_ch = {"atp": conn.execute("SELECT max(fecha) AS f FROM tenis_partidos WHERE id LIKE 'c:%%'").fetchone()["f"]}

    def _despues_del_corte(f) -> bool:
        cs = cortes_ch if f["id"].startswith("e:l") else cortes
        return cs.get(f["tour"]) is not None and (vistos[f["id"][2:]]["evento_inicio"] or "0000") > cs[f["tour"]].isoformat()
    nuevos = [f for f in resultados_espn(filas, sup) if _despues_del_corte(f)]
    for f in nuevos:
        conn.execute("""INSERT INTO tenis_partidos (id, tour, fecha, torneo, superficie, ronda, best_of, ganador, perdedor,
                            nombre_g, nombre_p, rank_g, rank_p, comentario, orden, cuota_g, cuota_p)
                        VALUES (%(id)s, %(tour)s, %(fecha)s, %(torneo)s, %(superficie)s, %(ronda)s, %(best_of)s, %(ganador)s,
                            %(perdedor)s, %(nombre_g)s, %(nombre_p)s, %(rank_g)s, %(rank_p)s, %(comentario)s, %(orden)s, NULL, NULL)
                        ON CONFLICT (id) DO NOTHING""", f)
    res["espn_resultados"] = len(nuevos)
    # partidos por jugar: los que ya trajo The Odds API (mismos jugadores, ±36 h) no se duplican
    con_momio = {}
    for r in conn.execute("SELECT jugador1, jugador2, inicio FROM tenis_eventos WHERE odds_api_id NOT LIKE 'espn:%%' AND inicio > %s",
                          (ahora - timedelta(days=1),)):
        par = frozenset((claves_nombre_completo(r["jugador1"])[0], claves_nombre_completo(r["jugador2"])[0]))
        con_momio.setdefault(par, []).append(r["inicio"])
    n = 0
    for f in filas:
        if f["estado"] != "pre" or not (ahora - timedelta(hours=3) < f["inicio"] < ahora + timedelta(days=int(t["dias"]) + 1)):
            continue
        par = frozenset((claves_nombre_completo(f["jugador1"])[0], claves_nombre_completo(f["jugador2"])[0]))
        if any(abs((i - f["inicio"]).total_seconds()) < 36 * 3600 for i in con_momio.get(par, [])):
            continue
        conn.execute("""INSERT INTO tenis_eventos (odds_api_id, tour, sport_key, torneo, inicio, jugador1, jugador2, cuota1, cuota2, casa,
                            best_of, capturada_en)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, %s, %s)
                        ON CONFLICT (odds_api_id) DO UPDATE SET inicio = EXCLUDED.inicio, capturada_en = EXCLUDED.capturada_en""",
                     (f"espn:{f['id']}", f["tour"], "livescore" if f["id"].startswith("l") else "espn", f["torneo"], f["inicio"],
                      f["jugador1"], f["jugador2"], f["best_of"], ahora))
        n += 1
    # lo que ESPN ya dio por jugado (o cambió de fecha fuera de la ventana) no se queda como «por jugar»
    terminados = [f"espn:{f['id']}" for f in filas if f["estado"] != "pre"]
    if terminados:
        conn.execute("DELETE FROM tenis_eventos WHERE odds_api_id = ANY(%s)", (terminados,))
    res["espn_proximos"] = n
    conn.commit()


def _estado(conn, cfg: dict):
    firma = conn.execute("""SELECT (SELECT count(*) FROM tenis_partidos) AS n, (SELECT max(fecha) FROM tenis_partidos) AS f,
                                   current_database() AS db""").fetchone()
    clave = (firma["db"], firma["n"], str(firma["f"]), repr(sorted(cfg["tenis"]["modelo"].items())))
    if _CACHE.get("clave") != clave:
        filas = conn.execute("""SELECT id, tour, fecha, superficie, ganador, perdedor, best_of, orden, cuota_g, cuota_p, torneo
                                FROM tenis_partidos ORDER BY fecha, orden""").fetchall()
        partidos = [md.Partido(r["tour"], r["fecha"].isoformat(), r["superficie"], r["ganador"], r["perdedor"], int(r["best_of"] or 3),
                               int(r["orden"] or 0), r["id"]) for r in filas]
        est, reg = md.construir(partidos, cfg, desde_eval="0000")
        mercado = {}
        for r in filas:
            if r["cuota_g"] and r["cuota_p"]:
                a, b = 1 / float(r["cuota_g"]), 1 / float(r["cuota_p"])
                mercado[r["id"]] = a / (a + b)
        torneos = {}
        for r in filas:                                            # superficie más reciente de cada torneo
            torneos[_plano(r["torneo"])] = md.superficie_norm(r["superficie"])
        conocidos = {}
        for r in filas:
            conocidos.setdefault(r["tour"], set()).update((r["ganador"], r["perdedor"]))
        _CACHE.update(clave=clave, valor=(est, reg, mercado, torneos, conocidos))
    return _CACHE["valor"]


def superficie_de(torneo: str, torneos: dict, cfg: dict) -> str:
    """Superficie de un torneo de The Odds API: reglas de config por palabra clave, luego el torneo más parecido del histórico."""
    t = _plano(torneo)
    for palabra, sup in (cfg["tenis"].get("superficies") or {}).items():
        if re.search(rf"\b{re.escape(_plano(palabra))}\b", t):       # palabra completa: «halle» no debe pegar en «challenger»
            return sup
    ruido = {"atp", "wta", "open", "masters", "championships", "international", "tennis", "cup", "the", "de", "singles", "challenger", "ch"}
    tok = set(t.split()) - ruido
    mejor, puntos = "Hard", 0
    for nombre, sup in torneos.items():
        comun = len(tok & (set(nombre.split()) - ruido))
        if comun > puntos:
            mejor, puntos = sup, comun
    return mejor


def _nombre(clave: str, original: str | None = None) -> str:
    return original or clave.title()


def pronosticos(conn, cfg: dict, ahora: datetime, dias: int | None = None) -> list[dict]:
    t = cfg["tenis"]
    est, _, _, torneos, conocidos = _estado(conn, cfg)
    alfa = float(t.get("peso_modelo", 0.3))
    min_n = int(t.get("min_partidos_modelo", 15))
    zona = ZoneInfo(cfg["zona_horaria"])
    hasta = ahora + timedelta(days=int(dias or t["dias"]))
    salida = []
    for e in conn.execute("SELECT * FROM tenis_eventos WHERE inicio > %s AND inicio < %s ORDER BY inicio",
                          (ahora - timedelta(hours=3), hasta)):
        tour = e["tour"]
        k1 = resolver(e["jugador1"], conocidos.get(tour, set()))
        k2 = resolver(e["jugador2"], conocidos.get(tour, set()))
        sup = superficie_de(e["torneo"], torneos, cfg)
        n1, n2 = est.n_general.get((tour, k1), 0), est.n_general.get((tour, k2), 0)
        p_mod = md.prob(est, tour, k1, k2, sup, cfg, int(e["best_of"] or 3))
        p_mk = None
        if e["cuota1"] and e["cuota2"]:
            a, b = 1 / float(e["cuota1"]), 1 / float(e["cuota2"])
            p_mk = a / (a + b)
        conoce = min(n1, n2) >= min_n
        if p_mk is None:
            p_fin = p_mod if conoce else None
        else:
            p_fin = alfa * p_mod + (1 - alfa) * p_mk if conoce else p_mk
        inicio = e["inicio"].astimezone(zona)
        salida.append({"id": e["odds_api_id"], "tour": tour, "torneo": e["torneo"], "superficie": sup, "best_of": e["best_of"],
                       "inicio": inicio.isoformat(), "jugador1": e["jugador1"], "jugador2": e["jugador2"],
                       "elo1": round(est.general.get((tour, k1), 1500.0)), "elo2": round(est.general.get((tour, k2), 1500.0)),
                       "partidos1": n1, "partidos2": n2, "p_modelo": p_mod, "p_mercado": p_mk, "p_final": p_fin,
                       "cuota1": float(e["cuota1"]) if e["cuota1"] else None, "cuota2": float(e["cuota2"]) if e["cuota2"] else None,
                       "casa": e["casa"], "modelo_confiable": conoce,
                       "sets": md.mercados_sets(p_fin, int(e["best_of"] or 3)) if p_fin is not None else None,
                       "fuente": "modelo + mercado" if (conoce and p_mk is not None) else ("solo mercado" if p_mk is not None else "solo modelo")})
    return salida


def _curva(reg: list[dict], mercado: dict) -> list[dict]:
    """Acierto acumulado semana a semana (ISO) de la temporada."""
    out, acum = [], []
    semanas = sorted({datetime.fromisoformat(r["fecha"]).isocalendar()[:2] for r in reg})
    for s in semanas:
        sem = [r for r in reg if datetime.fromisoformat(r["fecha"]).isocalendar()[:2] == s]
        acum += sem
        m, ms = md.metricas(acum, mercado), md.metricas(sem, mercado)
        out.append({"semana": f"{s[0]}-S{s[1]:02d}", "record": f"{ms['aciertos']}-{ms['partidos'] - ms['aciertos']}",
                    "pct_semana": ms["pct_aciertos"], "pct_acum": m["pct_aciertos"],
                    "vegas_acum": (m.get("mercado") or {}).get("pct_aciertos")})
    return out


def semana(conn, cfg: dict, ahora: datetime) -> dict:
    est, reg, mercado, _, _ = _estado(conn, cfg)
    actual = str(ahora.year)
    desde_activos = (ahora - timedelta(days=200)).date().isoformat()
    temporada = [r for r in reg if r["fecha"].startswith(actual)]
    return {"partidos": pronosticos(conn, cfg, ahora), "version": md.VERSION, "aviso": cfg["tenis"]["aviso"],
            "n_historico": est.n_partidos, "peso_modelo": float(cfg["tenis"].get("peso_modelo", 0.3)),
            "ranking": {t: {"general": md.ranking(est, t, cfg, desde=desde_activos),
                            **{s.lower(): md.ranking(est, t, cfg, superficie=s, desde=desde_activos, n=10) for s in md.SUPERFICIES}}
                        for t in cfg["tenis"]["circuitos"]},
            "desempeno": {t: md.metricas([r for r in temporada if r["tour"] == t], mercado) for t in cfg["tenis"]["circuitos"]}}


def dashboard(conn, cfg: dict, ahora: datetime) -> dict:
    _, reg, mercado, _, _ = _estado(conn, cfg)
    actual, previa = str(ahora.year), str(ahora.year - 1)
    temp = [r for r in reg if r["fecha"].startswith(actual)]
    pasada = [r for r in reg if r["fecha"].startswith(previa)]
    curva = _curva(temp, mercado)
    delta = curva[-1]["pct_acum"] - curva[-2]["pct_acum"] if len(curva) >= 2 else None
    mp = md.metricas(pasada, mercado)
    m = md.metricas(temp, mercado)
    if m.get("partidos"):
        m["record"] = f"{m['aciertos']}-{m['partidos'] - m['aciertos']}"
    if m.get("mercado"):
        m["vegas"] = {"pct_aciertos": m["mercado"]["pct_aciertos"], "brier": m["mercado"]["brier"]}
    return {"temporada": ahora.year, "desempeno": m, "curva": curva, "delta_semana": delta,
            "por_circuito": {t: md.metricas([r for r in temp if r["tour"] == t], mercado) for t in cfg["tenis"]["circuitos"]},
            "temporada_pasada": {"temporada": ahora.year - 1, "partidos": mp.get("partidos"), "pct_aciertos": mp.get("pct_aciertos"),
                                 "brier": mp.get("brier")},
            "version": md.VERSION}


def piernas_parlay(conn, cfg: dict, ahora: datetime, hasta: datetime, p_min: float) -> list[dict]:
    """Piernas «Gana <jugador>» para «Qué meter» / «Soñadoras»: solo partidos con momio y jugadores con historial suficiente."""
    u = float(cfg["valor"]["umbral_edge"])
    dias = max(1, (hasta - ahora).days + 1)
    salida = []
    for x in pronosticos(conn, cfg, ahora, dias=dias):
        inicio = datetime.fromisoformat(x["inicio"])
        if inicio <= ahora or inicio >= hasta or x["p_final"] is None or x["p_mercado"] is None:
            continue
        j1 = x["p_final"] >= 0.5
        p = x["p_final"] if j1 else 1 - x["p_final"]
        if p < p_min:
            continue
        quien = x["jugador1"] if j1 else x["jugador2"]
        salida.append({"partido_id": f"tenis:{x['id']}", "dia": inicio.date().isoformat(),
                       "dia_texto": f"{('lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom')[inicio.weekday()]} {inicio.day:02d}/{inicio.month:02d}",
                       "hora": inicio.strftime("%H:%M"), "inicio": inicio.isoformat(), "liga": f"🎾 {x['tour'].upper()} · {x['torneo']}",
                       "partido": f"{x['jugador1']} vs {x['jugador2']}", "tipo": "tenis_gana", "jugada": f"Gana {quien}",
                       "detalle": f"gana el partido ({x['superficie']}, a {x['best_of']} sets)", "p": p, "cuota_justa": 1 / p,
                       "cuota_min": (1 + u) / p, "confianza": None, "fuente": x["fuente"],
                       "cuota": x["cuota1"] if j1 else x["cuota2"]})
        st = x.get("sets")
        if not st:
            continue
        k = "jugador1" if j1 else "jugador2"
        rival = x["jugador2"] if j1 else x["jugador1"]
        extra = [("tenis_set1", f"{quien} gana el 1.er set", "gana el primer set", st[k]["set1"]),
                 ("tenis_sets", f"{quien} −1.5 sets (gana 2-0)", "gana sin ceder un set", st[k]["menos_1_5"]),
                 ("tenis_sets", f"{rival} +1.5 sets (gana al menos un set)", "el que no es favorito gana al menos un set",
                  st["jugador2" if j1 else "jugador1"]["mas_1_5"])]
        for tipo, jugada, detalle, pe in extra:
            if pe < p_min:
                continue
            salida.append({**salida[-1], "tipo": tipo, "jugada": jugada, "detalle": detalle, "p": pe, "cuota_justa": 1 / pe,
                           "cuota_min": (1 + u) / pe, "cuota": None})
    return salida
