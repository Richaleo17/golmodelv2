"""Valor esperado y umbrales según la calidad del dato.

Cuanto menos confiable es la probabilidad (α sin calibrar, casa de
referencia sustituta, SGP), más alto es el umbral para declarar valor.
"""
from __future__ import annotations

from dataclasses import dataclass


def ev(p: float, cuota: float) -> float:
    if not 0.0 <= p <= 1.0 or cuota <= 1.0:
        raise ValueError(f"p={p}, cuota={cuota} inválidos")
    return p * cuota - 1.0


def cuota_justa(p: float) -> float | None:
    return None if p <= 0 else 1.0 / p


def ev_con_push(p_gana: float, p_pierde: float, cuota: float) -> float:
    """En push se devuelve la apuesta: EV = p_g·(cuota − 1) − p_p."""
    return p_gana * (cuota - 1.0) - p_pierde


def cuota_justa_con_push(p_gana: float, p_pierde: float) -> float | None:
    """Cuota con EV = 0: 1 + p_pierde / p_gana."""
    return None if p_gana <= 0 else 1.0 + p_pierde / p_gana


@dataclass(frozen=True)
class Umbral:
    valor: float
    razones: tuple[str, ...]


def umbral_efectivo(cfg: dict, *, sgp: bool = False, sgp_con_mercado: bool = True,
                    alpha_calibrado: bool = True, referencia_sustituta: bool = False) -> Umbral:
    v, m = cfg["valor"], cfg["mercado"]
    if sgp:
        base = v["umbral_edge_sgp"] if sgp_con_mercado else v["umbral_edge_sgp_sin_mercado"]
        razones = ["SGP" if sgp_con_mercado else "SGP sin matriz de mercado"]
    else:
        base, razones = v["umbral_edge"], []
    if not alpha_calibrado:
        base += m["umbral_extra_no_calibrado"]
        razones.append("α no calibrado")
    if referencia_sustituta:
        base += m["umbral_extra_referencia_sustituta"]
        razones.append("sin casa de referencia")
    return Umbral(round(base, 6), tuple(razones))


def clasificar(ev_: float, umbral: Umbral) -> str:
    if ev_ >= umbral.valor:
        return "valor"          # verde
    if ev_ > 0:
        return "marginal"       # gris
    return "sin_valor"
