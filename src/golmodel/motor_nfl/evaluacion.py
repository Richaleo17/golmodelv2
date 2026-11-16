"""Evaluación del motor NFL con la predicción PREVIA a cada juego (sin look-ahead) y lo que el modelo «recuerda».

Idea tomada de nfl_probability_engine (sebastianBP26): un Elo que se actualiza juego a juego con resultados reales y que se mide
cada semana contra el 50/50, contra «siempre el local» y contra Las Vegas (récord, Brier, log loss y calibración por rango).
Aquí se reutiliza el MISMO estado que usa la pestaña NFL (`modelo.actualizar`), así que lo que se evalúa es lo que se muestra.
"""
from __future__ import annotations

import math
from collections import defaultdict

from golmodel.motor_nfl import mercados as mk
from golmodel.motor_nfl import modelo as md

RANGOS = [(0.50, 0.60, "50–60%"), (0.60, 0.70, "60–70%"), (0.70, 0.80, "70–80%"), (0.80, 1.01, "80%+")]


def p_americano(ml) -> float | None:
    """Momio americano -> probabilidad implícita (con margen)."""
    if ml is None:
        return None
    ml = float(ml)
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def p_vegas(ml_local=None, ml_visitante=None, spread_cierre=None, sigma: float = 13.5) -> float | None:
    """P(gana el local) según Las Vegas: moneyline sin margen si existe; si no, la línea de cierre (positiva = local favorito)."""
    a, b = p_americano(ml_local), p_americano(ml_visitante)
    if a and b:
        return a / (a + b)
    if spread_cierre is not None:
        return mk.moneyline(float(spread_cierre), sigma)["local"]
    return None


def recorrer(juegos: list[md.Juego], cfg: dict, epa_por_juego: dict | None = None, sigma: float | None = None,
             desde_temporada: int | None = None) -> tuple[md.EstadoNFL, list[dict]]:
    """Construye el estado juego a juego y guarda la predicción previa de cada juego (desde `desde_temporada`)."""
    sig = float(sigma or cfg["nfl"]["modelo"]["sigma_margen"])
    est = md.EstadoNFL()
    registros = []
    for j in sorted((x for x in juegos if x.jugado), key=lambda x: (x.temporada, x.semana, x.juego_id)):
        pred = md.actualizar(est, j, cfg, (epa_por_juego or {}).get(j.juego_id))
        if desde_temporada is not None and j.temporada < desde_temporada:
            continue
        p_loc = mk.moneyline(pred["mu_margen"], sig)["local"]
        real = j.puntos_local - j.puntos_visitante
        registros.append({"juego_id": j.juego_id, "temporada": j.temporada, "semana": j.semana, "tipo": j.tipo,
                          "local": j.local, "visitante": j.visitante, "p_local": p_loc, "mu_margen": pred["mu_margen"],
                          "elo_local": pred["elo_local"], "elo_visitante": pred["elo_visitante"],
                          "puntos_local": j.puntos_local, "puntos_visitante": j.puntos_visitante,
                          "gano_local": 1.0 if real > 0 else (0.0 if real < 0 else 0.5)})
    return est, registros


