"""Política versionada del Cerebro 2: umbrales pre-registrados (config/v3_umbrales.yaml) + frescura (config.yaml).

Ningún umbral vive en el código. El hash de la política identifica con qué reglas se tomó cada decisión;
mismos inputs + misma `CEREBRO2_VERSION` + mismo `hash` → mismo estado (decisión 6).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

CEREBRO2_VERSION = "cerebro2_v0.1"
RUTA_UMBRALES = Path(__file__).resolve().parents[3] / "config" / "v3_umbrales.yaml"

# Nombres del YAML de V3 → nombres de golmodel.modelos.devig
_METODOS_DEVIG = {"power": "potencia", "potencia": "potencia"}


@dataclass(frozen=True)
class Politica:
    modo_experimental: bool
    # Consenso de mercado (estructural, no se valida OOS)
    casas_elegibles: tuple[str, ...]
    metodo_devig: str
    n_min_casas: int
    # Umbrales provisionales U4–U10
    lift_max: float
    ic_max_rel: float
    umbral_ev_cons_ticket: float
    d_mm_edge_max: float
    d_mm_rechazo: float
    js_degradar_bits: float
    k_piernas_por_liga: int
    tasa_rechazos_correctos_min: float
    # Muestras y niveles de confianza
    calibracion_n_min: int
    rechazos_n_min: int
    wilson_ic: float
    bootstrap_ic: float
    # Frescura (config.yaml, la misma que usa el Cerebro 1 para no apostar con datos viejos)
    modelo_max_dias: float
    cuotas_max_min: float

    @property
    def hash(self) -> str:
        canon = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canon.encode()).hexdigest()[:16]

    def a_json(self) -> dict[str, Any]:
        return {**asdict(self), "casas_elegibles": list(self.casas_elegibles), "hash": self.hash,
                "cerebro2_version": CEREBRO2_VERSION}


def _exigir(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(f"v3_umbrales.yaml: {msg}")


def desde_dicts(v3: dict[str, Any], cfg: dict[str, Any]) -> Politica:
    cons = v3["cerebro2"]["consenso_mercado"]
    u = v3["umbrales_provisionales"]
    val = v3["validacion"]
    fr = cfg["frescura"]
    metodo = _METODOS_DEVIG.get(str(cons["metodo_devig"]).lower())
    _exigir(metodo is not None, f"metodo_devig desconocido: {cons['metodo_devig']}")
    _exigir(metodo != cfg["mercado"]["metodo_devig"],
            "el consenso del Cerebro 2 debe usar un devig distinto al del Cerebro 1 (anti-circularidad)")
    _exigir(cons["agregacion"] == "mediana", "la agregación pre-registrada es la mediana")
    _exigir(cons["renormalizar_grupo"] is True, "el consenso debe renormalizar el grupo")
    _exigir(int(cons["n_min"]) >= 3, "n_min del consenso debe ser >= 3")
    _exigir(0 < u["d_mm_edge_max_pp"] < u["d_mm_rechazo_pp"] < 1, "se requiere 0 < d_mm edge < d_mm rechazo < 1")
    _exigir(u["lift_max"] >= 1, "lift_max debe ser >= 1")
    _exigir(u["ic_max_rel"] > 0 and u["umbral_ev_cons_ticket"] >= 0, "ic_max_rel > 0 y umbral EV_cons >= 0")
    _exigir(int(u["k_piernas_por_liga"]) >= 1, "k_piernas_por_liga >= 1")
    _exigir(0 < val["wilson_ic"] < 1 and 0 < val["bootstrap_ic"] < 1, "niveles de confianza en (0, 1)")
    return Politica(
        modo_experimental=bool(v3["modo_experimental"]),
        casas_elegibles=tuple(str(c).lower() for c in cons["casas_elegibles"]),
        metodo_devig=metodo,
        n_min_casas=int(cons["n_min"]),
        lift_max=float(u["lift_max"]),
        ic_max_rel=float(u["ic_max_rel"]),
        umbral_ev_cons_ticket=float(u["umbral_ev_cons_ticket"]),
        d_mm_edge_max=float(u["d_mm_edge_max_pp"]),
        d_mm_rechazo=float(u["d_mm_rechazo_pp"]),
        js_degradar_bits=float(u["js_degradar_bits"]),
        k_piernas_por_liga=int(u["k_piernas_por_liga"]),
        tasa_rechazos_correctos_min=float(u["tasa_rechazos_correctos_min"]),
        calibracion_n_min=int(val["cobertura_min_neff_bucket"]),
        rechazos_n_min=int(val["discriminacion_min_por_lado"]),
        wilson_ic=float(val["wilson_ic"]),
        bootstrap_ic=float(val["bootstrap_ic"]),
        modelo_max_dias=float(fr["modelo_max_dias"]),
        cuotas_max_min=float(fr["cuotas_max_min"]),
    )


def cargar_politica(cfg: dict[str, Any] | None = None, ruta: Path | str = RUTA_UMBRALES) -> Politica:
    if cfg is None:
        from golmodel.config import config
        cfg = config()
    v3 = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    return desde_dicts(v3, cfg)
