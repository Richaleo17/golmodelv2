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
import zlib
from dataclasses import dataclass

import numpy as np

from golmodel.modelos.incertidumbre import FactorSampler
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import mascara, probabilidad

# Guarda numérica: λ perturbada extrema (τ grande durante el ajuste) hace que la matriz
# Dixon-Coles produzca NaN al renormalizar. Se acota a un rango futbolístico sano.
LAMBDA_MIN, LAMBDA_MAX = 0.02, 12.0
VERSION = "dependencia-v1"          # identificador de versión del algoritmo (auditoría de tickets)
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
    """Conjunta de ganar TODAS las piernas, integrando la incertidumbre de parámetros. La correlación emerge de los
    factores compartidos; no se asume independencia entre partidos.

    UNA SOLA fuente de aleatoriedad (deuda 2026-11-09): esta función NO genera números aleatorios; compone las mismas
    primitivas que usa `tickets.py` (con caché): `factores_compartidos` + `p_pierna_sorteos` + `resumir_conjunta`. Así
    `calibracion_tickets` ajusta/valida sobre exactamente la distribución que `tickets.py` usa en predicción (mismos inputs
    y semilla ⇒ resultado idéntico bit a bit). Exige `partido_id` DISTINTO por pierna: es la clave del ruido idiosincrático,
    y dos piernas con el mismo id compartirían ruido (correlación espuria)."""
    if not piernas:
        raise ValueError("probabilidad_conjunta: se requiere al menos una pierna")
    ids = [p.partido_id for p in piernas]
    if len(set(ids)) != len(ids):
        raise ValueError(f"probabilidad_conjunta: partido_id repetido {sorted(i for i in set(ids) if ids.count(i) > 1)}; "
                         "cada pierna es de un partido distinto (el id fija su flujo de ruido)")
    S = int(cfg.get("dependencia", {}).get("n_draws", 20000))
    f = factores_compartidos(sampler, [p.liga for p in piernas], S, semilla)
    pares = [p_pierna_sorteos(p, f, semilla) for p in piernas]
    return resumir_conjunta([a for a, _ in pares], [pi for _, pi in pares], cfg, semilla)


def _resumir(acum: np.ndarray, p_indep: float, q: float, ic: tuple[float, float], semilla: int) -> ResultadoConjunta:
    """Estadísticos de la distribución de Π p_i (un solo np.quantile con los 3 cuantiles)."""
    S = int(acum.shape[0])
    p_media = float(acum.mean())
    cons, lo, hi = (float(x) for x in np.quantile(acum, [q, ic[0], ic[1]]))
    lift = float(p_media / p_indep) if p_indep > 0 else float("inf")
    error_mc = float(acum.std(ddof=1) / np.sqrt(S)) if S > 1 else 0.0
    metodo = "independencia" if np.allclose(acum, acum[0]) else "montecarlo_jerarquico"
    return ResultadoConjunta(p_media, cons, float(p_indep), (lo, hi), lift, error_mc, metodo, int(semilla), S)


# --------------------------------------------------------------------- caché-friendly (usado por tickets.py)
#
# Para reutilizar p_i⁽ˢ⁾ de un partido entre cestas distintas (política de caché, dec. 12), los factores
# compartidos y el ruido idiosincrático NO pueden depender de qué otras piernas acompañan al partido.
# Se generan con semillas deterministas por canal: SeedSequence([semilla, canal, liga|partido]).

@dataclass(frozen=True, eq=False)
class Factores:
    g: np.ndarray                     # (S,) factor global
    u: dict                           # liga -> (S,) factor de liga
    n_draws: int
    semilla: int


def _rng(semilla: int, *claves: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([int(semilla), *[int(c) for c in claves]]))


def factores_compartidos(sampler: FactorSampler, ligas, n_draws: int, semilla: int) -> Factores:
    """g y u_ℓ con rng propio por canal: `u[liga]` es idéntico sea cual sea el conjunto de ligas pedido."""
    g, _ = sampler.muestrear([], n_draws, _rng(semilla, 0))
    u: dict = {}
    for liga in sorted(set(ligas)):
        _, ul = sampler.muestrear([liga], n_draws, _rng(semilla, 1, zlib.crc32(liga.encode("utf-8"))))
        u[liga] = ul[liga]
    return Factores(g, u, int(n_draws), int(semilla))


