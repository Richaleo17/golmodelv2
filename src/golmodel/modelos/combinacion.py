"""Combinación modelo + mercado.

[Diseño] p_adj = α·p_modelo + (1−α)·p_mercado, con α calibrado en walk-forward
contra la cuota disponible al momento de decidir (nunca la de cierre).

Matriz implícita del mercado [Diseño]: λ_h, λ_a tales que la matriz
Dixon-Coles (con el ρ del modelo) reproduce 1X2 y OU 2.5 sin margen. Permite
dar p_mercado a un SGP aunque la casa no cotice esa combinación. Si el
ajuste no reproduce bien el mercado (error > tolerancia), NO se usa.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from golmodel.modelos.matriz import Matriz, matriz_marcadores
from golmodel.modelos.mercados import probabilidad


def p_ajustada(p_modelo: float, p_mercado: float | None, alpha: float) -> float | None:
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("α debe estar en [0, 1]")
    if p_mercado is None:
        return None
    return alpha * p_modelo + (1.0 - alpha) * p_mercado


def p_ajustada_push(pw: float, pp: float, pl: float, q_mercado: float | None,
                    alpha: float) -> tuple[float, float, float] | None:
    """Para selecciones con push (AH ±1.0).

    El margen se quita sobre el par (A, B) de la línea, lo que da
    q = P(gana A | no hay push). La probabilidad de push sale del modelo
    (el mercado no la cotiza): p_w,mercado = q·(1 − p_push).
    Devuelve (p_gana, p_push, p_pierde) ajustadas.
    """
    if q_mercado is None:
        return None
    wm = q_mercado * (1.0 - pp)
    lm = (1.0 - q_mercado) * (1.0 - pp)
    return alpha * pw + (1 - alpha) * wm, pp, alpha * pl + (1 - alpha) * lm


@dataclass(frozen=True)
class MatrizMercado:
    matriz: Matriz
    error_max: float            # máx |p_matriz − p_mercado| en las selecciones usadas
    usable: bool


def matriz_mercado(p_mercado: dict[str, float], rho: float, *, gmax: int = 10,
                   tolerancia: float = 0.015, inicial: tuple[float, float] = (1.4, 1.1)) -> MatrizMercado | None:
    """p_mercado: selecciones sin margen, p. ej. {'1X2:1':.., '1X2:X':.., '1X2:2':.., 'OU:2.5:over':..}."""
    sels = [s for s in ("1X2:1", "1X2:X", "1X2:2", "OU:2.5:over") if s in p_mercado]
    if not {"1X2:1", "1X2:2"} <= set(sels):
        return None
    objetivo = np.array([p_mercado[s] for s in sels])

    def residuos(x):
        m = matriz_marcadores(np.exp(x[0]), np.exp(x[1]), rho, gmax)
        return np.array([probabilidad(m, s) for s in sels]) - objetivo

    sol = least_squares(residuos, np.log(inicial), bounds=(np.log(0.05), np.log(8.0)), xtol=1e-12, ftol=1e-12)
    m = matriz_marcadores(float(np.exp(sol.x[0])), float(np.exp(sol.x[1])), rho, gmax)
    err = float(np.max(np.abs(residuos(sol.x))))
    return MatrizMercado(m, err, err <= tolerancia)
