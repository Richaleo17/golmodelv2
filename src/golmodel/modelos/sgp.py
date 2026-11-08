"""Probabilidad conjunta de un SGP (mismo partido) desde la matriz.

Está prohibido multiplicar probabilidades de selecciones del mismo partido:
P_SGP es la suma de las celdas donde se cumplen TODAS las condiciones.
"""
from __future__ import annotations

from dataclasses import dataclass

from golmodel.modelos.matriz import Matriz
from golmodel.modelos.mercados import APTAS_PARLAY, GRUPO_DE, probabilidad

EPS_IMPOSIBLE = 1e-9


@dataclass(frozen=True)
class EvaluacionSGP:
    selecciones: tuple[str, ...]
    p_conjunta: float
    p_individuales: tuple[float, ...]
    p_si_independientes: float      # solo informativo: lo que daría multiplicar (incorrecto)
    correlacion: float              # p_conjunta / p_si_independientes (lift)
    valido: bool
    motivo: str                     # '' si es válido


def evaluar(m: Matriz, selecciones: tuple[str, ...] | list[str], *,
            ratio_redundancia: float = 0.98, max_selecciones: int = 2) -> EvaluacionSGP:
    sels = tuple(sorted(selecciones))
    if not 2 <= len(sels) <= max_selecciones:
        raise ValueError(f"Un SGP tiene entre 2 y {max_selecciones} selecciones")
    no_aptas = [s for s in sels if s not in APTAS_PARLAY]
    if no_aptas:
        raise ValueError(f"Selecciones con push no admitidas en SGP: {no_aptas}")
    if len(set(GRUPO_DE[s] for s in sels)) < len(sels):
        motivo = "dos selecciones del mismo mercado"
        indiv = tuple(probabilidad(m, s) for s in sels)
        return EvaluacionSGP(sels, probabilidad(m, *sels), indiv, _prod(indiv), 0.0, False, motivo)

    indiv = tuple(probabilidad(m, s) for s in sels)
    conj = probabilidad(m, *sels)
    indep = _prod(indiv)
    lift = conj / indep if indep > 0 else 0.0
    if conj <= EPS_IMPOSIBLE:
        return EvaluacionSGP(sels, conj, indiv, indep, lift, False, "combinación imposible (P = 0)")
    if conj / min(indiv) >= ratio_redundancia:
        return EvaluacionSGP(sels, conj, indiv, indep, lift, False,
                             "redundante: una selección implica la otra")
    return EvaluacionSGP(sels, conj, indiv, indep, lift, True, "")


def _prod(xs) -> float:
    r = 1.0
    for x in xs:
        r *= x
    return r
