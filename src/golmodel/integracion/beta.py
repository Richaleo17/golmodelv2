"""Modo «Operativo (Beta)»: ADAPTADOR de presentación y decisión. No toca `tickets.py`, `dependencia.py` ni `cerebro2/`.

Qué hace: con el interruptor `modo_operativo_beta.activo = true` (manual, apagado por defecto) la decisión de tickets se toma con `modo_experimental = False` —es decir,
sin el techo `CON_RESERVAS`, y el auditor puede emitir APROBADO—, PERO SOLO si hay una calibración `oos_real` vigente (la que el backtest OOS con las pruebas A y B
superadas deja guardada). Sin ella el modo no surte efecto (fail-closed) y se explica por qué. El `modo_experimental` de la config NO se modifica: sigue siendo la
protección por defecto y lo cambia el responsable.

Stake: el que ya calcula `tickets.py` = Kelly FRACCIONAL de `p_conservadora` con el tope `bankroll.stake_max_parlay_u`; aquí solo se garantiza que la fracción no supere
`modo_operativo_beta.kelly_fraccion_parlay` (0.10). Cada decisión Beta lleva `sello` y `banner` (se guardan en `auditoria_decisiones.payload`).

Honestidad: pasar las pruebas A y B es condición NECESARIA, no suficiente. La incertidumbre idiosincrática (τ) no está validada y `p_conservadora` ≈ 50 % de `p_media`;
el banner lo dice y no se puede quitar desde aquí.
"""
from __future__ import annotations

import copy

SIN_BETA = {"solicitado": False, "efectivo": False}


def _cfg_beta(cfg_v3: dict) -> dict | None:
    b = cfg_v3.get("modo_operativo_beta")
    if b is None:
        return None
    if not isinstance(b.get("activo"), bool):                       # fail-closed: nada de truthiness ("false", 1, None…)
        raise ValueError("modo_operativo_beta.activo debe ser un booleano explícito")
    return b


def resolver(cfg_v3: dict, version_cal) -> tuple[bool, dict, dict]:
    """(modo_experimental, cfg_para_tickets, info). `info`: {'solicitado', 'efectivo', ...}; con efecto trae sello, banner, fracción de Kelly y la versión usada.
    No muta `cfg_v3`."""
    modo_cfg = bool(cfg_v3["modo_experimental"])
    b = _cfg_beta(cfg_v3)
    if b is None or not b["activo"]:
        return modo_cfg, cfg_v3, dict(SIN_BETA)
    cal = getattr(version_cal, "calibrado_con", None)
    if cal != "oos_real":
        return modo_cfg, cfg_v3, {"solicitado": True, "efectivo": False, "sello": b["sello"],
                                  "motivo": f"modo Beta solicitado pero SIN EFECTO: no hay calibración oos_real vigente (calibrado_con={cal!r}); se mantiene el techo"}
    cfg = copy.deepcopy(cfg_v3)
    fraccion = min(float(cfg["bankroll"]["kelly_fraccion_parlay"]), float(b["kelly_fraccion_parlay"]))
    cfg["bankroll"]["kelly_fraccion_parlay"] = fraccion
    return False, cfg, {"solicitado": True, "efectivo": True, "sello": b["sello"], "banner": b["banner"], "kelly_fraccion_parlay": fraccion,
                        "stake_max_parlay_u": float(cfg["bankroll"]["stake_max_parlay_u"]), "calibrado_con": cal,
                        "modo_experimental_config": modo_cfg}


def efectivo(beta: dict | None) -> bool:
    return bool(beta and beta.get("efectivo"))
