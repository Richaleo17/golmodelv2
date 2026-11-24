"""Mercados NFL desde Normal(μ, σ) discretizada a puntos enteros (los marcadores son enteros: así salen los push).

* Moneyline: P(margen > 0) y P(margen < 0); el empate (rarísimo en NFL) se reparte a la mitad.
* Spread: el local con línea L cubre si margen + L > 0; push si margen + L == 0 (solo líneas enteras).
* Totales: Más si total > línea; push si total == línea (líneas enteras).
Límite conocido: los números clave (3, 7) tienen más masa que la Normal; se documenta, no se corrige en v1.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

RANGO = np.arange(-80, 81)
RANGO_TOTAL = np.arange(0, 121)


def pmf_entera(mu: float, sigma: float, soporte: np.ndarray) -> np.ndarray:
    p = norm.cdf(soporte + 0.5, mu, sigma) - norm.cdf(soporte - 0.5, mu, sigma)
    return p / p.sum()


def moneyline(mu: float, sigma: float) -> dict:
    p = pmf_entera(mu, sigma, RANGO)
    empate = float(p[RANGO == 0].sum())
    loc = float(p[RANGO > 0].sum()) + empate / 2
    return {"local": loc, "visitante": 1.0 - loc}


def spread(mu: float, sigma: float, linea_local: float) -> dict:
    """linea_local: p. ej. −3.5 (local favorito por 3.5)."""
    p = pmf_entera(mu, sigma, RANGO)
    x = RANGO + linea_local
    return {"local": float(p[x > 0].sum()), "visitante": float(p[x < 0].sum()), "push": float(p[x == 0].sum())}


def totales(mu: float, sigma: float, linea: float) -> dict:
    p = pmf_entera(mu, sigma, RANGO_TOTAL)
    return {"over": float(p[RANGO_TOTAL > linea].sum()), "under": float(p[RANGO_TOTAL < linea].sum()),
            "push": float(p[RANGO_TOTAL == linea].sum())}


def ev(p_gana: float, p_push: float, cuota: float) -> float:
    """EV por unidad con push (se devuelve la apuesta)."""
    return p_gana * (cuota - 1.0) - (1.0 - p_gana - p_push)
