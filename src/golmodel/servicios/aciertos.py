"""Marcador de ACIERTOS por mercado (KPI): de los partidos ya jugados, cuántas veces se cumplió lo que mostraba el Diario.

Ejemplo de salida: Ganador 5/6 · Ambos anotan 3/5 · Más de 2.5 4/6 · PA(1) 3/4 …

* Se usa la ÚLTIMA predicción de producción hecha ANTES del inicio (lo que viste antes del partido; sin look-ahead).
* «Pick» de cada KPI = el lado más probable (igual que las tarjetas del Diario).
* Dos columnas: la DECISIÓN (lo que muestra la app: modelo + mercado + factores) y el MERCADO solo (cuotas sin margen).
  Si la decisión no le gana al mercado con el tiempo, el modelo no está aportando: esa es la prueba de fuego.
* PA(1) (anota el PRIMER gol): sin el minuto de cada gol solo se puede verificar cuando un equipo terminó en 0
  (si el favorito no anotó → falló; si solo anotó el favorito → acertó). Los demás casos quedan «sin verificar».
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

KPIS = ("Ganador", "Ambos anotan", "Más de 1.5", "Más de 2.5", "Menos de 3.5", "PA(1) primer gol", "El otro anota")


def _poisson_probs(lh: float, la: float) -> dict[str, float]:
    from golmodel.servicios.pronostico_selecciones import mercados
    return mercados(lh, la, -0.05)


def _probs(sel: dict[str, dict], lam: tuple[float, float] | None, columna: str) -> dict[str, float] | None:
    """columna: 'p_adj' (decisión) o 'p_mercado'. Lo que falte sale de las λ (Poisson + ρ)."""
    base = _poisson_probs(*lam) if lam and lam[0] and lam[1] else None
    def g(k: str, alt: str | None):
        v = (sel.get(k) or {}).get(columna)
        if v is None and columna == "p_adj":
            v = (sel.get(k) or {}).get("p_modelo")
        if v is None and base is not None and alt is not None:
            v = base[alt]
        return v
    p = {"1": g("1X2:1", "1"), "X": g("1X2:X", "X"), "2": g("1X2:2", "2"), "btts": g("BTTS:si", "btts"),
         "o15": g("OU:1.5:over", "over15"), "o25": g("OU:2.5:over", "over25"), "u35": g("OU:3.5:under", "under35"),
         "al": g("ANOTA:local:si", "anota_local"), "av": g("ANOTA:visitante:si", "anota_visitante")}
    if any(v is None for v in p.values()) or lam is None:
        return None
    t = lam[0] + lam[1]
    alguno = 1 - math.exp(-t) if t > 0 else 0.0               # PA(1) = anota primero = λ_e/(λ_l+λ_v) · P(hay gol)
    p["pa_l"], p["pa_v"] = (lam[0] / t * alguno, lam[1] / t * alguno) if t > 0 else (0.0, 0.0)
    return p


def picks(p: dict[str, float], local: str = "local", visitante: str = "visitante") -> dict[str, tuple[str, float]]:
    """Lo que mostraba la tarjeta: el lado elegido de cada KPI y SU probabilidad (la que se clasifica en rangos)."""
    fav_l = p["1"] >= p["2"]
    s1x2 = p["1"] + p["X"] + p["2"]
    fav, riv = (local, visitante) if fav_l else (visitante, local)
    sino = lambda q, si, no: (si, q) if q >= 0.5 else (no, 1 - q)                       # noqa: E731
    otro = p["av"] if fav_l else p["al"]
    return {
        "Ganador": (f"Gana {fav}", (p["1"] if fav_l else p["2"]) / s1x2),
        "PA(1) primer gol": (f"PA(1) {fav}", p["pa_l"] if fav_l else p["pa_v"]),
        "Ambos anotan": sino(p["btts"], "Ambos anotan: Sí", "Ambos anotan: No"),
        "Más de 1.5": sino(p["o15"], "Más de 1.5: Sí", "Más de 1.5: No"),
        "Más de 2.5": sino(p["o25"], "Más de 2.5: Sí", "Más de 2.5: No"),
        "Menos de 3.5": sino(p["u35"], "Menos de 3.5: Sí", "Menos de 3.5: No"),
        "El otro anota": sino(otro, f"{riv} anota: Sí", f"{riv} anota: No"),
    }


def evaluar(p: dict[str, float], gl: int, gv: int) -> dict[str, bool | None]:
    fav_l = p["1"] >= p["2"]
    tot = gl + gv
    gf, gr = (gl, gv) if fav_l else (gv, gl)
    if gf == 0 and tot > 0:
        pa = False
    elif gr == 0 and gf > 0:
        pa = True
    else:
        pa = None                                          # 0-0 (nadie) o ambos anotaron (no se sabe quién primero)
    if tot == 0:
        pa = False                                         # PA(1) pierde con 0-0
    return {
        "Ganador": gf > gr,
        "Ambos anotan": (gl > 0 and gv > 0) == (p["btts"] >= 0.5),
        "Más de 1.5": (tot >= 2) == (p["o15"] >= 0.5),
        "Más de 2.5": (tot >= 3) == (p["o25"] >= 0.5),
        "Menos de 3.5": (tot <= 3) == (p["u35"] >= 0.5),
        "PA(1) primer gol": pa,
        "El otro anota": (gr > 0) == ((p["av"] if fav_l else p["al"]) >= 0.5),
    }


def _filas(conn, desde: date, hasta: date) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT p.id, p.inicio, dia_mx(p.inicio) AS dia, p.goles_local, p.goles_visitante, l.codigo AS liga, l.solo_mercado,
               el.nombre AS local, ev.nombre AS visitante, pr.id AS prediccion_id, pr.lambda_local, pr.lambda_visitante,
               pr.lambda_mercado_local, pr.lambda_mercado_visitante, pr.cr_scores
        FROM partidos p JOIN ligas l ON l.id = p.liga_id JOIN equipos el ON el.id = p.local_id JOIN equipos ev ON ev.id = p.visitante_id
        JOIN LATERAL (SELECT * FROM predicciones x WHERE x.partido_id = p.id AND x.origen = 'produccion' AND x.creada_en < p.inicio
                      ORDER BY x.creada_en DESC LIMIT 1) pr ON true
        WHERE p.estado = 'finalizado' AND p.goles_local IS NOT NULL AND dia_mx(p.inicio) BETWEEN %s AND %s
        ORDER BY p.inicio""", (desde, hasta)).fetchall()]


