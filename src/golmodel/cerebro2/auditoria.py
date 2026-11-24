"""Registro inmutable de cada decisión del Cerebro 2 (decisiones 6 y 8).

Cada fila guarda: versión del Cerebro 2 y hash de la política, todos los inputs con sus versiones, la lista
ORDENADA de gates (valor, umbral, severidad), el estado final y el gate que lo determinó. Un rechazo sin
justificación sería indistinguible de un bug.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from golmodel.cerebro2.estados import Decision
from golmodel.cerebro2.politica import CEREBRO2_VERSION, Politica
from golmodel.cerebro2.riesgo import EntradaSeleccion, EntradaTicket


def _jsonable(x: Any) -> Any:
    if isinstance(x, datetime):
        return x.isoformat()
    if hasattr(x, "a_json"):
        return x.a_json()
    if is_dataclass(x):
        return {k: _jsonable(v) for k, v in asdict(x).items()}
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _inputs_seleccion(e: EntradaSeleccion) -> dict[str, Any]:
    return {
        "partido_id": e.partido_id, "liga": e.liga, "seleccion": e.seleccion,
        "p_modelo_calibrada": e.p_modelo_calibrada, "p_conservadora": e.p_conservadora,
        "calibrador_incertidumbre_id": e.calibrador_incertidumbre_id,
        "cuota_ejecutable": e.cuota_ejecutable, "ev_cons": e.ev_cons,
        "cuota_capturada_en": _jsonable(e.cuota_capturada_en), "info_hasta": _jsonable(e.info_hasta),
        "crs": {"alerta": e.crs_alerta, "vetadas": list(e.crs_selecciones_vetadas), "version": e.crs_version},
        "benchmark": _jsonable(e.benchmark), "calibracion": _jsonable(e.calibracion),
        "alineacion_confirmada": e.alineacion_confirmada,
        "distribuciones_modelos": _jsonable(dict(e.distribuciones_modelos)),
        "versiones": _jsonable(dict(e.versiones)),
    }


def registro(decision: Decision, entrada: EntradaSeleccion | EntradaTicket, politica: Politica,
             ahora: datetime, *, origen: str = "produccion") -> dict[str, Any]:
    if isinstance(entrada, EntradaTicket):
        inputs = {"p_conservadora": entrada.p_conservadora, "cuota_combinada": entrada.cuota_combinada,
                  "ev_cons": entrada.ev_cons, "lift": entrada.lift, "ic_ancho_rel": entrada.ic_ancho_rel,
                  "piernas": [_inputs_seleccion(p) for p in entrada.piernas]}
        partido_id = seleccion = None
        infos = [p.info_hasta for p in entrada.piernas if p.info_hasta is not None]
        info_hasta = max(infos) if infos else None
    else:
        inputs = _inputs_seleccion(entrada)
        partido_id, seleccion, info_hasta = entrada.partido_id, entrada.seleccion, entrada.info_hasta
    return {
        "origen": origen, "nivel": decision.nivel, "partido_id": partido_id, "seleccion": seleccion,
        "estado": decision.estado.value, "gate_determinante": decision.gate_determinante,
        "tope_experimental": decision.tope_experimental, "decision": decision.a_json(), "inputs": inputs,
        "info_hasta": info_hasta, "auditada_en": ahora, "cerebro2_version": CEREBRO2_VERSION,
        "politica_hash": politica.hash, "politica": politica.a_json(),
        "modo_experimental": politica.modo_experimental,
    }


def guardar(conn, reg: dict[str, Any]) -> int:
    """Inserta la decisión (la tabla es solo-inserción). Devuelve el id."""
    fila = conn.execute("""
        INSERT INTO auditoria_decisiones
            (origen, nivel, partido_id, seleccion, estado, gate_determinante, tope_experimental, decision, inputs,
             info_hasta, auditada_en, cerebro2_version, politica_hash, politica, modo_experimental)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id
    """, (reg["origen"], reg["nivel"], reg["partido_id"], reg["seleccion"], reg["estado"], reg["gate_determinante"],
          reg["tope_experimental"], json.dumps(_jsonable(reg["decision"])), json.dumps(_jsonable(reg["inputs"])),
          reg["info_hasta"], reg["auditada_en"], reg["cerebro2_version"], reg["politica_hash"],
          json.dumps(_jsonable(reg["politica"])), reg["modo_experimental"])).fetchone()
    return int(fila["id"] if isinstance(fila, dict) else fila[0])
