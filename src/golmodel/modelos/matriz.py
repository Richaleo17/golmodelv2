"""Matriz de marcadores Dixon-Coles.

[Literatura] Dixon & Coles (1997): dos Poisson independientes con factor τ
en 0-0, 1-0, 0-1 y 1-1. τ conserva la masa total en la rejilla infinita
(los cambios en las 4 celdas suman 0), así que renormalizar solo corrige
el truncado a `gmax` goles.

Rango válido de ρ para que τ >= 0 en las 4 celdas:
    max(-1/λ_h, -1/λ_a) <= ρ <= min(1/(λ_h·λ_a), 1)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson


@dataclass(frozen=True)
class Matriz:
    p: np.ndarray            # (gmax+1, gmax+1): p[i, j] = P(local = i, visitante = j)
    lambda_local: float
    lambda_visitante: float
    rho: float               # ρ efectivamente aplicado (tras acotarlo)
    rho_acotado: bool        # True si el ρ pedido salía del rango válido
    masa_truncada: float     # 1 − masa antes de renormalizar

    def aplanada(self) -> list[float]:
        return self.p.ravel().tolist()


def rango_rho(lh: float, la: float) -> tuple[float, float]:
    return max(-1.0 / lh, -1.0 / la), min(1.0 / (lh * la), 1.0)


def tau(i: np.ndarray, j: np.ndarray, lh, la, rho) -> np.ndarray:
    """τ vectorizado para arrays de marcadores (i, j)."""
    t = np.ones(np.broadcast(i, j, lh, la).shape, dtype=float)
    t = np.where((i == 0) & (j == 0), 1.0 - lh * la * rho, t)
    t = np.where((i == 0) & (j == 1), 1.0 + lh * rho, t)
    t = np.where((i == 1) & (j == 0), 1.0 + la * rho, t)
    t = np.where((i == 1) & (j == 1), 1.0 - rho, t)
    return t


def matriz_marcadores(lh: float, la: float, rho: float, gmax: int = 10) -> Matriz:
    if lh <= 0 or la <= 0:
        raise ValueError(f"λ deben ser positivas: λ_h={lh}, λ_a={la}")
    lo, hi = rango_rho(lh, la)
    rho_ef = float(np.clip(rho, lo, hi))
    g = np.arange(gmax + 1)
    p = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
    p[0, 0] *= 1.0 - lh * la * rho_ef
    p[0, 1] *= 1.0 + lh * rho_ef
    p[1, 0] *= 1.0 + la * rho_ef
    p[1, 1] *= 1.0 - rho_ef
    masa = float(p.sum())
    return Matriz(p / masa, float(lh), float(la), rho_ef, rho_ef != rho, 1.0 - masa)
