"""Córners: Binomial Negativa (NB2) por equipo. Módulo PARALELO: no toca Dixon-Coles ni la matriz de goles.

    córners_local     ~ NB(μ_L, k)     log μ_L = c + h + ataque[local]  + concede[visit] + árbitro[r]
    córners_visitante ~ NB(μ_V, k)     log μ_V = c     + ataque[visit] + concede[local] + árbitro[r]

* `ataque` es el «estilo de juego» del equipo: cuántos córners genera de más/menos que el promedio (equipos que atacan por banda y
  centran generan más). No hay dato gratuito de «ataques por banda»; este parámetro es su mejor sustituto observable y se dice así.
* `concede`: córners que concede el rival por encima del promedio.
* `árbitro`: efecto multiplicativo pequeño (solo donde la fuente trae árbitro; con pocos partidos se encoge a 0).
* Var = μ + μ²/k (sobredispersión estimada por momentos). Con k → ∞ es Poisson.

Ajuste: máxima verosimilitud Poisson ponderada por decaimiento exp(−ξ·días) con penalización L2 (pooling hacia el promedio),
resuelta con L-BFGS; k por momentos sobre los residuos. Barato (≈ 2 000 partidos en < 1 s) y determinista.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import nbinom

VERSION = "corners-nb-v1"
MAX_CORNERS = 30


@dataclass(frozen=True)
class ModeloCorners:
    c: float
    ventaja: float
    ataque: dict = field(default_factory=dict)
    concede: dict = field(default_factory=dict)
    arbitro: dict = field(default_factory=dict)
    k: float = 50.0                      # dispersión NB2 (grande = casi Poisson)
    n_partidos: int = 0
    media_liga: float = 10.0             # córners totales promedio (para mostrar)
    version: str = VERSION

    def mu(self, local, visitante, arbitro=None) -> tuple[float, float]:
        r = self.arbitro.get(arbitro, 0.0) if arbitro else 0.0
        ml = math.exp(self.c + self.ventaja + self.ataque.get(local, 0.0) + self.concede.get(visitante, 0.0) + r)
        mv = math.exp(self.c + self.ataque.get(visitante, 0.0) + self.concede.get(local, 0.0) + r)
        return ml, mv

    def conoce(self, equipo) -> bool:
        return equipo in self.ataque


def pmf(mu: float, k: float, n: int = MAX_CORNERS) -> np.ndarray:
    """PMF NB2 en 0..n (la cola se suma a la última celda para que sume 1)."""
    p = k / (k + mu)
    x = nbinom.pmf(np.arange(n + 1), k, p)
    x[-1] += max(0.0, 1.0 - x.sum())
    return x


def distribucion(m: ModeloCorners, local, visitante, arbitro=None) -> dict:
    """PMFs de córners local, visitante y total (convolución; independencia condicional a μ)."""
    ml, mv = m.mu(local, visitante, arbitro)
    pl, pv = pmf(ml, m.k), pmf(mv, m.k)
    total = np.convolve(pl, pv)[: 2 * MAX_CORNERS + 1]
    return {"mu_local": ml, "mu_visitante": mv, "local": pl, "visitante": pv, "total": total}


def prob_mas_de(pmf_arr: np.ndarray, linea: float) -> float:
    """P(X > línea) para líneas .5 (sin push)."""
    return float(pmf_arr[int(math.floor(linea)) + 1:].sum())


def ajustar(df: pd.DataFrame, ahora: datetime, *, xi: float = 0.002, l2: float = 2.0, l2_arbitro: float = 20.0,
            min_partidos_arbitro: int = 15) -> ModeloCorners:
    """df: columnas local, visitante, corners_local, corners_visitante, inicio (tz-aware) y opcional arbitro."""
    d = df.dropna(subset=["corners_local", "corners_visitante"]).copy()
    if len(d) < 30:
        raise ValueError(f"córners: muy pocos partidos para ajustar ({len(d)})")
    equipos = sorted(set(d["local"]) | set(d["visitante"]))
    ie = {e: i for i, e in enumerate(equipos)}
    arb = d["arbitro"] if "arbitro" in d else pd.Series([None] * len(d), index=d.index)
    conteo_arb = arb.value_counts()
    arbitros = sorted(a for a in conteo_arb.index if a and conteo_arb[a] >= min_partidos_arbitro)
    ia = {a: i for i, a in enumerate(arbitros)}
    E, A = len(equipos), len(arbitros)
    li = d["local"].map(ie).to_numpy()
    vi = d["visitante"].map(ie).to_numpy()
    ai = np.array([ia.get(a, -1) for a in arb])
    yl = d["corners_local"].to_numpy(float)
    yv = d["corners_visitante"].to_numpy(float)
    dias = np.array([(ahora - t).total_seconds() / 86400.0 for t in pd.to_datetime(d["inicio"], utc=True)])
    w = np.exp(-xi * np.clip(dias, 0, None))
    con_arb = ai >= 0

    def unpack(x):
        return x[0], x[1], x[2:2 + E], x[2 + E:2 + 2 * E], x[2 + 2 * E:]

    def f(x):
        c, h, at, co, ar = unpack(x)
        r = np.where(con_arb, ar[np.clip(ai, 0, None)] if A else 0.0, 0.0)
        ell = c + h + at[li] + co[vi] + r
        elv = c + at[vi] + co[li] + r
        ml, mv = np.exp(ell), np.exp(elv)
        nll = (w * (ml - yl * ell)).sum() + (w * (mv - yv * elv)).sum()
        nll += l2 * ((at ** 2).sum() + (co ** 2).sum()) + l2_arbitro * (ar ** 2).sum()
        gl, gv = w * (ml - yl), w * (mv - yv)
        g = np.zeros_like(x)
        g[0] = gl.sum() + gv.sum()
        g[1] = gl.sum()
        g_at = np.bincount(li, gl, E) + np.bincount(vi, gv, E) + 2 * l2 * at
        g_co = np.bincount(vi, gl, E) + np.bincount(li, gv, E) + 2 * l2 * co
        g[2:2 + E], g[2 + E:2 + 2 * E] = g_at, g_co
        if A:
            g_ar = np.bincount(ai[con_arb], (gl + gv)[con_arb], A) + 2 * l2_arbitro * ar
            g[2 + 2 * E:] = g_ar
        return nll, g

    x0 = np.zeros(2 + 2 * E + A)
    x0[0] = math.log(max(0.5, (np.average(yl, weights=w) + np.average(yv, weights=w)) / 2))
    res = minimize(f, x0, jac=True, method="L-BFGS-B")
    c, h, at, co, ar = unpack(res.x)
    # dispersión por momentos (NB2): Var = μ + μ²/k  ⇒  k = Σμ² / Σ((y−μ)² − μ)
    r = np.where(con_arb, ar[np.clip(ai, 0, None)] if A else 0.0, 0.0)
    ml = np.exp(c + h + at[li] + co[vi] + r)
    mv = np.exp(c + at[vi] + co[li] + r)
    mu = np.concatenate([ml, mv])
    y = np.concatenate([yl, yv])
    ww = np.concatenate([w, w])
    exceso = float((ww * ((y - mu) ** 2 - mu)).sum())
    k = float((ww * mu ** 2).sum() / exceso) if exceso > 1e-9 else 1e6
    k = float(min(max(k, 1.0), 1e6))
    return ModeloCorners(c=float(c), ventaja=float(h), ataque={e: float(at[i]) for e, i in ie.items()},
                         concede={e: float(co[i]) for e, i in ie.items()}, arbitro={a: float(ar[i]) for a, i in ia.items()},
                         k=k, n_partidos=int(len(d)), media_liga=float(np.average(yl + yv, weights=w)))


def correlacion_goles_corners(df: pd.DataFrame) -> float:
    """Correlación de Spearman entre goles totales y córners totales (para la cópula del SGP). 0 si no hay datos."""
    d = df.dropna(subset=["corners_local", "corners_visitante", "goles_local", "goles_visitante"])
    if len(d) < 100:
        return 0.0
    g = d["goles_local"] + d["goles_visitante"]
    cc = d["corners_local"] + d["corners_visitante"]
    rho = g.rank().corr(cc.rank())
    return 0.0 if not math.isfinite(rho) else float(rho)
