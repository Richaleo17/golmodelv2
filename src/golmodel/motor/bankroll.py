"""Gestión de banca: Kelly fraccional, topes y exposición diaria.

[Literatura] Kelly (1956): f* = (b·p − q) / b, b = cuota − 1.
Con push (AH entero): maximizar p_g·log(1+f·b) + p_p·log(1−f) da
f* = (b·p_g − p_p) / (b·(p_g + p_p)).
[Diseño] Fracciones, topes y límite diario vienen de config.yaml.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def kelly(p: float, cuota: float) -> float:
    b = cuota - 1.0
    return (b * p - (1.0 - p)) / b


def kelly_push(p_gana: float, p_pierde: float, cuota: float) -> float:
    b = cuota - 1.0
    if p_gana + p_pierde <= 0:
        return 0.0
    return (b * p_gana - p_pierde) / (b * (p_gana + p_pierde))


def log_crecimiento(p: float, cuota: float, f: float) -> float:
    """E[log(1 + retorno)] apostando la fracción f de la banca."""
    if f <= 0:
        return 0.0
    if f >= 1:
        return -math.inf
    return p * math.log1p(f * (cuota - 1.0)) + (1.0 - p) * math.log1p(-f)


def log_crecimiento_push(p_gana: float, p_pierde: float, cuota: float, f: float) -> float:
    if f <= 0:
        return 0.0
    return p_gana * math.log1p(f * (cuota - 1.0)) + p_pierde * math.log1p(-f)


def stake_u(f_estrella: float, *, fraccion: float, max_u: float, unidad_pct: float) -> float:
    """Stake en unidades (1u = unidad_pct de la banca). 0 si f* <= 0."""
    if f_estrella <= 0:
        return 0.0
    return round(min(fraccion * f_estrella / unidad_pct, max_u), 3)


@dataclass
class Apuesta:
    id: str
    tipo: str                       # 'simple' | 'parlay'
    partidos: frozenset[int]
    stake_u: float
    log_crec: float                 # con el stake propuesto
    stake_final_u: float = 0.0
    nota: str = ""


@dataclass
class Asignacion:
    apuestas: list[Apuesta]
    exposicion_u: float
    excluidas: list[Apuesta] = field(default_factory=list)


def asignar_exposicion(apuestas: list[Apuesta], *, max_diaria_u: float,
                       max_simples_por_partido: int = 1) -> Asignacion:
    """Llena el límite diario en orden de crecimiento logarítmico; la última se recorta.

    Simples y parlays comparten el límite. Un partido que aparece en varias
    apuestas cuenta en cada una (exposición correlacionada).
    """
    restante = max_diaria_u
    simples_por_partido: dict[int, int] = {}
    aceptadas, excluidas = [], []
    for a in sorted(apuestas, key=lambda x: x.log_crec, reverse=True):
        if a.stake_u <= 0:
            a.nota = "sin stake (f* <= 0)"
            excluidas.append(a)
            continue
        if a.tipo == "simple":
            (pid,) = tuple(a.partidos)
            if simples_por_partido.get(pid, 0) >= max_simples_por_partido:
                a.nota = "ya hay una simple con stake en este partido"
                excluidas.append(a)
                continue
        if restante <= 0:
            a.nota = "límite diario alcanzado"
            excluidas.append(a)
            continue
        a.stake_final_u = round(min(a.stake_u, restante), 3)
        if a.stake_final_u < a.stake_u:
            a.nota = f"recortada por límite diario ({a.stake_u} -> {a.stake_final_u})"
        restante -= a.stake_final_u
        if a.tipo == "simple":
            simples_por_partido[pid] = simples_por_partido.get(pid, 0) + 1
        aceptadas.append(a)
    return Asignacion(aceptadas, round(max_diaria_u - restante, 3), excluidas)
