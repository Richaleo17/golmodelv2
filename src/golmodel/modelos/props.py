"""Props de jugador (anota / tiros al arco). Módulo PARALELO: usa las λ del partido que ya da Dixon-Coles, no las cambia.

Insumos por jugador (Understat, por temporada): minutos, partidos, xG y tiros. Todo lo que falta se dice:
* Titularidad ≈ minutos / (90·partidos), acotada a [0, 1]: Understat no publica titularidades; es una aproximación.
* Minutos esperados = 85·titularidad + 15·(1 − titularidad) (un suplente juega poco); configurable.
* Tiros AL ARCO: Understat da tiros totales, no al arco; se multiplica por la tasa de la liga (`tasa_al_arco`, ≈ 1/3). Provisional.
* Rival: factor = xGA del rival / xGA promedio (si no hay xG, goles concedidos). Defensas que conceden más ⇒ más tiros y goles.

ANOTA — estructural con el marcador: si el equipo hace k goles, cada gol es del jugador con probabilidad s (su cuota del xG del
equipo en el partido). P(anota | k) = 1 − (1 − s)^k. Así «anota Yamal» nunca supera «anota el Barça» y la correlación con el
marcador sale sola (lo usa `dependencia_mercados`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

VERSION = "props-v1"


@dataclass(frozen=True)
class Jugador:
    jugador_id: int
    nombre: str
    equipo_id: int
    minutos: int
    partidos: int
    xg: float
    tiros: float | None = None             # tiros totales de la temporada (None si la fuente no los da)


@dataclass(frozen=True)
class PerfilProp:
    jugador_id: int
    nombre: str
    titularidad: float
    minutos_esperados: float
    xg90: float
    tiros90: float | None
    tasa_gol_partido: float                # λ_j: xG esperado del jugador en este partido (ya con rival)
    tiros_arco_esperados: float | None
    cuota_gol_equipo: float                # s = λ_j / λ_equipo (acotada)


def titularidad(j: Jugador) -> float:
    if j.partidos <= 0:
        return 0.0
    return float(min(1.0, max(0.0, j.minutos / (90.0 * j.partidos))))


def perfil(j: Jugador, lambda_equipo: float, factor_rival: float, cfg: dict) -> PerfilProp:
    pc = cfg.get("props", {})
    min_tit, min_sup = float(pc.get("minutos_titular", 85)), float(pc.get("minutos_suplente", 15))
    tasa_arco = float(pc.get("tasa_al_arco", 0.33))
    tit = titularidad(j)
    m_esp = min_tit * tit + min_sup * (1.0 - tit)
    xg90 = 90.0 * j.xg / j.minutos if j.minutos > 0 else 0.0
    lam = xg90 * m_esp / 90.0 * factor_rival
    t90 = (90.0 * j.tiros / j.minutos) if (j.tiros is not None and j.minutos > 0) else None
    sot = (t90 * tasa_arco * m_esp / 90.0 * factor_rival) if t90 is not None else None
    s = float(min(0.95, lam / lambda_equipo)) if lambda_equipo > 0 else 0.0
    return PerfilProp(j.jugador_id, j.nombre, tit, m_esp, xg90, t90, lam, sot, s)


def p_anota_dado_goles(s: float, k) -> np.ndarray:
    """P(el jugador anota | su equipo hizo k goles) — thinning binomial."""
    return 1.0 - np.power(1.0 - s, np.asarray(k, dtype=float))


def p_anota(pf: PerfilProp, pmf_goles_equipo: np.ndarray) -> float:
    """P(anota) integrando sobre los goles de su equipo (pmf de Dixon-Coles marginal)."""
    k = np.arange(len(pmf_goles_equipo))
    return float((pmf_goles_equipo * p_anota_dado_goles(pf.cuota_gol_equipo, k)).sum())


def p_tiros_arco(pf: PerfilProp, minimo: int) -> float | None:
    """P(tiros al arco ≥ mínimo), Poisson. None si no hay dato de tiros."""
    if pf.tiros_arco_esperados is None:
        return None
    return float(poisson.sf(minimo - 1, pf.tiros_arco_esperados))


def factor_rival(xga_rival_por_partido: float | None, xga_promedio: float | None, cfg: dict) -> float:
    """xGA del rival / promedio, acotado (un rival raro no dispara el prop)."""
    if not xga_rival_por_partido or not xga_promedio:
        return 1.0
    lo, hi = cfg.get("props", {}).get("factor_rival_limites", [0.6, 1.6])
    return float(min(hi, max(lo, xga_rival_por_partido / xga_promedio)))


def pmf_marginal(matriz: np.ndarray, lado: str) -> np.ndarray:
    """PMF de goles de un equipo a partir de la matriz de marcadores (filas = local)."""
    return matriz.sum(axis=1) if lado == "local" else matriz.sum(axis=0)


def cuota_justa(p: float) -> float:
    return math.inf if p <= 0 else 1.0 / p
