"""Identidad del ticket y filas para `auditoria_decisiones` / `evaluaciones_riesgo` (migración 004).

Lógica pura: no toca la BD (la persistencia vive en `persistencia.py`).

Lo DESCONOCIDO se guarda con valores CONSERVADORES y EXPLÍCITOS, nunca con algo que aparente aprobación:
`modo_experimental` desconocido -> True, `techo_estado` -> CON_RESERVAS, `calibrado_con` -> no_calibrado, y las versiones
ausentes -> 'NO_DISPONIBLE' (las columnas son NOT NULL a propósito: no se permite una fila sin versiones, y el centinela
deja claro que faltaban en vez de inventarlas).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

NO_DISPONIBLE = "NO_DISPONIBLE"


def id_ticket(salida) -> str:
    """Id DETERMINISTA del ticket auditado (mismo contenido -> mismo id). Une la fila de tickets con la de cerebro2."""
    aud, t = salida.auditoria, salida.ticket
    nucleo = {
        "ahora": aud.ahora, "semilla": aud.semilla, "restricciones": aud.config_hash_restricciones,
        "calibracion_tickets": aud.version_calibracion_tickets,
        "ticket": None if t is None else {
            "tipo": t.tipo, "piernas": [[p.partido_id, list(p.selecciones), p.cuota] for p in t.piernas]},
    }
    return hashlib.sha256(json.dumps(nucleo, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _v(versiones: dict, clave: str) -> str:
    x = (versiones or {}).get(clave)
    return x if isinstance(x, str) and x else NO_DISPONIBLE


def fila_cerebro2(v, *, origen: str = "produccion", backtest_run_id: int | None = None,
                  info_asof_respaldo: datetime | None = None) -> dict:
    """Fila de `auditoria_decisiones` para un Veredicto. `info_asof` es el del ticket de ENTRADA; si no se conoce, hay que
    dar `info_asof_respaldo` explícito (la columna es NOT NULL y no se inventa con now())."""
    if v.info_asof is not None:
        info = datetime.fromisoformat(v.info_asof)
    elif info_asof_respaldo is not None:
        info = info_asof_respaldo
    else:
        raise ValueError("sin marca temporal del ticket de entrada: pasa info_asof_respaldo explícito (no se usa now())")
    ver = v.versiones or {}
    cm = ver.get("calibracion_mercado")
    return {
        "info_asof": info, "origen": origen, "backtest_run_id": backtest_run_id, "componente": "cerebro2",
        "estado": v.estado, "categoria": v.categoria, "motivo": v.razon_principal,
        "modo_experimental": True if v.modo_experimental is None else v.modo_experimental,
        "techo_estado": v.techo_estado if v.techo_estado in ("CON_RESERVAS", "SIN_TOPE") else "CON_RESERVAS",
        "calibrado_con": v.calibrado_con if v.calibrado_con in ("no_calibrado", "sinteticos", "oos_real") else "no_calibrado",
        "version_tickets": _v(ver, "tickets"), "version_calibracion_mercado": list(cm) if isinstance(cm, (list, tuple)) else [],
        "version_incertidumbre": _v(ver, "incertidumbre"), "version_dependencia": _v(ver, "dependencia"),
        "version_calibracion_tickets": _v(ver, "calibracion_tickets"), "version_cerebro2": _v(ver, "cerebro2"),
        "config_hash_restricciones": v.config_hash or NO_DISPONIBLE, "ticket_id": v.ticket_id,
        "semilla": v.semilla, "n_draws": v.n_draws, "parlay_id": None,
        "payload": v.a_json(),
    }


def filas_gates(v) -> list[dict]:
    """Una fila de `evaluaciones_riesgo` por gate: condición, valor, umbral, severidad y si fue decisivo."""
    out = []
    for g in v.gates:
        un_codigo = g.seleccion if (g.seleccion and "+" not in g.seleccion) else None   # SGP: FK a un solo código
        out.append({
            "orden": g.orden, "ambito": g.ambito, "partido_id": g.partido_id, "seleccion": un_codigo, "gate": g.gate,
            "severidad": g.severidad, "valor": g.valor, "umbral": g.umbral, "decisivo": g.decisivo,
            "detalle": g.razon if not g.seleccion or "+" not in g.seleccion else f"[{g.seleccion}] {g.razon or ''}".strip(),
        })
    return out


_CAT_TICKETS = {"tickets_sin_valor": "tickets_sin_valor", "incertidumbre_dependencia": "incertidumbre_gate_fallo"}


def fila_tickets(salida, *, origen: str = "produccion", backtest_run_id: int | None = None,
                 payload_extra: dict | None = None) -> dict:
    """Fila de `auditoria_decisiones` para la propia decisión de tickets.py (payload = RegistroAuditoria + ticket)."""
    from dataclasses import asdict
    aud, t = salida.auditoria, salida.ticket
    return {
        "info_asof": datetime.fromisoformat(aud.ahora), "origen": origen, "backtest_run_id": backtest_run_id,
        "componente": "tickets", "estado": salida.estado, "categoria": _CAT_TICKETS.get(aud.categoria_abstencion),
        "motivo": aud.motivo_abstencion, "modo_experimental": aud.modo_experimental, "techo_estado": aud.techo_estado,
        "calibrado_con": aud.calibrado_con, "version_tickets": aud.version_tickets,
        "version_calibracion_mercado": list(aud.version_calibracion_mercado),
        "version_incertidumbre": aud.version_incertidumbre, "version_dependencia": aud.version_dependencia,
        "version_calibracion_tickets": aud.version_calibracion_tickets, "version_cerebro2": None,
        "config_hash_restricciones": aud.config_hash_restricciones, "ticket_id": id_ticket(salida),
        "semilla": aud.semilla, "n_draws": aud.n_draws, "parlay_id": None,
        # payload_extra (p. ej. {"dia": ...}) se AÑADE sin pisar las claves propias; por defecto no cambia nada
        "payload": {**(payload_extra or {}), "auditoria": asdict(aud), "ticket": None if t is None else asdict(t)},
    }
