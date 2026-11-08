"""Migraciones de esquema para bases ya creadas.

schema.sql solo se ejecuta cuando Docker crea la base por primera vez. Los cambios posteriores van en
db/migraciones/NNN_nombre.sql (idempotentes) y se aplican al arrancar la API, una sola vez cada uno.
"""
from __future__ import annotations

import logging
from pathlib import Path

import psycopg

DIRECTORIO = Path(__file__).resolve().parents[2] / "db" / "migraciones"
log = logging.getLogger(__name__)


def aplicar(conn: psycopg.Connection, directorio: Path = DIRECTORIO) -> list[str]:
    """Aplica las migraciones pendientes en orden; devuelve los nombres aplicados."""
    conn.execute("CREATE TABLE IF NOT EXISTS migraciones (nombre text PRIMARY KEY, aplicada_en timestamptz NOT NULL DEFAULT now())")
    conn.commit()
    aplicadas: list[str] = []
    if not directorio.exists():
        return aplicadas
    for f in sorted(directorio.glob("*.sql")):
        conn.execute("SELECT pg_advisory_xact_lock(727101)")          # dos procesos no migran a la vez
        hecho = conn.execute("SELECT 1 FROM migraciones WHERE nombre = %s", (f.name,)).fetchone()
        if hecho:
            conn.commit()
            continue
        conn.execute(f.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO migraciones (nombre) VALUES (%s)", (f.name,))
        conn.commit()
        aplicadas.append(f.name)
        log.info("Migración aplicada: %s", f.name)
    return aplicadas
