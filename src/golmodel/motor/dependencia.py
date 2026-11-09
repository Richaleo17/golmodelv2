"""Probabilidad conjunta REAL de un ticket de varias piernas. Diseño:
v3_dependencia_diseno.md y pre-registro §2.6.

Sustituye la multiplicación por independencia (`math.prod`) del cross-partido de
`modulo2.py`. Método: Monte Carlo jerárquico con factores latentes compartidos,
Rao-Blackwellizado (producto analítico de piernas condicionado a cada sorteo).

Consume (contrato §2.6): un `FactorSampler` inyectado (de `incertidumbre`), los `τ_i`
y, por pierna, `(λ_local, λ_visit, ρ, liga, selecciones)`. NO reimplementa el muestreo
de factores. El SGP intra-partido se evalúa sumando celdas de la matriz (vía
`mercados.probabilidad(M, *sels)`), nunca multiplicando.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from golmodel.modelos.incertidumbre import FactorSampler
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import probabilidad


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


def _p_pierna(lh: float, la: float, rho: float, selecciones) -> float:
    m = matriz_marcadores(lh, la, rho)
    return probabilidad(m, *selecciones)


def probabilidad_conjunta(piernas: list[Pierna], sampler: FactorSampler, cfg: dict,
                          semilla: int) -> ResultadoConjunta:
    """Conjunta de ganar TODAS las piernas, integrando la incertidumbre de parámetros.

    Rao-Blackwell: condicionado a cada sorteo de factores (g, u_ℓ, δ_i), las piernas son
    independientes, así que se toma el producto ANALÍTICO de sus p_i (baja varianza MC).
    La correlación emerge de los factores compartidos; no se asume independencia entre partidos.
    """
    dcfg = cfg.get("dependencia", {})
    S = int(dcfg.get("n_draws", 20000))
    q = float(dcfg.get("percentil_conservador", cfg.get("incertidumbre", {}).get("q", 0.25)))
    ic_lo, ic_hi = dcfg.get("ic", [0.025, 0.975])
    rng = np.random.default_rng(semilla)

    ligas = sorted({p.liga for p in piernas})
    g, u = sampler.muestrear(ligas, S, rng)            # g: (S,), u[liga]: (S,)

    acum = np.ones(S)
    p_indep = 1.0
    for p in piernas:
        p_indep *= _p_pierna(p.lambda_local, p.lambda_visitante, p.rho, p.selecciones)
        d_loc = np.exp(rng.normal(0.0, p.tau_local, S)) if p.tau_local > 0 else np.ones(S)
        d_vis = np.exp(rng.normal(0.0, p.tau_visit, S)) if p.tau_visit > 0 else np.ones(S)
        lam_loc = p.lambda_local * g * u[p.liga] * d_loc
        lam_vis = p.lambda_visitante * g * u[p.liga] * d_vis
        pi = np.fromiter(
            (_p_pierna(float(lam_loc[s]), float(lam_vis[s]), p.rho, p.selecciones) for s in range(S)),
            dtype=float, count=S,
        )
        acum *= pi

    p_media = float(acum.mean())
    p_cons = float(np.quantile(acum, q))
    ic = (float(np.quantile(acum, ic_lo)), float(np.quantile(acum, ic_hi)))
    lift = float(p_media / p_indep) if p_indep > 0 else float("inf")
    error_mc = float(acum.std(ddof=1) / np.sqrt(S)) if S > 1 else 0.0
    # Reducción a independencia: sin variación entre sorteos ⇒ es el producto ingenuo (invariante §2.6).
    metodo = "independencia" if np.allclose(acum, acum[0]) else "montecarlo_jerarquico"
    return ResultadoConjunta(p_media, p_cons, float(p_indep), ic, lift, error_mc, metodo, int(semilla), S)
