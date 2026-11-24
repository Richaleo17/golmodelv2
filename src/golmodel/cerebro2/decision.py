"""Función de decisión del Cerebro 2: estado = el MÁS SEVERO de los gates (y de las piernas, en tickets).

* Determinista: sin aleatoriedad; mismos inputs + misma política → mismo estado.
* Fail-closed: sin gates o con cualquier excepción de cómputo → ABSTENCIÓN (la rama `else` nunca aprueba).
* `modo_experimental=True` topa APROBADO a CON_RESERVAS (decisión 9).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from golmodel.cerebro2.estados import Decision, Estado, Gate, Severidad
from golmodel.cerebro2.politica import Politica
from golmodel.cerebro2.riesgo import EntradaSeleccion, EntradaTicket, gates_seleccion, gates_ticket


def decidir(nivel: str, gates: Sequence[Gate], *, modo_experimental: bool,
            piernas: Sequence[Decision] = ()) -> Decision:
    candidatos: list[tuple[Estado, str]] = [(g.estado, g.nombre) for g in gates]
    # Una pierna topada solo por modo_experimental cuenta como APROBADO aquí; el tope se aplica una vez al final.
    candidatos += [(Estado.APROBADO if p.tope_experimental else p.estado, f"pierna[{i}]:{p.gate_determinante}")
                   for i, p in enumerate(piernas)]
    if not candidatos:
        return Decision(nivel, Estado.ABSTENCION, "sin_gates", tuple(gates), tuple(piernas))
    # max estable: ante empate gana el primero en el orden de evaluación
    estado, determinante = candidatos[0]
    for e, n in candidatos[1:]:
        if e.rango > estado.rango:
            estado, determinante = e, n
    tope = False
    if estado is Estado.APROBADO and modo_experimental:
        estado, determinante, tope = Estado.CON_RESERVAS, "modo_experimental", True
    return Decision(nivel, estado, determinante, tuple(gates), tuple(piernas), tope)


def _error(nivel: str, exc: Exception) -> Decision:
    g = Gate("error_computo", Severidad.NO_EVALUABLE, valor=type(exc).__name__, detalle={"error": str(exc)[:500]})
    return Decision(nivel, Estado.ABSTENCION, g.nombre, (g,))


def auditar_seleccion(e: EntradaSeleccion, ahora: datetime, politica: Politica) -> Decision:
    try:
        return decidir("seleccion", gates_seleccion(e, ahora, politica), modo_experimental=politica.modo_experimental)
    except Exception as exc:  # noqa: BLE001 — fail-closed: cualquier error es ABSTENCIÓN, nunca APROBADO
        return _error("seleccion", exc)


def auditar_ticket(t: EntradaTicket, ahora: datetime, politica: Politica) -> Decision:
    try:
        piernas = [auditar_seleccion(p, ahora, politica) for p in t.piernas]
        return decidir("ticket", gates_ticket(t, politica), modo_experimental=politica.modo_experimental,
                       piernas=piernas)
    except Exception as exc:  # noqa: BLE001
        return _error("ticket", exc)