def marcador(conn, desde: date, hasta: date) -> dict:
    filas = _filas(conn, desde, hasta)
    ids = [f["prediccion_id"] for f in filas]
    sels: dict[int, dict[str, dict]] = defaultdict(dict)
    if ids:
        for r in conn.execute("""SELECT prediccion_id, seleccion, p_modelo, p_mercado, p_adj FROM predicciones_seleccion
                                 WHERE prediccion_id = ANY(%s) AND origen = 'produccion'""", (ids,)).fetchall():
            sels[r["prediccion_id"]][r["seleccion"]] = dict(r)
    grupos = ("todos", "selecciones", "clubes")
    tab = {g: {c: {k: [0, 0] for k in KPIS} for c in ("decision", "mercado")} for g in grupos}
    ll = {g: {c: [0.0, 0] for c in ("decision", "mercado")} for g in grupos}
    detalle = []
    for f in filas:
        cr = f.get("cr_scores") if isinstance(f.get("cr_scores"), dict) else {}
        es_sel = bool(cr.get("_selecciones")) or (f["solo_mercado"] and any(x in f["liga"].lower() for x in ("nations", "friendl", "qualif")))
        g2 = "selecciones" if es_sel else "clubes"
        lam = (f["lambda_local"], f["lambda_visitante"])
        lam_mk = (f["lambda_mercado_local"], f["lambda_mercado_visitante"]) if f["lambda_mercado_local"] else None
        s = sels.get(f["prediccion_id"], {})
        fila = {"partido": f"{f['local']} {f['goles_local']}-{f['goles_visitante']} {f['visitante']}", "liga": f["liga"],
                "dia": str(f.get("dia") or f["inicio"].date()), "grupo": g2, "ll": {}}
        for col, columna, l_ in (("decision", "p_adj", lam), ("mercado", "p_mercado", lam_mk)):
            p = _probs(s, l_, columna)
            if p is None:
                continue
            r = evaluar(p, f["goles_local"], f["goles_visitante"])
            fila[col] = r
            fila.setdefault("picks", {})[col] = picks(p, f["local"], f["visitante"])
            fila.setdefault("p", {})[col] = {k: v[1] for k, v in fila["picks"][col].items()}
            fila["partido_id"], fila["prediccion_id"] = f["id"], f["prediccion_id"]
            res = "1" if f["goles_local"] > f["goles_visitante"] else ("2" if f["goles_visitante"] > f["goles_local"] else "X")
            tot1x2 = p["1"] + p["X"] + p["2"]
            for g in ("todos", g2):
                for k, v in r.items():
                    if v is not None:
                        tab[g][col][k][0] += int(v)
                        tab[g][col][k][1] += 1
                ll[g][col][0] -= math.log(max(p[res] / tot1x2, 1e-12))
                ll[g][col][1] += 1
            fila["ll"][col] = -math.log(max(p[res] / tot1x2, 1e-12))
        detalle.append(fila)
    salida = {}
    for g in grupos:
        salida[g] = {}
        for c in ("decision", "mercado"):
            n = ll[g][c][1]
            salida[g][c] = {"kpis": {k: {"aciertos": v[0], "total": v[1], "pct": (v[0] / v[1]) if v[1] else None}
                                     for k, v in tab[g][c].items()},
                            "log_loss_1x2": (ll[g][c][0] / n) if n else None, "partidos": n}
    return {"desde": str(desde), "hasta": str(hasta), "grupos": salida, "detalle": detalle}


