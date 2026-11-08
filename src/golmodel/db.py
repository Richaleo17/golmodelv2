"""Conexión a PostgreSQL y bloqueo de actualizaciones."""
from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

CLAVE_BLOQUEO = 727_100   # advisory lock: una sola actualización a la vez


class Ocupado(Exception):
    """Ya hay una actualización en curso."""


def url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://golmodel:golmodel_local@localhost:5432/golmodel")


def conectar(dsn: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    conn = psycopg.connect(dsn or url(), row_factory=dict_row, autocommit=autocommit)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO golmodel, public")
        cur.execute("SET TIME ZONE 'UTC'")
    if not autocommit:
        conn.commit()
    return conn


@contextlib.contextmanager
def bloqueo(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    """Conexión dedicada que mantiene el advisory lock durante toda la ejecución."""
    conn = conectar(dsn, autocommit=True)
    try:
        ok = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (CLAVE_BLOQUEO,)).fetchone()["ok"]
        if not ok:
            raise Ocupado("Ya hay una actualización en curso")
        try:
            yield conn
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (CLAVE_BLOQUEO,))
    finally:
        conn.close()
