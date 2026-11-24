"""Tipos normalizados que devuelve cualquier proveedor.

Cada proveedor traduce su formato a estas clases. Más arriba, nadie sabe
de qué API salió un dato, salvo por el campo `fuente`, que siempre se guarda.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum


class Capacidad(str, Enum):
    FIXTURES = "fixtures"
    RESULTADOS = "resultados"
    XG = "xg"
    XG_JUGADORES = "xg_jugadores"
    LESIONES = "lesiones"
    ALINEACIONES = "alineaciones"
    CUOTAS = "cuotas"
    CUOTAS_CIERRE = "cuotas_cierre"
    CALENDARIO = "calendario"          # todas las competiciones (fatiga del CRS)


class Calidad(str, Enum):
    """Calidad del dato obtenido por una cadena de proveedores."""

    OPTIMA = "optima"                      # proveedor principal
    SUSTITUTO_EQUIVALENTE = "equivalente"  # otro proveedor, mismo dato (p. ej. resultado final)
    DEGRADADA = "degradada"                # sustituto NO equivalente: se avisa y se endurecen umbrales
    SIN_DATOS = "sin_datos"                # nadie respondió: requiere captura manual o se omite


@dataclass(frozen=True)
class Solicitud:
    capacidad: Capacidad
    liga: str                                  # código interno: 'EPL','SA','MX1','MXF','MLS','BR1'…
    temporada: str | None = None
    desde: date | None = None
    hasta: date | None = None
    # Identificadores del partido en cada fuente: {'api_football': '123', 'odds_api': 'abc'}
    ids_partido: dict[str, str] = field(default_factory=dict)
    para_cierre: bool = False                  # permite usar la reserva de presupuesto de cierres


@dataclass(frozen=True)
class PartidoExterno:
    fuente: str
    liga: str
    inicio: datetime                           # timezone-aware, UTC
    local: str                                 # nombre crudo de la fuente
    visitante: str
    estado: str                                # 'programado','en_juego','finalizado','pospuesto','cancelado','abandonado'
    temporada: str | None = None
    goles_local: int | None = None
    goles_visitante: int | None = None
    id_externo: str | None = None


@dataclass(frozen=True)
class XgPartido:
    fuente: str
    familia_xg: str                            # modelo de xG; solo se mezcla con la misma familia
    liga: str
    inicio: datetime
    local: str
    visitante: str
    xg_local: float
    xg_visitante: float
    id_externo: str | None = None


@dataclass(frozen=True)
class XgJugador:
    fuente: str
    familia_xg: str
    liga: str
    temporada: str
    jugador: str
    equipo: str
    minutos: int
    xg: float
    xa: float
    id_externo: str | None = None
    partidos: int | None = None                # partidos jugados (props: titularidad)
    tiros: int | None = None                   # tiros totales (props: tiros al arco)


@dataclass(frozen=True)
class CuotaExterna:
    fuente: str
    casa: str                                  # nombre normalizado: 'pinnacle','bet365','promedio',...
    seleccion: str                             # código del catálogo: '1X2:1','OU:2.5:over',...
    cuota: float
    tipo: str                                  # 'prepartido','actual','cierre'
    capturada_en: datetime
    local: str
    visitante: str
    inicio: datetime | None = None
    id_partido_externo: str | None = None


@dataclass(frozen=True)
class BajaExterna:
    fuente: str
    equipo: str
    jugador: str
    tipo: str                                  # 'lesion','suspension','duda','otro'
    motivo: str | None
    capturado_en: datetime
    id_jugador_externo: str | None = None


@dataclass(frozen=True)
class AlineacionExterna:
    fuente: str
    equipo: str
    jugador: str
    titular: bool
    confirmada: bool
    capturada_en: datetime
    id_jugador_externo: str | None = None


@dataclass(frozen=True)
class CalendarioExterno:
    fuente: str
    equipo: str
    inicio: datetime
    competicion: str
    id_competicion_externa: str | None
    es_local: bool


# ---------------------------------------------------------------- ligas femeniles
# Cada fuente escribe distinto a los equipos femeniles («Monterrey W», «CF Monterrey Women», «Monterrey»…) y algunas
# usan EXACTAMENTE el mismo nombre que el club varonil. Como los alias (fuente, nombre) son globales, «Monterrey» de
# la Femenil terminaría unido al Monterrey de la Liga MX y mezclaría los datos de dos equipos distintos. Por eso,
# en estas ligas todo nombre sale de la cadena con un sufijo único: «Monterrey Femenil».
LIGAS_FEMENILES = frozenset({"MXF"})
SUFIJO_FEMENIL = "Femenil"
_SUFIJOS_FEMENILES = re.compile(r"(?:(?:\s+|\s*[-–(]\s*)(?:w|women|womens|woman|fem|femenil|femenino|feminil|ladies)\.?\)?)+\s*$",
                                re.IGNORECASE)


def nombre_femenil(nombre: str) -> str:
    """«Club America W» → «Club America Femenil». Idempotente."""
    base = _SUFIJOS_FEMENILES.sub("", nombre.strip()).strip() or nombre.strip()
    return f"{base} {SUFIJO_FEMENIL}"


def adaptar_nombres(liga: str, datos: list) -> list:
    """Aplica nombre_femenil a los campos de equipo de lo que entrega un proveedor (solo en ligas femeniles)."""
    if liga not in LIGAS_FEMENILES:
        return datos
    salida = []
    for d in datos:
        campos = {c: nombre_femenil(getattr(d, c)) for c in ("local", "visitante", "equipo")
                  if isinstance(getattr(d, c, None), str) and getattr(d, c)}
        salida.append(replace(d, **campos) if campos else d)
    return salida
