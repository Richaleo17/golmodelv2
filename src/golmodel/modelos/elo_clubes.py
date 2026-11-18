"""Segundo modelo de fútbol: Elo de clubes, en ENSAMBLE con Dixon-Coles para el 1X2 (2026-11-18).

* Elo por equipo con nuestro propio histórico (sin fuentes nuevas): K fijo, multiplicador por diferencia de goles
  (ln(|dif|+1)) y ventaja de local APRENDIDA por liga (promedio exponencial del rendimiento del local sobre lo esperado).
* 1X2 con un logit ordenado sobre la diferencia d = Elo_local − Elo_visitante + ventaja:
      P(visita) = σ(c1 − b·d),   P(visita o empate) = σ(c2 − b·d),   c1 < c2.
  Tres parámetros ajustados por máxima verosimilitud con partidos ANTERIORES a la ventana que se evalúa.
* Ensamble: p = w·p_DixonColes + (1 − w)·p_Elo. w se elige en la primera parte de la ventana walk-forward y se valida en la
  última: se ACTIVA solo si mejora el log loss en la validación (global) y solo en las ligas donde mejora. El α contra el
  mercado se calibra después sobre el ensamble: si no le gana al mercado, el mercado sigue mandando.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.optimize import minimize

SEL_1X2 = ("1X2:1", "1X2:X", "1X2:2")


def _p(cfg: dict) -> dict:
    return {"k": 20.0, "hfa_inicial": 60.0, "hfa_memoria": 300.0, "inicial": 1500.0, "regresion_temporada": 0.2,
            **((cfg.get("elo_clubes") or {}).get("parametros") or {})}


def recorrer(df: pd.DataFrame, cfg: dict) -> tuple[dict, dict, pd.Series]:
    """Elo de todos los partidos jugados (orden cronológico). Devuelve (ratings, ventaja por liga, d previa por partido_id)."""
    p = _p(cfg)
    k, ini = float(p["k"]), float(p["inicial"])
    w = 1.0 / float(p["hfa_memoria"])
    elo: dict = {}
    hfa: dict = {}
    temporada_de: dict = {}
    d_previa = {}
    jugados = df[df["goles_local"].notna()].sort_values("inicio")
    for r in jugados.itertuples(index=False):
        liga, a, b = r.liga, r.local, r.visitante
        for eq in (a, b):                                          # regresión parcial al cambiar de temporada
            t = getattr(r, "temporada", None)
            if t is not None and eq in temporada_de and temporada_de[eq] != t and eq in elo:
                elo[eq] = elo[eq] - (elo[eq] - ini) * float(p["regresion_temporada"])
            if t is not None:
                temporada_de[eq] = t
        ra, rb = elo.get(a, ini), elo.get(b, ini)
        h = hfa.get(liga, float(p["hfa_inicial"]))
        d = ra - rb + h
        d_previa[r.partido_id] = d
        gl, gv = int(r.goles_local), int(r.goles_visitante)
        s = 1.0 if gl > gv else (0.5 if gl == gv else 0.0)
        e = 1.0 / (1.0 + 10 ** (-d / 400.0))
        mult = math.log(abs(gl - gv) + 1) + (1.0 if gl == gv else 0.0)
        delta = k * mult * (s - e)
        elo[a], elo[b] = ra + delta, rb - delta
        # ventaja de local: cuánto rindió el local por encima de lo que decía el Elo sin ventaja (en puntos Elo aprox.)
        obs = -400.0 * math.log10(1.0 / min(max(s, 0.02), 0.98) - 1.0) - (ra - rb)
        hfa[liga] = h + w * (max(-200.0, min(400.0, obs)) - h)
    return elo, hfa, pd.Series(d_previa, dtype=float)


def _sig(x):
    return 1.0 / (1.0 + np.exp(-x))


def probs(d, par: dict) -> np.ndarray:
    """d (escalar o arreglo) -> matriz (n, 3) con P(1), P(X), P(2)."""
    d = np.atleast_1d(np.asarray(d, dtype=float)) / 400.0
    b, c1, c2 = par["b"], par["c1"], par["c2"]
    p2 = _sig(c1 - b * d)
    p2x = _sig(c2 - b * d)
    return np.clip(np.column_stack([1 - p2x, p2x - p2, p2]), 1e-6, 1)


def ajustar_ordenado(d: np.ndarray, y: np.ndarray) -> dict:
    """y: 0 = gana local, 1 = empate, 2 = gana visita. Máxima verosimilitud de (b, c1, c2)."""
    d = np.asarray(d, dtype=float)
    y = np.asarray(y, dtype=int)

    def nll(x):
        par = {"b": x[0], "c1": x[1], "c2": x[1] + math.exp(x[2])}
        p = probs(d, par)
        return -float(np.mean(np.log(p[np.arange(len(y)), y])))
    r = minimize(nll, x0=np.array([2.0, -1.0, math.log(1.1)]), method="Nelder-Mead", options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-7})
    return {"b": float(r.x[0]), "c1": float(r.x[1]), "c2": float(r.x[1] + math.exp(r.x[2])), "log_loss": float(r.fun), "n": int(len(y))}


def _ll(p: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1))))


def calibrar_ensamble(df: pd.DataFrame, pred: pd.DataFrame, cfg: dict) -> dict:
    """pred: walk-forward de Dixon-Coles (con partido_id, liga, 1X2:*, y_1x2). Decide w y en qué ligas se activa."""
    ce = cfg.get("elo_clubes") or {}
    out = {"activo": False, "version": "elo-ens-v1"}
    if not ce.get("activo", True) or pred.empty or "partido_id" not in pred.columns:
        out["motivo"] = "sin datos"
        return out
    _, _, dprev = recorrer(df, cfg)
    pr = pred[pred["partido_id"].isin(dprev.index)].sort_values("inicio").copy()
    if len(pr) < int(ce.get("min_partidos", 300)):
        out["motivo"] = f"solo {len(pr)} partidos evaluables"
        return out
    pr["d"] = dprev.loc[pr["partido_id"]].to_numpy()
    inicio_eval = pr["inicio"].min()
    hist = df[(df["inicio"] < inicio_eval) & df["goles_local"].notna() & df["partido_id"].isin(dprev.index)]
    if len(hist) < 500:
        out["motivo"] = "historial previo insuficiente para el logit ordenado"
        return out
    yh = np.where(hist["goles_local"] > hist["goles_visitante"], 0, np.where(hist["goles_local"] == hist["goles_visitante"], 1, 2))
    par = ajustar_ordenado(dprev.loc[hist["partido_id"]].to_numpy(), yh)
    p_dc = pr[list(SEL_1X2)].to_numpy()
    p_dc = p_dc / p_dc.sum(axis=1, keepdims=True)
    p_elo = probs(pr["d"].to_numpy(), par)
    y = pr["y_1x2"].to_numpy(int)
    corte = int(len(pr) * (1 - float(ce.get("frac_validacion", 0.4))))
    rejilla = np.round(np.arange(0, 1.0001, 0.05), 2)
    ll_aj = [_ll(w * p_dc[:corte] + (1 - w) * p_elo[:corte], y[:corte]) for w in rejilla]
    w = float(rejilla[int(np.argmin(ll_aj))])
    ens = w * p_dc + (1 - w) * p_elo
    val = slice(corte, None)
    ll_dc, ll_ens, ll_elo = _ll(p_dc[val], y[val]), _ll(ens[val], y[val]), _ll(p_elo[val], y[val])
    mejora_min = float(ce.get("mejora_min", 0.002))
    ligas = {}
    for liga in sorted(pr["liga"].unique()):
        m = (pr["liga"].to_numpy() == liga)
        mv = m.copy()
        mv[:corte] = False
        if mv.sum() < int(ce.get("min_partidos_liga", 60)):
            continue
        a, b_ = _ll(p_dc[mv], y[mv]), _ll(ens[mv], y[mv])
        ligas[liga] = {"n": int(mv.sum()), "log_loss_dc": a, "log_loss_ensamble": b_, "activa": bool(b_ < a)}
    activo = w < 1.0 and ll_ens <= ll_dc - mejora_min
    out.update(activo=bool(activo), w=w, parametros=par, n_eval=int(len(pr) - corte), log_loss_dc=ll_dc, log_loss_ensamble=ll_ens,
               log_loss_elo=ll_elo, ligas=ligas, ligas_activas=[l for l, v in ligas.items() if v["activa"]] if activo else [])
    if not activo:
        out["motivo"] = f"el ensamble no mejora lo suficiente en validación ({ll_ens:.4f} vs {ll_dc:.4f})"
    return out


def aplicar_a_pred(pred: pd.DataFrame, df: pd.DataFrame, ens: dict, cfg: dict) -> pd.DataFrame:
    """Reemplaza el 1X2 del walk-forward por el ensamble en las ligas activas (para calibrar α sobre lo que se va a usar)."""
    if not ens.get("activo") or pred.empty:
        return pred
    _, _, dprev = recorrer(df, cfg)
    out = pred.copy()
    m = out["liga"].isin(ens["ligas_activas"]) & out["partido_id"].isin(dprev.index)
    if not m.any():
        return out
    p_dc = out.loc[m, list(SEL_1X2)].to_numpy()
    p_dc = p_dc / p_dc.sum(axis=1, keepdims=True)
    p_elo = probs(dprev.loc[out.loc[m, "partido_id"]].to_numpy(), ens["parametros"])
    out.loc[m, list(SEL_1X2)] = ens["w"] * p_dc + (1 - ens["w"]) * p_elo
    return out


_CACHE: dict = {}


def estado(hist: pd.DataFrame, cfg: dict) -> tuple[dict, dict]:
    """Ratings y ventaja por liga con todo lo jugado (se guarda en memoria hasta que cambie el historial)."""
    jug = hist[hist["goles_local"].notna()]
    clave = (len(jug), str(jug["inicio"].max()) if len(jug) else None)
    if _CACHE.get("clave") != clave:
        elo, hfa, _ = recorrer(hist, cfg)
        _CACHE.update(clave=clave, valor=(elo, hfa))
    return _CACHE["valor"]


def p_partido(hist: pd.DataFrame, cfg: dict, ens: dict, liga: str, local: str, visitante: str) -> dict | None:
    """1X2 del Elo para un partido próximo (None si el ensamble no aplica a la liga o falta algún equipo)."""
    if not ens.get("activo") or liga not in (ens.get("ligas_activas") or []):
        return None
    elo, hfa = estado(hist, cfg)
    if local not in elo or visitante not in elo:
        return None
    p = _p(cfg)
    d = elo[local] - elo[visitante] + hfa.get(liga, float(p["hfa_inicial"]))
    pe = probs(d, ens["parametros"])[0]
    return dict(zip(SEL_1X2, pe))


def mezclar(p_dc: dict, p_elo: dict, w: float) -> dict:
    """Ensamble del 1X2 y sus dobles oportunidades a partir de las probabilidades del Dixon-Coles."""
    t = sum(p_dc[s] for s in SEL_1X2)
    e = {s: w * p_dc[s] / t + (1 - w) * p_elo[s] for s in SEL_1X2}
    return {**e, "DC:1X": e["1X2:1"] + e["1X2:X"], "DC:X2": e["1X2:X"] + e["1X2:2"], "DC:12": e["1X2:1"] + e["1X2:2"]}
