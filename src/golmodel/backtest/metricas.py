"""Métricas de probabilidad: log-loss, Brier y curva de calibración. [Literatura]"""
from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-15


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    """p: (n, k) probabilidades; y: (n,) índice de la clase observada."""
    p = np.asarray(p, float)
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0))))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    p = np.asarray(p, float)
    o = np.zeros_like(p)
    o[np.arange(len(y)), y] = 1.0
    return float(np.mean(np.sum((p - o) ** 2, axis=1)))


def calibracion(p: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Reliability plot para una probabilidad binaria: p predicha vs frecuencia observada."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    cortes = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, cortes) - 1, 0, bins - 1)
    filas = []
    for b in range(bins):
        m = idx == b
        if m.any():
            n = int(m.sum())
            f = float(y[m].mean())
            filas.append({"bin": b, "p_media": float(p[m].mean()), "frecuencia": f, "n": n,
                          "error_std": float(np.sqrt(max(f * (1 - f), 1e-12) / n))})
    return pd.DataFrame(filas)


def error_calibracion_esperado(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """ECE: promedio ponderado de |p_media − frecuencia| por bin."""
    t = calibracion(p, y, bins)
    return float((t["n"] * (t["p_media"] - t["frecuencia"]).abs()).sum() / t["n"].sum())
