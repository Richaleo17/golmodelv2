"""Probabilidad conjunta REAL de un ticket de varias piernas. Diseño:
v3_dependencia_diseno.md y pre-registro §2.6.

Sustituye la multiplicación por independencia (`math.prod`) del cross-partido de
`modulo2.py`. Método: Monte Carlo jerárquico con factores latentes compartidos,
Rao-Blackwellizado (producto analítico de piernas condicionado a cada sorteo).

Producción: VECTORIZADA sobre los S sorteos (construye las 121 celdas para los S
valores de λ de una vez con numpy, sin bucle Python). El bucle queda solo como
REFERENCIA de test (`_probabilidad_conjunta_referencia`, marcado @pytest.mark.slow).

Consume (contrato §2.6): un `FactorSampler` inyectado (de `incertidumbre`), los `τ_i`
y, por pierna, `(λ_local, λ_visit, ρ, liga, selecciones)`. No reimplementa el muestreo
de factores. El SGP intra-partido se evalúa por intersección de celdas de la matriz.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from golmodel.modelos.incertidumbre import FactorSampler
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import mascara, probabilidad

# Guarda numérica: λ perturbada extrema (τ grande durante el ajuste) hace que la matriz
# Dixon-Coles produzca NaN al renormalizar. Se acota a un rango futbolístico sano.
LAMBDA_MIN, LAMBDA_MAX = 0.02, 12.0
GMAX = 10
_G = np.arange(GMAX + 1)
_FACT = np.array([math.factorial(k) for k in range(GMAX + 1)], dtype=float)


@dataclass(frozen=True)
class Pierna:
    partido_id: int
    liga: str
    lambda_local: float
    lambda_visitante: float
    rho: float
    selecciones: tuple[str, ...]      # 1 (simple) o 2 (SGP del mismo partido)
    tau_local: float = 0.0            # idiosincrático (de incertidumbre); 0 = sin ruido propio
    tau_visit: float = 0.0


@dataclass(frozen=True)
class ResultadoConjunta:
    p_media: float                    # E_θ[Π p_i] — conjunta honesta
    p_conservadora: float             # percentil q de la distribución de Π p_i
    p_independencia: float            # Π p_i(θ̂) — el número ingenuo (se conserva y muestra, §10.1-F)
    ic: tuple[float, float]
    lift: float                       # p_media / p_independencia
    error_mc: float
    metodo: str                       # 'montecarlo_jerarquico' | 'independencia'
    semilla: int
    n_draws: int


# --------------------------------------------------------------------- núcleo vectorizado

def _mascara_pierna(selecciones) -> np.ndarray:
    m = np.ones((GMAX + 1, GMAX + 1), dtype=bool)
    for s in selecciones:
        m &= mascara(s, GMAX + 1)
    return m.astype(float)                                 # (11,11) 0/1


def _pois(lam: np.ndarray) -> np.ndarray:
    """PMF Poisson(0..GMAX) para λ (S,) → (S, GMAX+1). Manual y sin np.power: las potencias
    λ^0..λ^GMAX se arman por producto acumulado (más rápido que `lam ** _G`)."""
    S = lam.shape[0]
    pw = np.empty((S, GMAX + 1))
    pw[:, 0] = 1.0
    for k in range(1, GMAX + 1):
        pw[:, k] = pw[:, k - 1] * lam
    return np.exp(-lam)[:, None] * pw / _FACT[None, :]


def _p_pierna_vec(lam_loc: np.ndarray, lam_vis: np.ndarray, rho: float, maskM: np.ndarray) -> np.ndarray:
    """p_i para los S sorteos (S,). Replica matriz_marcadores (Dixon-Coles + clip de ρ) SIN
    construir la matriz (S,11,11): usa (ph @ maskM)·pa + corrección de las 4 celdas DC."""
    lo = np.maximum(-1.0 / lam_loc, -1.0 / lam_vis)
    hi = np.minimum(1.0 / (lam_loc * lam_vis), 1.0)
    rho_ef = np.clip(rho, lo, hi)                          # (S,)
    ph = _pois(lam_loc)                                    # (S, 11)
    pa = _pois(lam_vis)
    # Correcciones DC: (factor − 1)·ph_i·pa_j en las celdas (0,0),(0,1),(1,0),(1,1).
    d00 = ph[:, 0] * pa[:, 0] * (-lam_loc * lam_vis * rho_ef)
    d01 = ph[:, 0] * pa[:, 1] * (lam_loc * rho_ef)
    d10 = ph[:, 1] * pa[:, 0] * (lam_vis * rho_ef)
    d11 = ph[:, 1] * pa[:, 1] * (-rho_ef)
    masa = ph.sum(1) * pa.sum(1) + d00 + d01 + d10 + d11   # Σ_ij ph·pa·τ
    masked = ((ph @ maskM) * pa).sum(1)                    # Σ_{(i,j)∈mask} ph·pa (sin τ)
    if maskM[0, 0]:
        masked = masked + d00
    if maskM[0, 1]:
        masked = masked + d01
    if maskM[1, 0]:
        masked = masked + d10
    if maskM[1, 1]:
        masked = masked + d11
    return masked / masa


def probabilidad_conjunta(piernas: list[Pierna], sampler: FactorSampler, cfg: dict,
                          semilla: int) -> ResultadoConjunta:
    """Conjunta de ganar TODAS las piernas, integrando la incertidumbre de parámetros
    (VECTORIZADA sobre sorteos). La correlación emerge de los factores compartidos; no se
    asume independencia entre partidos."""
    dcfg = cfg.get("dependencia", {})
    S = int(dcfg.get("n_draws", 20000))
    q = float(dcfg.get("percentil_conservador", cfg.get("incertidumbre", {}).get("q", 0.25)))
    ic_lo, ic_hi = dcfg.get("ic", [0.025, 0.975])
    rng = np.random.default_rng(semilla)

    ligas = sorted({p.liga for p in piernas})
    g, u = sampler.muestrear(ligas, S, rng)                # g: (S,), u[liga]: (S,)

    acum = np.ones(S)
    p_indep = 1.0
    for p in piernas:
        maskM = _mascara_pierna(p.selecciones)
        p_indep *= float(_p_pierna_vec(
            np.array([p.lambda_local]), np.array([p.lambda_visitante]), p.rho, maskM)[0])
        d_loc = np.exp(rng.normal(0.0, p.tau_local, S)) if p.tau_local > 0 else np.ones(S)
        d_vis = np.exp(rng.normal(0.0, p.tau_visit, S)) if p.tau_visit > 0 else np.ones(S)
        lam_loc = np.clip(p.lambda_local * g * u[p.liga] * d_loc, LAMBDA_MIN, LAMBDA_MAX)
        lam_vis = np.clip(p.lambda_visitante * g * u[p.liga] * d_vis, LAMBDA_MIN, LAMBDA_MAX)
        acum *= _p_pierna_vec(lam_loc, lam_vis, p.rho, maskM)

    p_media = float(acum.mean())
    p_cons = float(np.quantile(acum, q))
    ic = (float(np.quantile(acum, ic_lo)), float(np.quantile(acum, ic_hi)))
    lift = float(p_media / p_indep) if p_indep > 0 else float("inf")
    error_mc = float(acum.std(ddof=1) / np.sqrt(S)) if S > 1 else 0.0
    metodo = "independencia" if np.allclose(acum, acum[0]) else "montecarlo_jerarquico"
    return ResultadoConjunta(p_media, p_cons, float(p_indep), ic, lift, error_mc, metodo, int(semilla), S)


# --------------------------------------------------------------------- referencia (bucle, solo test)

def _p_pierna(lh: float, la: float, rho: float, selecciones) -> float:
    return probabilidad(matriz_marcadores(lh, la, rho), *selecciones)


def _probabilidad_conjunta_referencia(piernas: list[Pierna], sampler: FactorSampler, cfg: dict,
                                      semilla: int) -> ResultadoConjunta:
    """Implementación de REFERENCIA con bucle Python (lenta). Solo para el test de
    equivalencia (@pytest.mark.slow); NO se usa en producción. Mismo orden de consumo de
    aleatorios que la vectorizada, para que coincidan dentro del error MC."""
    dcfg = cfg.get("dependencia", {})
    S = int(dcfg.get("n_draws", 20000))
    q = float(dcfg.get("percentil_conservador", cfg.get("incertidumbre", {}).get("q", 0.25)))
    ic_lo, ic_hi = dcfg.get("ic", [0.025, 0.975])
    rng = np.random.default_rng(semilla)
    ligas = sorted({p.liga for p in piernas})
    g, u = sampler.muestrear(ligas, S, rng)
    acum = np.ones(S)
    p_indep = 1.0
    for p in piernas:
        p_indep *= _p_pierna(p.lambda_local, p.lambda_visitante, p.rho, p.selecciones)
        d_loc = np.exp(rng.normal(0.0, p.tau_local, S)) if p.tau_local > 0 else np.ones(S)
        d_vis = np.exp(rng.normal(0.0, p.tau_visit, S)) if p.tau_visit > 0 else np.ones(S)
        lam_loc = np.clip(p.lambda_local * g * u[p.liga] * d_loc, LAMBDA_MIN, LAMBDA_MAX)
        lam_vis = np.clip(p.lambda_visitante * g * u[p.liga] * d_vis, LAMBDA_MIN, LAMBDA_MAX)
        pi = np.fromiter((_p_pierna(float(lam_loc[s]), float(lam_vis[s]), p.rho, p.selecciones)
                          for s in range(S)), dtype=float, count=S)
        acum *= pi
    p_media = float(acum.mean())
    return ResultadoConjunta(
        p_media, float(np.quantile(acum, q)), float(p_indep),
        (float(np.quantile(acum, ic_lo)), float(np.quantile(acum, ic_hi))),
        float(p_media / p_indep) if p_indep > 0 else float("inf"),
        float(acum.std(ddof=1) / np.sqrt(S)) if S > 1 else 0.0,
        "independencia" if np.allclose(acum, acum[0]) else "montecarlo_jerarquico", int(semilla), S)
