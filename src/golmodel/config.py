"""Carga y valida config/config.yaml. Cada versión de modelo guarda su hash."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RUTA_DEFECTO = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
RUTA_V3 = Path(__file__).resolve().parents[2] / "config" / "v3_umbrales.yaml"
SECCIONES_V3 = ("modo_experimental", "cerebro2", "umbrales_provisionales", "tickets", "dependencia", "calibracion_tickets",
                "calibracion_mercado")

SECCIONES = ("zona_horaria", "ligas_activas", "fuentes", "cadenas", "frescura", "cierre", "modelo",
             "contexto", "mercado", "crs", "sugerencias", "valor", "parlays", "bankroll", "operacion")


def cargar(ruta: Path | str = RUTA_DEFECTO) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    faltan = [s for s in SECCIONES if s not in cfg]
    if faltan:
        raise ValueError(f"config.yaml incompleto, faltan secciones: {faltan}")
    v, p = cfg["valor"], cfg["parlays"]
    if not 0 < v["umbral_edge"] <= v["umbral_edge_sgp"] <= v["umbral_edge_sgp_sin_mercado"]:
        raise ValueError("Se requiere 0 < umbral_edge <= umbral_edge_sgp <= umbral_edge_sgp_sin_mercado")
    if not 3 <= p["bloques_min"] <= p["bloques_max"] <= 5:
        raise ValueError("bloques de parlay deben estar entre 3 y 5")
    if not 1 < p["cuota_min_bloque"] < p["cuota_max_bloque"]:
        raise ValueError("rango de cuotas por bloque inválido")
    c = cfg["crs"]
    if set(c["pesos"]) != {"divergencia_xpts", "brecha_probabilidad", "fatiga", "motivacion",
                           "fragilidad_defensiva"} or sum(c["pesos"].values()) != 100:
        raise ValueError("crs.pesos: deben ser los 5 parámetros y sumar 100")
    if c["ajuste_favorito"]["modo"] not in ("sombra", "activo"):
        raise ValueError("crs.ajuste_favorito.modo debe ser 'sombra' o 'activo'")
    return cfg


def cargar_v3(ruta: Path | str = RUTA_V3) -> dict[str, Any]:
    """Carga config/v3_umbrales.yaml (política PROVISIONAL de V3). Falla si falta una sección o la bandera no es bool."""
    cfg = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    faltan = [k for k in SECCIONES_V3 if k not in cfg]
    if faltan:
        raise ValueError(f"v3_umbrales.yaml incompleto, faltan secciones: {faltan}")
    if not isinstance(cfg["modo_experimental"], bool):
        raise ValueError("modo_experimental debe ser un booleano explícito")
    return cfg


def hash_config(cfg: dict[str, Any]) -> str:
    canon = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def config() -> dict[str, Any]:
    return cargar()
