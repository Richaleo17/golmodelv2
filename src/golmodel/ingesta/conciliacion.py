"""Conciliación de resultados entre fuentes.

Un pick solo se liquida con un resultado confiable: de una fuente
equivalente, o de una no equivalente que otra fuente confirma. Si dos
fuentes dan marcadores distintos, el partido queda "en conflicto" y no se
liquida hasta resolverlo (a mano o con una tercera fuente).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from golmodel.ingesta.tipos import PartidoExterno


@dataclass(frozen=True)
class Conflicto:
    partido: PartidoExterno
    otro: PartidoExterno
    motivo: str


def _clave(p: PartidoExterno, normalizar) -> tuple[str, str]:
    return normalizar(p.local), normalizar(p.visitante)


def conciliar(principal: list[PartidoExterno], confirmacion: list[PartidoExterno],
              normalizar=lambda s: s.strip().lower(),
              tolerancia: timedelta = timedelta(hours=36)) -> tuple[list[PartidoExterno], list[Conflicto]]:
    """Devuelve (confirmados, conflictos). Empareja por equipos e inicio ± tolerancia."""
    por_clave: dict[tuple[str, str], list[PartidoExterno]] = {}
    for p in confirmacion:
        por_clave.setdefault(_clave(p, normalizar), []).append(p)
    confirmados, conflictos = [], []
    for p in principal:
        candidatos = [q for q in por_clave.get(_clave(p, normalizar), [])
                      if abs(q.inicio - p.inicio) <= tolerancia]
        if not candidatos:
            continue  # sin confirmación todavía: queda pendiente
        q = candidatos[0]
        if (p.goles_local, p.goles_visitante) == (q.goles_local, q.goles_visitante):
            confirmados.append(p)
        else:
            conflictos.append(Conflicto(p, q, f"{p.fuente} {p.goles_local}-{p.goles_visitante} vs "
                                              f"{q.fuente} {q.goles_local}-{q.goles_visitante}"))
    return confirmados, conflictos