def texto(m: dict, grupo: str = "todos", columna: str = "decision") -> str:
    k = m["grupos"][grupo][columna]["kpis"]
    return " · ".join(f"{n} {v['aciertos']}/{v['total']}" for n, v in k.items() if v["total"])


# ------------------------------------------------------------------ Dashboard: cuánto vamos acertando y cómo cambia día a día
KPIS_DASHBOARD = (("Ganador", "Gana"), ("PA(1) primer gol", "PA(1) · Primer gol"), ("Ambos anotan", "Ambos anotan"),
                  ("Más de 1.5", "Más de 1.5"), ("Más de 2.5", "Más de 2.5"), ("Menos de 3.5", "Menos de 3.5"))


def construir_dashboard(detalle: list[dict], hoy: date) -> dict:
    """De los partidos evaluados arma, por KPI: % de acierto acumulado, cambio vs ayer y vs el primer día, últimos 7 días,
    serie diaria acumulada (la curva de aprendizaje) y comparación contra el mercado."""
    dias = sorted({f["dia"] for f in detalle})
    kpis = []
    for clave, titulo in KPIS_DASHBOARD:
        serie, a, t, am, tm = [], 0, 0, 0, 0
        for d in dias:
            ad = td = 0
            for f in detalle:
                if f["dia"] != d:
                    continue
                v = (f.get("decision") or {}).get(clave)
                if v is not None:
                    ad += int(v)
                    td += 1
                w = (f.get("mercado") or {}).get(clave)
                if w is not None:
                    am += int(w)
                    tm += 1
            a, t = a + ad, t + td
            serie.append({"dia": d, "aciertos_dia": ad, "total_dia": td, "aciertos": a, "total": t,
                          "pct": a / t if t else None, "pct_mercado": am / tm if tm else None})
        con = [x for x in serie if x["pct"] is not None]
        actual = con[-1]["pct"] if con else None
        ayer = con[-2]["pct"] if len(con) >= 2 else None
        inicio = con[0]["pct"] if con else None
        u7 = [x for x in serie if x["dia"] > str(hoy - timedelta(days=7))]
        a7, t7 = sum(x["aciertos_dia"] for x in u7), sum(x["total_dia"] for x in u7)
        mk = con[-1]["pct_mercado"] if con else None
        kpis.append({"clave": clave, "titulo": titulo, "aciertos": a, "total": t, "pct": actual, "rangos": rangos(detalle, clave),
                     "delta_ayer": (actual - ayer) if (actual is not None and ayer is not None) else None,
                     "delta_inicio": (actual - inicio) if (actual is not None and inicio is not None and len(con) > 1) else None,
                     "pct_7d": a7 / t7 if t7 else None, "total_7d": t7, "pct_mercado": mk,
                     "vs_mercado": (actual - mk) if (actual is not None and mk is not None) else None, "serie": serie})
    ll_app = [f["ll"]["decision"] for f in detalle if "decision" in f.get("ll", {})]
    ll_mk = [f["ll"]["mercado"] for f in detalle if "mercado" in f.get("ll", {})]
    con_delta = [k for k in kpis if k["delta_ayer"] is not None]
    mensajes = []
    for k in sorted(con_delta, key=lambda k: -abs(k["delta_ayer"]))[:3]:
        signo = "más" if k["delta_ayer"] >= 0 else "menos"
        mensajes.append(f"{k['titulo']}: {abs(100 * k['delta_ayer']):.1f} pts {signo} de acierto que ayer ({100 * k['pct']:.1f}%)")
    return {"hoy": str(hoy), "dias": dias, "partidos": len(detalle), "kpis": kpis,
            "log_loss_app": sum(ll_app) / len(ll_app) if ll_app else None,
            "log_loss_mercado": sum(ll_mk) / len(ll_mk) if ll_mk else None, "mensajes": mensajes}


