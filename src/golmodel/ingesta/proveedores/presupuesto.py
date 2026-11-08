"""Presupuesto de requests/créditos por proveedor (planes gratis)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Presupuesto:
    nombre: str
    limite: int                 # por periodo
    periodo: str                # 'dia' (API-Football) | 'mes' (The Odds API)
    reserva: int = 0            # unidades que solo puede usar el snapshot de cierre
    usado: int = 0              # se carga desde llamadas_api al arrancar
    restante_reportado: int | None = None   # lo que dice el proveedor en sus headers

    @property
    def restante(self) -> int:
        propio = self.limite - self.usado
        # Si el proveedor reporta su propio contador, gana el más pesimista.
        if self.restante_reportado is not None:
            return min(propio, self.restante_reportado)
        return propio

    def puede_gastar(self, costo: int, *, para_cierre: bool = False) -> bool:
        disponible = self.restante if para_cierre else self.restante - self.reserva
        return costo <= disponible

    def registrar(self, costo: int, restante_reportado: int | None = None) -> None:
        self.usado += costo
        if restante_reportado is not None:
            self.restante_reportado = restante_reportado