def _ll(p: float, y: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def metricas(registros: list[dict], vegas: dict | None = None) -> dict:
    """Récord con pick = lado con > 50% (empate = ½), Brier, log loss, calibración por rango, vs siempre local y vs Las Vegas."""
    vegas = vegas or {}
    n = len(registros)
    if n == 0:
        return {"partidos": 0}
    ac = er = em = 0
    brier = ll = esperados = 0.0
    loc_p = loc_a = vis_p = vis_a = 0
    siempre_local = 0.0
    cal = defaultdict(lambda: {"partidos": 0, "aciertos": 0.0, "suma_p": 0.0})
    v_n = v_ac = coincide = 0
    v_brier = 0.0
    sorpresas = []
    for r in registros:
        p, y = r["p_local"], r["gano_local"]
        brier += (p - y) ** 2
        ll += _ll(p, y)
        pick_local = p >= 0.5
        p_pick = p if pick_local else 1 - p
        esperados += p_pick
        acierto = 0.5 if y == 0.5 else float((y == 1.0) == pick_local)
        if y == 0.5:
            em += 1
        elif acierto:
            ac += 1
        else:
            er += 1
        if pick_local:
            loc_p += 1
            loc_a += acierto == 1.0
        else:
            vis_p += 1
            vis_a += acierto == 1.0
        siempre_local += y
        for lo, hi, et in RANGOS:
            if lo <= p_pick < hi:
                c = cal[et]
                c["partidos"] += 1
                c["aciertos"] += acierto
                c["suma_p"] += p_pick
        pv = vegas.get(r["juego_id"])
        if pv is not None:
            v_n += 1
            v_brier += (pv - y) ** 2
            v_ac += 0.5 if y == 0.5 else float((y == 1.0) == (pv >= 0.5))
            coincide += (pv >= 0.5) == pick_local
        ganador_p = p if y == 1.0 else (1 - p if y == 0.0 else None)
        if ganador_p is not None:
            sorpresas.append((ganador_p, r))
    calib = [{"rango": et, "partidos": cal[et]["partidos"], "prob_media": cal[et]["suma_p"] / cal[et]["partidos"],
              "pct_real": cal[et]["aciertos"] / cal[et]["partidos"]} for _, _, et in RANGOS if cal[et]["partidos"]]
    out = {"partidos": n, "aciertos": ac, "errores": er, "empates": em, "record": f"{ac}-{er}" + (f"-{em}" if em else ""),
           "pct_aciertos": (ac + 0.5 * em) / n, "aciertos_esperados": esperados, "brier": brier / n, "log_loss": ll / n,
           "brier_moneda": 0.25, "picks_local": loc_p, "aciertos_local": loc_a, "picks_visita": vis_p, "aciertos_visita": vis_a,
           "pct_siempre_local": siempre_local / n, "calibracion": calib}
    if v_n:
        out["vegas"] = {"partidos": v_n, "pct_aciertos": v_ac / v_n, "brier": v_brier / v_n, "coincide": coincide / v_n}
    if sorpresas:
        p, r = min(sorpresas, key=lambda x: x[0])
        ganador = r["local"] if r["gano_local"] == 1.0 else r["visitante"]
        out["batacazo"] = {"juego_id": r["juego_id"], "semana": r["semana"], "ganador": ganador, "p": p,
                           "marcador": f"{r['visitante']} {r['puntos_visitante']} – {r['puntos_local']} {r['local']}"}
    return out


def por_semana(registros: list[dict], vegas: dict | None = None) -> list[dict]:
    semanas = sorted({(r["temporada"], r["semana"]) for r in registros})
    salida = []
    for t, s in semanas:
        m = metricas([r for r in registros if (r["temporada"], r["semana"]) == (t, s)], vegas)
        salida.append({"temporada": t, "semana": s, "record": m["record"], "pct_aciertos": m["pct_aciertos"], "brier": m["brier"],
                       "vegas_pct": m.get("vegas", {}).get("pct_aciertos")})
    return salida


def ranking_elo(est: md.EstadoNFL, registros: list[dict], temporada: int) -> list[dict]:
    """Elo actual por equipo y cuánto cambió en la última semana jugada y en la temporada («la memoria» del modelo)."""
    previo: dict = {}
    inicio: dict = {}
    ultima = max((r["semana"] for r in registros if r["temporada"] == temporada), default=None)
    for r in registros:
        if r["temporada"] != temporada:
            continue
        for eq, elo in ((r["local"], r["elo_local"]), (r["visitante"], r["elo_visitante"])):
            inicio.setdefault(eq, elo)
            if r["semana"] == ultima:
                previo[eq] = elo
    filas = [{"equipo": eq, "elo": round(v, 1), "cambio_semana": round(v - previo[eq], 1) if eq in previo else 0.0,
              "cambio_temporada": round(v - inicio[eq], 1) if eq in inicio else 0.0} for eq, v in est.elo.items()]
    filas.sort(key=lambda f: -f["elo"])
    for i, f in enumerate(filas, 1):
        f["lugar"] = i
    return filas
