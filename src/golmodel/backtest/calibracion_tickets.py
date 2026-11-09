"""Calibración de τ/σ sobre cestas (el ANCHO, ajuste). Diseño: v3_incertidumbre_diseno.md
y pre-registro §2.5/§2.7.

Cierra el bucle: ajusta `c_seg` (escala de τ) minimizando el log-loss del indicador
"ganan todas" sobre **cestas ex-post walk-forward** (contrato: el llamador provee ventanas
ya separadas en el tiempo), y valida con el **gate de cobertura BILATERAL** de `incertidumbre`
a nivel de TICKET. Usa la MC de `dependencia.py`.

v1: ajusta un `c` GLOBAL (escalar) y marca por segmento calibrado/fallback (≥ n_min cestas →
propio; si no, global con nulo explícito). Ajustar un `c` DISTINTO por segmento y el ajuste de
σ_ℓ quedan como extensión documentada. σ_g=0 por defecto (protocolo).

OFFLINE (backtest): tolera lentitud, pero ver el presupuesto de §2.7 y la deuda técnica de
rendimiento (vectorización requerida antes de `tickets.py`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import minimize_scalar

from golmodel.modelos import incertidumbre as inc
from golmodel.motor import dependencia as dep

EPS = 1e-12


@dataclass(frozen=True)
class PiernaHistorica:
    liga: str
    segmento: tuple                 # (liga, mercado, rango)
    lambda_local: float
    lambda_visitante: float
    rho: float
    selecciones: tuple
    peso_efectivo_local: float
    peso_efectivo_visitante: float


@dataclass(frozen=True)
class CestaHistorica:
    piernas: list[PiernaHistorica]
    gano: bool                      # resultado real: ¿ganaron TODAS las piernas?


# --------------------------------------------------------------------- construcción

def _a_pierna(ph: PiernaHistorica, c: float) -> dep.Pierna:
    """PiernaHistorica → dep.Pierna, con τ = c·(1/√peso_efectivo) (proxy por defecto)."""
    return dep.Pierna(
        partido_id=0, liga=ph.liga,
        lambda_local=ph.lambda_local, lambda_visitante=ph.lambda_visitante, rho=ph.rho,
        selecciones=ph.selecciones,
        tau_local=inc.tau_idiosincratico(c, ph.peso_efectivo_local),
        tau_visit=inc.tau_idiosincratico(c, ph.peso_efectivo_visitante),
    )


def _cfg_dep(cfg: dict) -> dict:
    c = cfg.get("calibracion_tickets", {})
    return {"dependencia": {"n_draws": int(c.get("n_draws", 20000)),
                            "percentil_conservador": float(c.get("q", 0.25)),
                            "ic": [0.025, 0.975]}}


def _sampler(cfg: dict) -> inc.FactorSamplerJerarquico:
    c = cfg.get("calibracion_tickets", {})
    return inc.FactorSamplerJerarquico(sigma_g=float(c.get("sigma_g", 0.0)),
                                       sigma_liga=c.get("sigma_liga", 0.0))


def _resultados(cestas: list[CestaHistorica], c: float, cfg: dict, semilla: int):
    sampler = _sampler(cfg)
    cfgd = _cfg_dep(cfg)
    out = []
    for i, cesta in enumerate(cestas):
        piernas = [_a_pierna(ph, c) for ph in cesta.piernas]
        out.append(dep.probabilidad_conjunta(piernas, sampler, cfgd, semilla + i))
    return out


# --------------------------------------------------------------------- coste y ajuste

def coste(cestas: list[CestaHistorica], c: float, cfg: dict, semilla: int) -> float:
    """Log-loss medio del indicador 'ganan todas' bajo p_media (regla de puntuación propia)."""
    res = _resultados(cestas, c, cfg, semilla)
    y = np.array([1.0 if k.gano else 0.0 for k in cestas])
    p = np.clip(np.array([r.p_media for r in res]), EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def segmentos_cestas(cestas: list[CestaHistorica]) -> dict[tuple, int]:
    cuenta: dict[tuple, int] = {}
    for cesta in cestas:
        for ph in cesta.piernas:
            cuenta[tuple(ph.segmento)] = cuenta.get(tuple(ph.segmento), 0) + 1
    return cuenta


@dataclass
class ResultadoAjuste:
    c_global: float
    coste: float
    segmentos_calibrados: list      # ≥ n_min cestas
    segmentos_fallback: list        # fallback global con nulo


def ajustar(cestas: list[CestaHistorica], cfg: dict, semilla: int) -> ResultadoAjuste:
    """Ajusta c GLOBAL minimizando el log-loss de ticket. Marca segmentos calibrados vs fallback.
    Ventanas ya separadas en el tiempo por el llamador walk-forward (contrato anti-leakage)."""
    def coste_log(logc: float) -> float:
        return coste(cestas, float(np.exp(logc)), cfg, semilla)

    r = minimize_scalar(coste_log, bounds=(np.log(0.1), np.log(5.0)), method="bounded")
    c_global = float(np.exp(r.x))
    n_min = int(cfg.get("calibracion_tickets", {}).get("n_min_cestas_segmento", 200))
    cuenta = segmentos_cestas(cestas)
    calibrados = sorted([list(s) for s, n in cuenta.items() if n >= n_min])
    fallback = sorted([list(s) for s, n in cuenta.items() if n < n_min])
    return ResultadoAjuste(c_global, float(r.fun), calibrados, fallback)


# --------------------------------------------------------------------- gate (bilateral, nivel ticket)

def gate_cobertura_tickets(cestas: list[CestaHistorica], c: float, cfg: dict, semilla: int) -> inc.ResultadoGateIncert:
    """Reusa el gate BILATERAL de incertidumbre sobre la p_conservadora de cada cesta vs 'ganan todas'."""
    res = _resultados(cestas, c, cfg, semilla)
    p_cons = np.array([r.p_conservadora for r in res])
    y = np.array([1 if k.gano else 0 for k in cestas])
    ct = cfg.get("calibracion_tickets", {})
    subcfg = {"incertidumbre": {"q": float(ct.get("q", 0.25)),
                                "delta_cobertura": float(ct.get("delta_cobertura", 0.10)),
                                "gate_bins": int(ct.get("gate_bins", 8)),
                                "gate_n_min_bin": int(ct.get("gate_n_min_bin", 100))}}
    return inc.gate_cobertura_conservadora(p_cons, y, subcfg)


# --------------------------------------------------------------------- versionado

@dataclass(frozen=True)
class VersionCalibradorTickets:
    """Registro inmutable (reproducible as-of). calibrado_con='sinteticos' NO desactiva modo_experimental."""
    datos_hasta: datetime
    config_hash: str
    n_draws: int
    semilla: int
    metodo: str
    segmentos_calibrados: list
    segmentos_fallback: list
    c_global: float
    sigma_liga: dict | float = 0.0
    sigma_g: float = 0.0
    calibrado_con: str = "no_calibrado"   # 'sinteticos' | 'oos_real'

    def a_json(self) -> dict:
        d = asdict(self)
        d["datos_hasta"] = self.datos_hasta.isoformat()
        return d


# --------------------------------------------------------------------- contrato de consumo (tickets.py)

def verificar_para_tickets(version: "VersionCalibradorTickets | None", modo_experimental: bool) -> str:
    """Contrato de consumo de tickets.py (pre-registro §2.7). fail-closed si no hay versión;
    devuelve el techo de estado. tickets.py además registra `version` en auditoria_decisiones."""
    if version is None:
        raise ValueError("VersionCalibradorTickets ausente: fail-closed; tickets.py no opera sin calibrador.")
    return "CON_RESERVAS" if modo_experimental else "SIN_TOPE"
