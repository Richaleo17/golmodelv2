"""Calibración de hiperparámetros (ξ, w_xG, L2) por walk-forward minimizando log-loss.

[Diseño] Se calibra en un periodo y se REPORTA en otro posterior: calibrar
y evaluar en el mismo periodo sobreestima la precisión.
"""
from __future__ import annotations

import itertools
from datetime import datetime

import numpy as np
import pandas as pd

from golmodel.backtest.walkforward import resumen, walkforward


def buscar(df: pd.DataFrame, *, desde: datetime, hasta: datetime, rejilla: dict[str, list[float]],
           fijos: dict | None = None, paso_dias: int = 7, metrica: str = "log_loss_1x2") -> pd.DataFrame:
    fijos = fijos or {}
    claves = list(rejilla)
    filas = []
    for valores in itertools.product(*(rejilla[k] for k in claves)):
        params = {**fijos, **dict(zip(claves, valores))}
        r = resumen(walkforward(df, desde=desde, hasta=hasta, paso_dias=paso_dias, **params))
        fila = {**dict(zip(claves, valores)), **r}
        if "xi" in fila:
            fila["vida_media_dias"] = float(np.log(2) / fila["xi"]) if fila["xi"] > 0 else float("inf")
        filas.append(fila)
    return pd.DataFrame(filas).sort_values(metrica).reset_index(drop=True)
