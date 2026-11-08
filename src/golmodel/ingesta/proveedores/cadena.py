"""Cadenas de proveedores con respaldo automático.

Regla central de precisión: un sustituto se usa automáticamente solo si
entrega el MISMO dato (`equivalente=True`). Si no es equivalente, el
resultado sale con Calidad.DEGRADADA y avisos; el motor de valor usa esa
calidad para endurecer umbrales o no apostar. Para xG, la cadena ni
siquiera se puede construir con proveedores de otra familia de xG: mezclar
modelos de xG distintos sesga los ratings sin que se note.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from golmodel.ingesta.proveedores.base import (
    ErrorPermanente,
    ErrorProveedor,
    Proveedor,
    SinPresupuesto,
)
from golmodel.ingesta.proveedores.presupuesto import Presupuesto
from golmodel.ingesta.tipos import Calidad, Capacidad, Solicitud

log = logging.getLogger(__name__)

# Un validador revisa los datos y devuelve avisos; si alguno empieza con
# "DEGRADA:", el resultado baja a Calidad.DEGRADADA.
Validador = Callable[[list[Any]], list[str]]


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Interruptor:
    """Circuit breaker por proveedor."""

    fallos_para_abrir: int = 3
    espera_transitorio: timedelta = timedelta(minutes=15)
    espera_permanente: timedelta = timedelta(hours=24)
    reloj: Callable[[], datetime] = _ahora
    _fallos: dict[str, int] = field(default_factory=dict)
    _abierto_hasta: dict[str, datetime] = field(default_factory=dict)
    _motivo: dict[str, str] = field(default_factory=dict)

    def disponible(self, proveedor: str) -> bool:
        hasta = self._abierto_hasta.get(proveedor)
        return hasta is None or self.reloj() >= hasta

    def exito(self, proveedor: str) -> None:
        self._fallos.pop(proveedor, None)
        self._abierto_hasta.pop(proveedor, None)

    def falla(self, proveedor: str, error: ErrorProveedor) -> None:
        self._motivo[proveedor] = str(error)
        if isinstance(error, ErrorPermanente):
            self._abierto_hasta[proveedor] = self.reloj() + self.espera_permanente
            return
        n = self._fallos.get(proveedor, 0) + 1
        self._fallos[proveedor] = n
        if n >= self.fallos_para_abrir:
            self._abierto_hasta[proveedor] = self.reloj() + self.espera_transitorio

    def estado(self) -> dict[str, dict]:
        """Proveedores en pausa: hasta cuándo y por qué (último error)."""
        return {p: {"hasta": h.isoformat(), "motivo": self._motivo.get(p, "")}
                for p, h in self._abierto_hasta.items() if self.reloj() < h}


@dataclass(frozen=True)
class Eslabon:
    proveedor: Proveedor
    equivalente: bool = True        # ¿entrega el mismo dato que el principal?
    nota: str = ""                  # se muestra en la UI cuando se usa


@dataclass
class Intento:
    proveedor: str
    resultado: str                  # 'ok','vacio','error','omitido_liga','omitido_interruptor','sin_presupuesto'
    detalle: str = ""


@dataclass
class ResultadoCadena:
    capacidad: Capacidad
    datos: list[Any]
    calidad: Calidad
    proveedor: str | None
    intentos: list[Intento]
    avisos: list[str]

    @property
    def requiere_manual(self) -> bool:
        return self.calidad is Calidad.SIN_DATOS


class Cadena:
    def __init__(
        self,
        capacidad: Capacidad,
        eslabones: Sequence[Eslabon],
        *,
        interruptor: Interruptor,
        presupuestos: dict[str, Presupuesto] | None = None,
        validador: Validador | None = None,
        familia_xg: str | None = None,
    ):
        if not eslabones:
            raise ValueError("Una cadena necesita al menos un proveedor")
        for e in eslabones:
            if capacidad not in e.proveedor.capacidades:
                raise ValueError(f"{e.proveedor.nombre} no ofrece {capacidad.value}")
        if capacidad in (Capacidad.XG, Capacidad.XG_JUGADORES):
            if familia_xg is None:
                raise ValueError("Una cadena de xG debe declarar la familia_xg del modelo")
            ajenos = [e.proveedor.nombre for e in eslabones if e.proveedor.familia_xg != familia_xg]
            if ajenos:
                raise ValueError(
                    f"Proveedores {ajenos} no son de la familia de xG '{familia_xg}'. "
                    "Mezclar familias de xG sesga los ratings; cambiar de familia exige "
                    "reingerir el histórico y recalibrar (ver docs/proveedores.md)."
                )
        self.capacidad = capacidad
        self.eslabones = list(eslabones)
        self.interruptor = interruptor
        self.presupuestos = presupuestos or {}
        self.validador = validador

    def costo_estimado(self, solicitud: Solicitud) -> dict[str, int]:
        """Costo del primer proveedor que se intentaría (lo que muestra la UI antes del botón)."""
        for e in self.eslabones:
            p = e.proveedor
            if p.cubre(solicitud) and self.interruptor.disponible(p.nombre):
                return {p.presupuesto: p.costo_estimado(solicitud)} if p.presupuesto else {}
        return {}

    def obtener(self, solicitud: Solicitud) -> ResultadoCadena:
        if solicitud.capacidad is not self.capacidad:
            raise ValueError(f"Solicitud de {solicitud.capacidad} en cadena de {self.capacidad}")
        intentos: list[Intento] = []
        avisos: list[str] = []
        for i, e in enumerate(self.eslabones):
            p = e.proveedor
            if not p.cubre(solicitud):
                intentos.append(Intento(p.nombre, "omitido_liga"))
                continue
            if not self.interruptor.disponible(p.nombre):
                intentos.append(Intento(p.nombre, "omitido_interruptor"))
                continue
            pres = self.presupuestos.get(p.presupuesto) if p.presupuesto else None
            costo = p.costo_estimado(solicitud)
            if pres is not None and not pres.puede_gastar(costo, para_cierre=solicitud.para_cierre):
                intentos.append(Intento(p.nombre, "sin_presupuesto", f"costo {costo}, quedan {pres.restante}"))
                continue
            try:
                datos = p.obtener(solicitud)
            except SinPresupuesto as err:
                intentos.append(Intento(p.nombre, "sin_presupuesto", str(err)))
                continue
            except ErrorProveedor as err:
                self.interruptor.falla(p.nombre, err)
                intentos.append(Intento(p.nombre, "error", str(err)))
                log.warning("Proveedor %s falló para %s: %s", p.nombre, self.capacidad.value, err)
                continue
            self.interruptor.exito(p.nombre)
            if not datos and self.capacidad not in p.vacio_valido:
                intentos.append(Intento(p.nombre, "vacio"))
                continue
            intentos.append(Intento(p.nombre, "ok", f"{len(datos)} registros"))

            calidad = Calidad.OPTIMA if i == 0 else (
                Calidad.SUSTITUTO_EQUIVALENTE if e.equivalente else Calidad.DEGRADADA
            )
            if i > 0:
                avisos.append(f"Se usó {p.nombre} como respaldo. {e.nota}".strip())
            if self.validador:
                for aviso in self.validador(datos):
                    if aviso.startswith("DEGRADA:"):
                        calidad = Calidad.DEGRADADA
                        aviso = aviso.removeprefix("DEGRADA:").strip()
                    avisos.append(aviso)
            return ResultadoCadena(self.capacidad, datos, calidad, p.nombre, intentos, avisos)

        avisos.append(f"Ningún proveedor entregó {self.capacidad.value} para {solicitud.liga}.")
        return ResultadoCadena(self.capacidad, [], Calidad.SIN_DATOS, None, intentos, avisos)


def validador_casa_referencia(casa_referencia: str) -> Validador:
    """Para cuotas: si falta la casa de referencia, p_mercado será un consenso y no la referencia."""

    def _validar(datos: list[Any]) -> list[str]:
        casas = {getattr(c, "casa", None) for c in datos}
        if casa_referencia not in casas:
            return [f"DEGRADA: sin cuotas de {casa_referencia}; p_mercado = mediana sin margen "
                    "de las casas disponibles y α queda como no calibrado."]
        return []

    return _validar
