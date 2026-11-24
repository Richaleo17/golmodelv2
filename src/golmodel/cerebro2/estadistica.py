"""Estadística mínima del auditor: Wilson, Jensen-Shannon y bootstrap con semilla (reproducible)."""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.stats import norm


def wilson(aciertos: int, n: int, confianza: float) -> tuple[float, float]:
    """Intervalo de Wilson bilateral para una proporción."""
    if n <= 0:
        raise ValueError("Wilson requiere n > 0")
    z = float(norm.ppf(0.5 + confianza / 2))
    p = aciertos / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    medio = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centro - medio), min(1.0, centro + medio)


def jensen_shannon_bits(p: Sequence[float], q: Sequence[float]) -> float:
    """Divergencia de Jensen-Shannon en bits (simétrica, acotada en [0, 1])."""
    a, b = np.asarray(p, dtype=float), np.asarray(q, dtype=float)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("Las distribuciones deben tener la misma forma 1-D")
    if np.any(a < 0) or np.any(b < 0) or a.sum() <= 0 or b.sum() <= 0:
        raise ValueError("Distribuciones inválidas")
    a, b = a / a.sum(), b / b.sum()
    m = (a + b) / 2

    def kl(x: np.ndarray) -> float:
        nz = x > 0
        return float(np.sum(x[nz] * np.log2(x[nz] / m[nz])))

    return max(0.0, 0.5 * kl(a) + 0.5 * kl(b))


def bootstrap_media(valores: Sequence[float], confianza: float, *, n_boot: int = 2000,
                    semilla: int = 0) -> tuple[float, float, float]:
    """(media, ic_inf, ic_sup) por bootstrap percentil."""
    v = np.asarray(valores, dtype=float)
    if v.size == 0:
        raise ValueError("bootstrap sin datos")
    rng = np.random.default_rng(semilla)
    medias = v[rng.integers(0, v.size, size=(n_boot, v.size))].mean(axis=1)
    a = (1 - confianza) / 2
    return float(v.mean()), float(np.quantile(medias, a)), float(np.quantile(medias, 1 - a))


def bootstrap_diferencia(x: Sequence[float], y: Sequence[float], confianza: float, *, n_boot: int = 2000,
                         semilla: int = 0) -> tuple[float, float, float]:
    """(media(x) − media(y), ic_inf, ic_sup) remuestreando cada grupo por separado."""
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if a.size == 0 or b.size == 0:
        raise ValueError("bootstrap sin datos en algún grupo")
    rng = np.random.default_rng(semilla)
    da = a[rng.integers(0, a.size, size=(n_boot, a.size))].mean(axis=1)
    db = b[rng.integers(0, b.size, size=(n_boot, b.size))].mean(axis=1)
    d = da - db
    q = (1 - confianza) / 2
    return float(a.mean() - b.mean()), float(np.quantile(d, q)), float(np.quantile(d, 1 - q))
