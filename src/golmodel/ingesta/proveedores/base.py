"""Contrato común de proveedores y jerarquía de errores."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from golmodel.ingesta.tipos import Capacidad, Solicitud


class ErrorProveedor(Exception):
    """Error base. `transitorio` decide si conviene reintentar más tarde."""

    transitorio: ClassVar[bool] = False

    def __init__(self, proveedor: str, mensaje: str):
        super().__init__(f"[{proveedor}] {mensaje}")
        self.proveedor = proveedor


class ErrorTransitorio(ErrorProveedor):
    """Timeout, error de red, 5xx o 429: se reintenta y luego se pasa al siguiente."""

    transitorio = True


class ErrorPermanente(ErrorProveedor):
    """401/403/404 o respuesta inválida: el proveedor queda fuera por más tiempo."""


class ErrorPlan(ErrorPermanente):
    """El plan gratis no cubre lo pedido (temporada, liga o endpoint)."""


class ErrorFormato(ErrorPermanente):
    """La respuesta cambió de formato: hay que revisar el parser."""


class SinPresupuesto(ErrorProveedor):
    """No quedan requests/créditos fuera de la reserva. No cuenta como falla."""


class Proveedor(ABC):
    nombre: ClassVar[str]
    capacidades: ClassVar[frozenset[Capacidad]]
    # Ligas internas que cubre; None = todas las que el cliente sepa mapear.
    ligas: ClassVar[frozenset[str] | None] = None
    # Familia del modelo de xG (solo proveedores con Capacidad.XG*).
    familia_xg: ClassVar[str | None] = None
    # Presupuesto que consume ('api_football', 'odds_api') o None si es gratis sin límite.
    presupuesto: ClassVar[str | None] = None
    # Capacidades donde una lista vacía es una respuesta válida (p. ej. "no hay bajas").
    vacio_valido: ClassVar[frozenset[Capacidad]] = frozenset()

    def cubre(self, solicitud: Solicitud) -> bool:
        return solicitud.capacidad in self.capacidades and (
            self.ligas is None or solicitud.liga in self.ligas
        )

    def costo_estimado(self, solicitud: Solicitud) -> int:
        """Costo en la unidad de su presupuesto (requests o créditos)."""
        return 1 if self.presupuesto else 0

    @abstractmethod
    def obtener(self, solicitud: Solicitud) -> list[Any]:
        """Devuelve objetos de golmodel.ingesta.tipos o lanza ErrorProveedor."""
