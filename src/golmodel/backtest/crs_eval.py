"""¿Se puede activar el ajuste de probabilidad del CRS?

Criterio (config: crs.ajuste_favorito.criterio_activacion): en los partidos
con CRS ALTA, el log-loss 1X2 CON ajuste debe ser menor que SIN ajuste,
con la cota superior del intervalo bootstrap al 95% de la diferencia < 0 y
al menos `n_min` partidos. Si no se cumple, el ajuste se queda en 'sombra'.
También reporta si el veto tuvo sentido: frecuencia real de victoria del
favorito frente a lo que decía el mercado sin margen.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

EPS = 1e-15


@dataclass(frozen=True)
class EvaluacionCRS:
    n: int
    log_loss_sin: float
    log_loss_con: float
    diferencia: float                 # con − sin (negativo = el ajuste ayuda)
    ic95: tuple[float, float]
    frecuencia_fav_gana: float
    p_fav_mercado_media: float
    activable: bool
    motivo: str


def evaluar(df: pd.DataFrame, *, n_min: int = 200, n_boot: int = 2000, semilla: int = 0) -> EvaluacionCRS:
    """df: una fila por partido con CRS ALTA. Columnas:
    p_sin_{1,X,2}, p_con_{1,X,2} (probabilidades 1X2), y (0/1/2), fav_gana (0/1), p_fav_mercado.
    """
    n = len(df)
    y = df["y"].to_numpy(int)
    sin = df[["p_sin_1", "p_sin_X", "p_sin_2"]].to_numpy()
    con = df[["p_con_1", "p_con_X", "p_con_2"]].to_numpy()
    ll_sin = -np.log(np.clip(sin[np.arange(n), y], EPS, 1))
    ll_con = -np.log(np.clip(con[np.arange(n), y], EPS, 1))
    d = ll_con - ll_sin
    rng = np.random.default_rng(semilla)
    boot = np.array([d[rng.integers(0, n, n)].mean() for _ in range(n_boot)]) if n else np.array([0.0])
    lo, hi = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))) if n else (0.0, 0.0)
    if n < n_min:
        activable, motivo = False, f"muestra insuficiente ({n} < {n_min})"
    elif hi >= 0:
        activable, motivo = False, "el ajuste no mejora el log-loss de forma significativa"
    else:
        activable, motivo = True, "el ajuste mejora el log-loss (IC95 < 0)"
    return EvaluacionCRS(n, float(ll_sin.mean()) if n else float("nan"), float(ll_con.mean()) if n else float("nan"),
                         float(d.mean()) if n else float("nan"), (lo, hi),
                         float(df["fav_gana"].mean()) if n else float("nan"),
                         float(df["p_fav_mercado"].mean()) if n else float("nan"), activable, motivo)
