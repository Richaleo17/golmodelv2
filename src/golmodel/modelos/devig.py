"""Quitar el margen de la casa.

[Literatura]
* Shin (1992, 1993); fórmula cerrada por selección según Jullien & Salanié
  (1994) y Štrumbelj (2014), que lo valida en fútbol:
      p_i = (sqrt(z² + 4(1−z)·π_i²/Σπ) − z) / (2(1−z)),  π_i = 1/cuota_i
  con z ∈ [0, 1) tal que Σ p_i = 1.
* Potencia: p_i = π_i^k con k tal que Σ p_i = 1 (Clarke, Kovalchik e Ingram, 2017).
* Normalización simple (π_i/Σπ): solo como referencia, NO para decidir.
[Diseño] Consenso: si falta la casa de referencia, mediana por selección de
las probabilidades sin margen de >= N casas, renormalizada.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.optimize import brentq


def _implicitas(cuotas: Sequence[float]) -> np.ndarray:
    c = np.asarray(cuotas, dtype=float)
    if c.ndim != 1 or len(c) < 2:
        raise ValueError("Se necesitan las cuotas de todo el grupo (>= 2 selecciones)")
    if np.any(c <= 1.0):
        raise ValueError(f"Cuotas inválidas: {c}")
    return 1.0 / c


def margen(cuotas: Sequence[float]) -> float:
    return float(_implicitas(cuotas).sum() - 1.0)


def normalizacion(cuotas: Sequence[float]) -> np.ndarray:
    pi = _implicitas(cuotas)
    return pi / pi.sum()


def _shin_p(pi: np.ndarray, z: float) -> np.ndarray:
    s = pi.sum()
    return (np.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / s) - z) / (2.0 * (1.0 - z))


def shin(cuotas: Sequence[float], *, devolver_z: bool = False):
    pi = _implicitas(cuotas)
    if pi.sum() <= 1.0:          # sin margen (o arbitraje): no hay nada que quitar con Shin
        p, z = pi / pi.sum(), 0.0
    else:
        z = brentq(lambda z: _shin_p(pi, z).sum() - 1.0, 0.0, 0.999999, xtol=1e-14)
        p = _shin_p(pi, z)
        p = p / p.sum()          # elimina el error numérico residual
    return (p, z) if devolver_z else p


def potencia(cuotas: Sequence[float]) -> np.ndarray:
    pi = _implicitas(cuotas)
    if pi.sum() <= 1.0:
        return pi / pi.sum()
    k = brentq(lambda k: (pi**k).sum() - 1.0, 1.0, 50.0, xtol=1e-14)
    p = pi**k
    return p / p.sum()


METODOS = {"shin": shin, "potencia": potencia}


def quitar_margen(cuotas: Sequence[float], metodo: str = "shin") -> np.ndarray:
    return np.asarray(METODOS[metodo](cuotas))


def consenso(cuotas_por_casa: dict[str, Sequence[float]], *, metodo: str = "shin",
             min_casas: int = 3) -> np.ndarray | None:
    """Mediana por selección de las probabilidades sin margen. None si hay pocas casas."""
    validas = [quitar_margen(c, metodo) for c in cuotas_por_casa.values()]
    if len(validas) < min_casas:
        return None
    med = np.median(np.vstack(validas), axis=0)
    return med / med.sum()
