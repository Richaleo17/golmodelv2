"""Muestreador de incertidumbre τ/σ (el ANCHO). Diseño: v3_incertidumbre_diseno.md y
pre-registro §2.5.

Separado de calibracion_mercado (que calibra el NIVEL). Lógica pura (numpy + cfg).

Decisiones de orden (v3_incertidumbre_diseno.md, 15-16):
  * Este módulo POSEE la interfaz `FactorSampler` y su impl por defecto; NO importa
    `dependencia.py` (que, cuando llegue, consume un FactorSampler inyectado).
  * `s_i` (desv. de estimación de la fuerza del equipo) = Hessiano si se provee, si no la
    proxy `1/√peso_efectivo`. El modelo base no expone Hessiano hoy → proxy por defecto,
    Hessiano como interfaz reservada (`s_hessiano=None`).

τ_i = c_seg · s_i. c_seg se calibra por segmento con pooling seg→liga→global y fallback
global con nulo explícito (nunca τ local inventado). El gate de cobertura es BILATERAL.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol

import numpy as np


VERSION = "incertidumbre-v1"   # identificador de versión del algoritmo (auditoría de tickets)


# --------------------------------------------------------------------- factores compartidos

class FactorSampler(Protocol):
    """Lo consume `dependencia.py`. Devuelve, por sorteo, multiplicadores compartidos:
    g (global) de forma (n_draws,) y u[liga] de forma (n_draws,)."""

    def muestrear(self, ligas: list[str], n_draws: int, rng) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        ...


@dataclass
class FactorSamplerJerarquico:
    """Implementación por defecto. σ_g=0 por defecto (sin correlación global espuria)."""
    sigma_g: float = 0.0
    sigma_liga: dict | float = 0.0        # por liga (dict) o escalar

    def _s_liga(self, liga: str) -> float:
        return float(self.sigma_liga.get(liga, 0.0)) if isinstance(self.sigma_liga, dict) else float(self.sigma_liga)

    def muestrear(self, ligas: list[str], n_draws: int, rng) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        g = np.exp(rng.normal(0.0, self.sigma_g, n_draws)) if self.sigma_g > 0 else np.ones(n_draws)
        u: dict[str, np.ndarray] = {}
        for liga in ligas:
            s = self._s_liga(liga)
            u[liga] = np.exp(rng.normal(0.0, s, n_draws)) if s > 0 else np.ones(n_draws)
        return g, u


# --------------------------------------------------------------------- τ idiosincrático

def desviacion_estimacion(peso_efectivo: float, s_hessiano: float | None = None) -> float:
    """s_i. Fuente principal: Hessiano (si se provee). Proxy por defecto: 1/√peso_efectivo.
    [Diseño] el error estándar escala como 1/√(tamaño efectivo); peso_efectivo es el nº
    efectivo de partidos (ponderado por recencia), campo real `parametros_equipo.peso_efectivo`."""
    if s_hessiano is not None:
        return float(s_hessiano)
    return float(1.0 / np.sqrt(max(float(peso_efectivo), 1e-9)))


def tau_idiosincratico(c_seg: float, peso_efectivo: float, s_hessiano: float | None = None) -> float:
    return float(c_seg) * desviacion_estimacion(peso_efectivo, s_hessiano)


# --------------------------------------------------------------------- c_seg: pooling + fallback

def poolear_c(c_hijo: float, c_padre: float, n_eff: int, cfg: dict) -> float:
    """Shrinkage del escalar c_seg hacia el padre. w = n_eff/(n_eff+n0)."""
    n0 = float(cfg.get("incertidumbre", {}).get("n0", 150))
    w = n_eff / (n_eff + n0)
    return float(w * c_hijo + (1 - w) * c_padre)


@dataclass(frozen=True)
class CSegmento:
    c: float
    fuente: str                 # 'segmento' | 'global'
    calibrado: bool             # False si fallback global (nulo explícito)


def c_seg_con_fallback(segmento, registro_c: dict, c_global: float, n_eff: int, cfg: dict) -> CSegmento:
    """Fallback explícito: segmento sin datos suficientes → c global con calibrado=False;
    nunca un c local inventado. [Diseño §2.5]"""
    n_min = int(cfg.get("incertidumbre", {}).get("n_min_segmento", 200))
    seg = tuple(segmento)
    if n_eff < n_min or seg not in registro_c:
        return CSegmento(float(c_global), "global", False)
    return CSegmento(poolear_c(float(registro_c[seg]), float(c_global), n_eff, cfg), "segmento", True)


# --------------------------------------------------------------------- versionado

@dataclass(frozen=True)
class VersionIncertidumbre:
    """Registro inmutable (tabla versiones_calibrador_incertidumbre). Reconstruible as-of."""
    calibrador_version: str
    datos_hasta: datetime
    segmento: tuple
    metodo: str
    config_hash: str
    params: dict = field(default_factory=dict)   # c_seg, sigma_liga, sigma_g, n0

    def a_json(self) -> dict:
        d = asdict(self)
        d["datos_hasta"] = self.datos_hasta.isoformat()
        d["segmento"] = list(self.segmento)
        return d


# --------------------------------------------------------------------- gate de cobertura (bilateral)

def _wilson(k: int, n: int, z: float) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    phat = k / n
    d = 1 + z * z / n
    centro = (phat + z * z / (2 * n)) / d
    medio = z * np.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / d
    return max(0.0, centro - medio), min(1.0, centro + medio)


@dataclass
class ResultadoGateIncert:
    aprobado: bool
    modo: str                   # 'ok' | 'estrecho' | 'ancho' | 'insuficiente'
    cobertura: float            # fracción de buckets con frecuencia ≥ p_conservadora
    objetivo: float             # 1 − q
    bins: int
    detalle: str


def gate_cobertura_conservadora(p_conservadora: np.ndarray, y: np.ndarray, cfg: dict) -> ResultadoGateIncert:
    """Gate BILATERAL (pre-registro §2.5). Una cota q-conservadora correcta es superada por la
    realidad en ≈ (1−q) de los buckets: menos → τ/σ demasiado ESTRECHO (sub-cobertura); más →
    demasiado ANCHO (sobre-cobertura). La falta de valor económico NO se evalúa aquí (es de tickets).
    Si falla, no se despliega y no se relaja."""
    c = cfg.get("incertidumbre", {})
    q = float(c.get("q", 0.25))
    objetivo = 1.0 - q
    delta = float(c.get("delta_cobertura", 0.10))
    bins = int(c.get("gate_bins", 10))
    n_min = int(c.get("gate_n_min_bin", 50))

    p = np.asarray(p_conservadora, float)
    y = np.asarray(y, float)
    cortes = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, cortes) - 1, 0, bins - 1)
    cub = tot = 0
    for b in range(bins):
        m = idx == b
        n = int(m.sum())
        if n < n_min:
            continue
        tot += 1
        if float(y[m].mean()) >= float(p[m].mean()):
            cub += 1
    if tot == 0:
        return ResultadoGateIncert(False, "insuficiente", 0.0, objetivo, 0, "sin buckets con muestra suficiente")
    rate = cub / tot
    modo = "estrecho" if rate < objetivo - delta else "ancho" if rate > objetivo + delta else "ok"
    aprobado = modo == "ok"
    detalle = f"cobertura {rate:.2f} vs objetivo {objetivo:.2f} ±{delta} → {modo}"
    if not aprobado:
        detalle += " → NO DESPLEGAR (no se relaja el criterio)"
    return ResultadoGateIncert(aprobado, modo, float(rate), objetivo, tot, detalle)
