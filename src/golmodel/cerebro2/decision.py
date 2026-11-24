"""Resolución MECE del estado de una decisión. Sin dependencias: se verifica de forma exhaustiva.

Cada gate produce una severidad; el estado es el del RANGO máximo (un orden total), de modo que
(a) todo conjunto de gates produce exactamente UN estado (mutuamente excluyentes) y
(b) todo conjunto produce alguno (exhaustivos). Sin gates → ABSTENCION: nunca APROBADO por defecto.

    rango 0  OK, NO_APLICABLE           -> APROBADO
    rango 1  AVISO                      -> CON_RESERVAS
    rango 2  PENDIENTE (o NO_EVALUABLE no requerido) -> EN_OBSERVACION
    rango 3  FALLA                      -> RECHAZADO
    rango 4  NO_EVALUABLE requerido     -> ABSTENCION

`modo_experimental` NO es un parámetro aquí: es UN gate más (AVISO si está activo), el único mecanismo del tope.
Una severidad desconocida lanza KeyError a propósito: quien llama (riesgo.evaluar) la convierte en ABSTENCION.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

ESTADOS = ("APROBADO", "CON_RESERVAS", "EN_OBSERVACION", "RECHAZADO", "ABSTENCION")
SEVERIDADES = ("OK", "NO_APLICABLE", "AVISO", "PENDIENTE", "FALLA", "NO_EVALUABLE")
_RANGO = {"OK": 0, "NO_APLICABLE": 0, "AVISO": 1, "PENDIENTE": 2, "FALLA": 3}
_ESTADO_DE_RANGO = {0: "APROBADO", 1: "CON_RESERVAS", 2: "EN_OBSERVACION", 3: "RECHAZADO", 4: "ABSTENCION"}


@dataclass(frozen=True)
class GateEval:
    gate: str
    ambito: str                         # 'ticket' | 'seleccion'
    severidad: str
    requerido: bool = False             # solo cambia el significado de NO_EVALUABLE
    valor: object = None                # valor medido (qué condición, con qué valor...)
    umbral: object = None               # ...contra qué umbral
    razon: str | None = None            # obligatoria si no es OK (trazabilidad)
    partido_id: int | None = None
    seleccion: str | None = None        # código(s) de la selección, unidos por '+'
    decisivo: bool = False              # determinó el estado final
    orden: int = 0                      # orden de evaluación


def rango(g: GateEval) -> int:
    if g.severidad == "NO_EVALUABLE":
        return 4 if g.requerido else 2
    return _RANGO[g.severidad]


def resolver_estado(gates: Sequence[GateEval]) -> str:
    if not gates:
        return "ABSTENCION"             # fail-closed: sin evidencia no se aprueba
    return _ESTADO_DE_RANGO[max(rango(g) for g in gates)]


def marcar_decisivos(gates: Sequence[GateEval]) -> tuple[GateEval, ...]:
    """Numera los gates en orden de evaluación y marca los que fijan el estado (rango máximo > 0)."""
    from dataclasses import replace
    if not gates:
        return ()
    tope = max(rango(g) for g in gates)
    return tuple(replace(g, orden=i, decisivo=(tope > 0 and rango(g) == tope)) for i, g in enumerate(gates, start=1))


@dataclass(frozen=True)
class Veredicto:
    """Salida de Cerebro 2. Todos los campos existen SIEMPRE: lo no disponible es None explícito, nunca ausente."""
    estado: str
    razon_principal: str | None
    categoria: str | None
    ticket_id: str | None
    gates: tuple[GateEval, ...]
    estado_por_pierna: dict = field(default_factory=dict)
    fuente_devig: str | None = None
    metodo_devig: str | None = None
    casas_usadas: tuple | None = None
    casas_esperadas: tuple | None = None
    mercado_por_pierna: dict = field(default_factory=dict)
    modo_experimental: bool | None = None
    techo_estado: str | None = None
    calibrado_con: str | None = None
    info_asof: str | None = None        # ISO del ticket de ENTRADA (nunca now())
    versiones: dict = field(default_factory=dict)
    config_hash: str | None = None
    semilla: int | None = None
    n_draws: int | None = None
    ticket_resumen: dict | None = None

    def a_json(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