def dashboard(conn, hoy: date, dias: int = 60, desde: date | None = None) -> dict:
    """Lee lo GUARDADO al cierre de cada día (dashboard_registro). Si la tabla aún está vacía (antes del primer cierre),
    calcula al vuelo para no mostrar la pantalla en blanco."""
    ini = desde or (hoy - timedelta(days=dias))
    try:
        with conn.transaction():
            det = detalle_guardado(conn, ini, hoy)
    except Exception:                                             # noqa: BLE001 — tabla aún no migrada
        det = []
    fuente = "cierre diario guardado"
    if not det:
        det, fuente = marcador(conn, ini, hoy)["detalle"], "calculado al vuelo (aún sin cierre guardado)"
    out = construir_dashboard(det, hoy)
    out["fuente_datos"], out["desde"] = fuente, str(ini)
    return out


# ------------------------------------------------------------------ Cierre diario: se GUARDA lo que mostró la app y si se cumplió
RANGOS = ((0.30, 0.50, "30-50%"), (0.50, 0.60, "51-60%"), (0.60, 0.70, "61-70%"), (0.70, 0.80, "71-80%"),
          (0.80, 0.90, "81-90%"), (0.90, 1.0001, "91-100%"))


def rango_de(p: float) -> str | None:
    for lo, hi, nombre in RANGOS:
        if lo <= p < hi:
            return nombre
    return None


def registrar(conn, desde: date, hasta: date) -> int:
    """Guarda (o actualiza) en dashboard_registro cada pick de los partidos terminados del rango. Idempotente: se puede
    correr cada actualización y para rellenar el histórico (desde que arrancó la app)."""
    m = marcador(conn, desde, hasta)
    filas = []
    for f in m["detalle"]:
        for fuente in ("decision", "mercado"):
            if fuente not in f or fuente not in f.get("picks", {}):
                continue
            for kpi, (pick, prob) in f["picks"][fuente].items():
                filas.append((f["partido_id"], kpi, fuente, f["dia"], f["prediccion_id"], pick, min(max(prob, 0.0), 1.0),
                              f[fuente].get(kpi), f["grupo"], f["ll"].get(fuente)))
    if filas:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO dashboard_registro (partido_id, kpi, fuente, dia, prediccion_id, pick, p, acierto, grupo, log_loss_1x2)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (partido_id, kpi, fuente) DO UPDATE SET dia = EXCLUDED.dia, prediccion_id = EXCLUDED.prediccion_id,
                    pick = EXCLUDED.pick, p = EXCLUDED.p, acierto = EXCLUDED.acierto, grupo = EXCLUDED.grupo,
                    log_loss_1x2 = EXCLUDED.log_loss_1x2, registrado_en = now()""", filas)
    return len(filas)


def detalle_guardado(conn, desde: date, hasta: date) -> list[dict]:
    """Reconstruye el «detalle» del dashboard desde la tabla (lo guardado al cierre de cada día)."""
    por: dict[int, dict] = {}
    for r in conn.execute("""SELECT r.partido_id, r.kpi, r.fuente, r.dia, r.pick, r.p, r.acierto, r.grupo, r.log_loss_1x2
                             FROM dashboard_registro r WHERE r.dia BETWEEN %s AND %s""", (desde, hasta)).fetchall():
        f = por.setdefault(r["partido_id"], {"dia": str(r["dia"]), "grupo": r["grupo"], "ll": {}, "p": {}})
        f.setdefault(r["fuente"], {})[r["kpi"]] = r["acierto"]
        f["p"].setdefault(r["fuente"], {})[r["kpi"]] = r["p"]
        if r["log_loss_1x2"] is not None:
            f["ll"][r["fuente"]] = r["log_loss_1x2"]
    return list(por.values())


def rangos(detalle: list[dict], kpi: str) -> list[dict]:
    """Aciertos por rango de probabilidad que mostró la app: si dijo 71-80% y acierta ~75%, la app está bien calibrada."""
    out = []
    for lo, hi, nombre in RANGOS:
        a = t = 0
        suma_p = 0.0
        for f in detalle:
            v = (f.get("decision") or {}).get(kpi)
            pr = ((f.get("p") or {}).get("decision") or {}).get(kpi)
            if v is None or pr is None or not (lo <= pr < hi):
                continue
            a, t, suma_p = a + int(v), t + 1, suma_p + pr
        out.append({"rango": nombre, "aciertos": a, "total": t, "pct": a / t if t else None,
                    "esperado": suma_p / t if t else None})
    return out
