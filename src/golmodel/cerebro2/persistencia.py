"""Persistencia de las decisiones en `auditoria_decisiones` / `evaluaciones_riesgo` (migración 004).

Las tablas son inmutables (triggers): esto solo INSERTA. La transacción es del llamador (como el resto de servicios).
Requiere que la 004 esté aplicada; si no lo está, el INSERT falla en voz alta (no hay fallback silencioso).
"""
from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
from psycopg.types.json import Jsonb

from golmodel.cerebro2 import auditoria

_COLS_DECISION = (
    "info_asof", "origen", "backtest_run_id", "componente", "estado", "categoria", "motivo", "modo_experimental",
    "techo_estado", "calibrado_con", "version_tickets", "version_calibracion_mercado", "version_incertidumbre",
    "version_dependencia", "version_calibracion_tickets", "version_cerebro2", "config_hash_restricciones", "ticket_id",
    "semilla", "n_draws", "parlay_id", "payload")
_COLS_GATE = ("orden", "ambito", "partido_id", "seleccion", "gate", "severidad", "valor", "umbral", "decisivo", "detalle")


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(f"no serializable a JSON: {type(o).__name__}")


def _jsonb(x):
    return None if x is None else Jsonb(x, dumps=lambda o: json.dumps(o, default=_json_default, allow_nan=False))


def _insertar_decision(conn, fila: dict) -> int:
    fila = dict(fila, payload=_jsonb(fila["payload"]))
    cols = ", ".join(_COLS_DECISION)
    marcas = ", ".join(f"%({c})s" for c in _COLS_DECISION)
    cur = conn.execute(f"INSERT INTO auditoria_decisiones ({cols}) VALUES ({marcas}) RETURNING id", fila)
    r = cur.fetchone()
    return r["id"] if isinstance(r, dict) else r[0]


def guardar_veredicto(conn, v, *, origen: str = "produccion", backtest_run_id: int | None = None, info_asof_respaldo=None) -> int:
    """Guarda el veredicto de Cerebro 2 y una fila por gate evaluado. Devuelve el id de `auditoria_decisiones`."""
    aid = _insertar_decision(conn, auditoria.fila_cerebro2(v, origen=origen, backtest_run_id=backtest_run_id,
                                                           info_asof_respaldo=info_asof_respaldo))
    cols = ", ".join(("auditoria_id",) + _COLS_GATE)
    marcas = ", ".join(["%(auditoria_id)s"] + [f"%({c})s" for c in _COLS_GATE])
    for g in auditoria.filas_gates(v):
        conn.execute(f"INSERT INTO evaluaciones_riesgo ({cols}) VALUES ({marcas})",
                     dict(g, auditoria_id=aid, valor=_jsonb(g["valor"]), umbral=_jsonb(g["umbral"])))
    return aid


def guardar_decision_tickets(conn, salida, *, origen: str = "produccion", backtest_run_id: int | None = None) -> int:
    """Guarda la propia decisión de tickets.py (PROPUESTO/ABSTENCION) con su RegistroAuditoria como payload."""
    return _insertar_decision(conn, auditoria.fila_tickets(salida, origen=origen, backtest_run_id=backtest_run_id))
