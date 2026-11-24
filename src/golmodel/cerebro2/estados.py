"""Severidades de gate, estados de decisión y su mapa (decisión 2: escalera MECE y determinista).

Precedencia (de mayor a menor): ABSTENCIÓN > RECHAZADO > EN_OBSERVACIÓN > CON_RESERVAS > APROBADO.
Un gate NO_EVALUABLE requerido → ABSTENCIÓN; NO_EVALUABLE no requerido → se trata como PENDIENTE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any


class Severidad(IntEnum):
    OK = 0
    AVISO = 1
    PENDIENTE = 2
    FALLA = 3
    NO_EVALUABLE = 4


class Estado(str, Enum):
    APROBADO = "APROBADO"
    CON_RESERVAS = "CON_RESERVAS"
    EN_OBSERVACION = "EN_OBSERVACION"
    RECHAZADO = "RECHAZADO"
    ABSTENCION = "ABSTENCION"

    @property
    def rango(self) -> int:
        return _RANGO[self]

    @property
    def stake_permitido(self) -> bool:
        """Solo APROBADO (Kelly pleno) y CON_RESERVAS (Kelly reducido) llevan stake."""
        return self in (Estado.APROBADO, Estado.CON_RESERVAS)


_RANGO = {Estado.APROBADO: 0, Estado.CON_RESERVAS: 1, Estado.EN_OBSERVACION: 2, Estado.RECHAZADO: 3,
          Estado.ABSTENCION: 4}

_MAPA = {Severidad.OK: Estado.APROBADO, Severidad.AVISO: Estado.CON_RESERVAS,
         Severidad.PENDIENTE: Estado.EN_OBSERVACION, Severidad.FALLA: Estado.RECHAZADO}


@dataclass(frozen=True)
class Gate:
    """Resultado de un chequeo verificable: qué se midió, contra qué umbral y con qué severidad."""
    nombre: str
    severidad: Severidad
    valor: float | str | None = None
    umbral: float | str | None = None
    requerido: bool = True
    detalle: dict[str, Any] = field(default_factory=dict)

    @property
    def estado(self) -> Estado:
        if self.severidad is Severidad.NO_EVALUABLE:
            return Estado.ABSTENCION if self.requerido else Estado.EN_OBSERVACION
        return _MAPA[self.severidad]

    def a_json(self) -> dict[str, Any]:
        return {"gate": self.nombre, "severidad": self.severidad.name, "estado": self.estado.value,
                "valor": self.valor, "umbral": self.umbral, "requerido": self.requerido, "detalle": self.detalle}


@dataclass(frozen=True)
class Decision:
    nivel: str                       # 'seleccion' | 'ticket'
    estado: Estado
    gate_determinante: str           # qué gate (o pierna) fijó el estado
    gates: tuple[Gate, ...]
    piernas: tuple["Decision", ...] = ()
    tope_experimental: bool = False  # True si el estado era APROBADO y modo_experimental lo topó

    @property
    def rechazo_sustantivo(self) -> bool:
        """RECHAZADO por evidencia presente; las ABSTENCIONES van aparte (falta de auditoría, decisión 7)."""
        return self.estado is Estado.RECHAZADO

    def a_json(self) -> dict[str, Any]:
        return {"nivel": self.nivel, "estado": self.estado.value, "gate_determinante": self.gate_determinante,
                "tope_experimental": self.tope_experimental, "gates": [g.a_json() for g in self.gates],
                "piernas": [p.a_json() for p in self.piernas]}