def ruido_idiosincratico(p: Pierna, n_draws: int, semilla: int) -> tuple[np.ndarray, np.ndarray]:
    """(d_local, d_visitante) multiplicativos de UNA pierna: flujo `SeedSequence([semilla, 2, partido_id])`. Única fuente del
    ruido idiosincrático (la usan `p_pierna_sorteos` y la referencia de test)."""
    rng = _rng(semilla, 2, p.partido_id)
    d_loc = np.exp(rng.normal(0.0, p.tau_local, n_draws)) if p.tau_local > 0 else np.ones(n_draws)
    d_vis = np.exp(rng.normal(0.0, p.tau_visit, n_draws)) if p.tau_visit > 0 else np.ones(n_draws)
    return d_loc, d_vis


def p_pierna_sorteos(p: Pierna, f: Factores, semilla: int) -> tuple[np.ndarray, float]:
    """(p_i⁽ˢ⁾ de los S sorteos, p_i(θ̂)) de UNA pierna. Determinista en (pierna, factores, semilla):
    no depende de las demás piernas de la cesta ⇒ cacheable entre cestas."""
    d_loc, d_vis = ruido_idiosincratico(p, f.n_draws, semilla)
    lam_loc = np.clip(p.lambda_local * f.g * f.u[p.liga] * d_loc, LAMBDA_MIN, LAMBDA_MAX)
    lam_vis = np.clip(p.lambda_visitante * f.g * f.u[p.liga] * d_vis, LAMBDA_MIN, LAMBDA_MAX)
    maskM = _mascara_pierna(p.selecciones)
    arr = _p_pierna_vec(lam_loc, lam_vis, p.rho, maskM)
    p_indep = float(_p_pierna_vec(np.array([p.lambda_local]), np.array([p.lambda_visitante]), p.rho, maskM)[0])
    return arr, p_indep


def resumir_conjunta(arrays: list[np.ndarray], p_indeps: list[float], cfg: dict, semilla: int) -> ResultadoConjunta:
    """Conjunta de una cesta a partir de los p_i⁽ˢ⁾ ya calculados (de caché o frescos)."""
    dcfg = cfg.get("dependencia", {})
    q = float(dcfg.get("percentil_conservador", cfg.get("incertidumbre", {}).get("q", 0.25)))
    ic_lo, ic_hi = dcfg.get("ic", [0.025, 0.975])
    acum = arrays[0].copy()
    for a in arrays[1:]:
        acum *= a
    return _resumir(acum, float(np.prod(p_indeps)), q, (ic_lo, ic_hi), semilla)


# --------------------------------------------------------------------- referencia (bucle, solo test)

def _p_pierna(lh: float, la: float, rho: float, selecciones) -> float:
    return probabilidad(matriz_marcadores(lh, la, rho), *selecciones)


def _probabilidad_conjunta_referencia(piernas: list[Pierna], sampler: FactorSampler, cfg: dict,
                                      semilla: int) -> ResultadoConjunta:
    """Implementación de REFERENCIA con bucle Python (lenta). Solo para el test de equivalencia (@pytest.mark.slow); NO se
    usa en producción. Usa la MISMA secuencia aleatoria (`factores_compartidos` + `ruido_idiosincratico`): solo cambia el cálculo
    de p_i por sorteo (matriz Dixon-Coles en bucle vs vectorizado), de modo que coinciden dentro del error numérico."""
    dcfg = cfg.get("dependencia", {})
    S = int(dcfg.get("n_draws", 20000))
    q = float(dcfg.get("percentil_conservador", cfg.get("incertidumbre", {}).get("q", 0.25)))
    ic_lo, ic_hi = dcfg.get("ic", [0.025, 0.975])
    f = factores_compartidos(sampler, [p.liga for p in piernas], S, semilla)
    acum = np.ones(S)
    p_indep = 1.0
    for p in piernas:
        p_indep *= _p_pierna(p.lambda_local, p.lambda_visitante, p.rho, p.selecciones)
        d_loc, d_vis = ruido_idiosincratico(p, S, semilla)
        lam_loc = np.clip(p.lambda_local * f.g * f.u[p.liga] * d_loc, LAMBDA_MIN, LAMBDA_MAX)
        lam_vis = np.clip(p.lambda_visitante * f.g * f.u[p.liga] * d_vis, LAMBDA_MIN, LAMBDA_MAX)
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
