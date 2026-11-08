"""Prepara una base PostgreSQL vacía (la de la nube): crea el esquema y el seed la primera vez.
Es idempotente: si el esquema ya existe, no toca nada (las migraciones las aplica la API al arrancar)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg


RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 1
    ultimo = None
    for _ in range(30):                       # la base puede tardar unos segundos en aceptar conexiones
        try:
            conn = psycopg.connect(dsn, autocommit=True)
            break
        except psycopg.OperationalError as e:
            ultimo = e
            time.sleep(2)
    else:
        print(f"No se pudo conectar a la base: {ultimo}", file=sys.stderr)
        return 1
    with conn:
        existe = conn.execute("SELECT to_regclass('golmodel.ligas') IS NOT NULL").fetchone()[0]
        if existe:
            print("Esquema ya creado: nada que hacer.")
            return 0
        conn.execute((RAIZ / "db/schema.sql").read_text())
        conn.execute("SET search_path TO golmodel, public")
        conn.execute((RAIZ / "db/seed.sql").read_text())
        print("Esquema y seed creados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
