"""Benchmark de mercado INDEPENDIENTE de Cerebro 1 (pre-registro §2.9).

Cerebro 1 usa una casa de referencia con Shin (Pinnacle+Shin). Cerebro 2 usa el consenso de >= N casas con
Power: fuente Y método distintos, para no auditar el mercado con la misma información que ya usó Cerebro 1.
Reutiliza las primitivas matemáticas de `modelos/devig.py`; la independencia está en la fuente y el método.

Regla dura: NUNCA se inventa una cuota. Una casa esperada sin cuota, o con el grupo incompleto o inválido, se
descarta y se registra; si quedan menos de N casas no hay consenso (p_mercado = None).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from golmodel.modelos import devig
from golmodel.modelos.mercados import GRUPO_DE, GRUPOS

FUENTE_DEVIG = "consenso_multicasa"
_METODO = {"power": "potencia", "shin": "shin"}        # nombre en config -> función de devig.py


@dataclass(frozen=True)
class MercadoPierna:
    """Cuotas CRUDAS de varias casas para el grupo de devig de la selección de una pierna."""
    cuotas_por_casa: Mapping[str, Mapping[str, float]]    # casa -> {selección: cuota} de TODO el grupo
    casas_esperadas: tuple[str, ...] = ()
    lote: str | None = None
    p_mercado_c1: float | None = None                    # lo que usó Cerebro 1 (solo para comprobar independencia)
    fuente_devig_c1: str | None = None
    metodo_devig_c1: str | None = None


@dataclass(frozen=True)
class ResultadoConsenso:
    estado: str                         # 'ok' | 'consenso_insuficiente' | 'consenso_incompleto' | 'no_auditable'
    razon: str | None
    p_mercado: float | None
    fuente_devig: str
    metodo_devig: str
    casas_usadas: tuple[str, ...] = ()
    casas_esperadas: tuple[str, ...] = ()
    casas_faltantes: tuple[str, ...] = ()
    casas_descartadas: dict = field(default_factory=dict)
    n_min: int = 3
    lote: str | None = None
    independiente_de_c1: bool | None = None


def js_bits(p: float, q: float) -> float:
    """Divergencia de Jensen-Shannon (bits) entre dos Bernoulli. Simétrica y acotada en [0, 1]; se descarta KL."""
    P, Q = np.array([p, 1.0 - p]), np.array([q, 1.0 - q])
    M = (P + Q) / 2.0

    def kl(a, b):
        m = a > 0
        return float(np.sum(a[m] * np.log2(a[m] / b[m])))
    return 0.5 * kl(P, M) + 0.5 * kl(Q, M)


def _cuotas_validas(cuotas: Mapping[str, float] | None, grupo: tuple[str, ...]) -> tuple[list[float] | None, str | None]:
    if cuotas is None:
        return None, "sin_cuotas"
    faltan = [s for s in grupo if s not in cuotas]
    if faltan:
        return None, f"grupo_incompleto:{','.join(faltan)}"
    out = []
    for s in grupo:
        try:
            c = float(cuotas[s])
        except (TypeError, ValueError):
            return None, f"cuota_invalida:{s}"
        if not math.isfinite(c) or c <= 1.0:
            return None, f"cuota_invalida:{s}"
        out.append(c)
    return out, None


def consenso_independiente(selecciones: tuple[str, ...], mercado: MercadoPierna | None, cfg: dict) -> ResultadoConsenso:
    c = cfg["cerebro2"]["consenso_mercado"]
    metodo_cfg, n_min = c["metodo_devig"], int(c["n_min"])
    base = dict(fuente_devig=FUENTE_DEVIG, metodo_devig=metodo_cfg, n_min=n_min)
    if len(selecciones) != 1:
        return ResultadoConsenso("no_auditable", "pierna_sgp_sin_grupo_devig", None, **base,
                                 lote=getattr(mercado, "lote", None))
    sel = selecciones[0]
    grupo_nombre = GRUPO_DE.get(sel)
    if grupo_nombre not in GRUPOS:                       # p. ej. doble oportunidad: derivada, no exhaustiva
        return ResultadoConsenso("no_auditable", "seleccion_sin_grupo_devig_exhaustivo", None, **base)
    if mercado is None:
        return ResultadoConsenso("no_auditable", "sin_cuotas_de_mercado", None, **base)

    grupo = GRUPOS[grupo_nombre]
    elegibles = set(c["casas_elegibles"])
    esperadas = tuple(mercado.casas_esperadas) or tuple(c["casas_elegibles"])
    cuotas: dict[str, list[float]] = {}
    faltantes, descartadas = [], {}
    for casa in esperadas:
        if casa not in elegibles:
            descartadas[casa] = "no_elegible"
            continue
        valores, motivo = _cuotas_validas(mercado.cuotas_por_casa.get(casa), grupo)
        if valores is None:
            if motivo == "sin_cuotas":
                faltantes.append(casa)             # esperada y sin cuota: se registra, NUNCA se imputa
            else:
                descartadas[casa] = motivo         # cotiza pero el grupo está incompleto / cuota inválida
            continue
        cuotas[casa] = valores
    usadas = tuple(sorted(cuotas))
    comunes = dict(base, casas_usadas=usadas, casas_esperadas=esperadas, casas_faltantes=tuple(faltantes),
                   casas_descartadas=descartadas, lote=mercado.lote)
    independiente = None
    if mercado.fuente_devig_c1 is not None and mercado.metodo_devig_c1 is not None:
        independiente = (FUENTE_DEVIG != mercado.fuente_devig_c1) and (metodo_cfg != mercado.metodo_devig_c1)
    comunes["independiente_de_c1"] = independiente
    if len(usadas) < n_min:
        estado = "consenso_incompleto" if len(esperadas) >= n_min else "consenso_insuficiente"
        return ResultadoConsenso(estado, f"{estado}:usadas={len(usadas)}<{n_min}", None, **comunes)
    p_group = devig.consenso(cuotas, metodo=_METODO[metodo_cfg], min_casas=n_min)
    return ResultadoConsenso("ok", None, float(p_group[grupo.index(sel)]), **comunes)
