"""Almacén de `VersionCalibradorTickets` (tabla `versiones_calibrador_tickets`, migración 005). Adaptador sin lógica de modelo.

* `guardar_version`: una fila inmutable por corrida del backtest OOS, SIEMPRE (también cuando los gates fallan: queda el registro).
* `cargar_ultima_valida`: lo único que la app usa — la versión más reciente con `gates_ok` Y `calibrado_con = 'oos_real'` creada
  `<= hasta` (as-of). Una versión `sinteticos`/`no_calibrado`, o una que falló sus gates, NUNCA se carga: ni siquiera por error.
* Fail-closed: sin la migración 005 (o sin versión válida) devuelve `None` y el llamador usa la versión provisional (`no_calibrado`).
"""
from __future__ import annotations

from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from golmodel.backtest.calibracion_tickets import VersionCalibradorTickets


def a_filas(v: VersionCalibradorTickets) -> dict:
    """VersionCalibradorTickets -> columnas de la tabla (c_seg_json / sigma_json)."""
    return {"datos_hasta": v.datos_hasta, "config_hash": v.config_hash, "n_draws": int(v.n_draws), "semilla": int(v.semilla),
            "metodo": v.metodo, "calibrado_con": v.calibrado_con,
            "c_seg": {"c_global": float(v.c_global), "segmentos_calibrados": [list(s) for s in v.segmentos_calibrados],
                      "segmentos_fallback": [list(s) for s in v.segmentos_fallback]},
            "sigma": {"sigma_g": float(v.sigma_g), "sigma_liga": v.sigma_liga}}


def de_fila(f: dict) -> VersionCalibradorTickets:
    c, s = f["c_seg_json"], f["sigma_json"]
    return VersionCalibradorTickets(
        datos_hasta=f["datos_hasta"], config_hash=f["config_hash"], n_draws=int(f["n_draws"]), semilla=int(f["semilla"]),
        metodo=f["metodo"], segmentos_calibrados=[list(x) for x in c.get("segmentos_calibrados", [])],
        segmentos_fallback=[list(x) for x in c.get("segmentos_fallback", [])], c_global=float(c["c_global"]),
        sigma_liga=s["sigma_liga"], sigma_g=float(s["sigma_g"]), calibrado_con=f["calibrado_con"])


def guardar_version(conn, v: VersionCalibradorTickets, *, gates_ok: bool, resultado: dict, creado_en: datetime | None = None) -> int:
    """Inserta la versión. La BD rechaza `oos_real` sin `gates_ok` (CHECK); aquí se rechaza ANTES, con un mensaje claro.
    `creado_en` (opcional, por defecto el now() de la BD) fija desde cuándo está DISPONIBLE la versión (as-of de `cargar_ultima_valida`)."""
    if v.calibrado_con == "oos_real" and not gates_ok:
        raise ValueError("una versión no puede declararse 'oos_real' si no superó los gates de cobertura en todos los folds")
    f = a_filas(v)
    return conn.execute("""
        INSERT INTO versiones_calibrador_tickets (creado_en, datos_hasta, config_hash, n_draws, semilla, metodo, c_seg_json, sigma_json,
                                                  calibrado_con, gates_ok, resultado_json)
        VALUES (coalesce(%s::timestamptz, now()), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (creado_en, f["datos_hasta"], f["config_hash"], f["n_draws"], f["semilla"], f["metodo"], Jsonb(f["c_seg"]),
                         Jsonb(f["sigma"]), f["calibrado_con"], bool(gates_ok), Jsonb(resultado, dumps=_dumps))).fetchone()["id"]


def _dumps(o):
    import json
    return json.dumps(o, default=lambda x: x.isoformat() if hasattr(x, "isoformat") else float(x) if hasattr(x, "__float__") else str(x),
                      allow_nan=False)


def cargar_ultima_valida(conn, hasta: datetime | None = None) -> tuple[int, VersionCalibradorTickets] | None:
    """La versión más reciente VÁLIDA (gates_ok y oos_real) creada `<= hasta`; None si no hay o si la tabla no existe (sin 005).
    Corre en un SAVEPOINT: un fallo aquí no aborta la transacción del llamador."""
    try:
        with conn.transaction():
            f = conn.execute("""SELECT * FROM versiones_calibrador_tickets WHERE gates_ok AND calibrado_con = 'oos_real'
                                AND (%s::timestamptz IS NULL OR creado_en <= %s) ORDER BY creado_en DESC, id DESC LIMIT 1""",
                             (hasta, hasta)).fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    return None if f is None else (f["id"], de_fila(f))
