"""El diagnóstico es de solo lectura y falla cerrado."""
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from golmodel import diag

RAIZ = Path(__file__).resolve().parents[1]
ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")


def test_sin_token_esta_desactivado(monkeypatch):
    monkeypatch.delenv("DIAG_TOKEN", raising=False)
    c = TestClient(diag.app)
    assert c.get("/diag/resumen").status_code == 503
    monkeypatch.setenv("DIAG_TOKEN", "corto")                      # token débil también se rechaza
    assert c.get("/diag/resumen", headers={"Authorization": "Bearer corto"}).status_code == 503


def test_token_incorrecto_y_consultas_contra_postgres(monkeypatch):
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_dg_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn = ADMIN.replace("/postgres?", f"/{nombre}?")
    try:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute((RAIZ / "db/schema.sql").read_text())
            c.execute("SET search_path TO golmodel, public")
            c.execute((RAIZ / "db/seed.sql").read_text())
        monkeypatch.setenv("DATABASE_URL", dsn)
        monkeypatch.setenv("DIAG_TOKEN", "x" * 24)
        cli = TestClient(diag.app)
        bueno = {"Authorization": "Bearer " + "x" * 24}
        assert cli.get("/diag/resumen", headers={"Authorization": "Bearer otro"}).status_code == 401
        assert cli.get("/salud").json() == {"ok": True}
        for ruta in ("/diag/resumen", "/diag/calibracion", "/diag/partido?q=Hoffenheim"):
            r = cli.get(ruta, headers=bueno)
            assert r.status_code == 200, (ruta, r.text)
    finally:
        admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
        admin.close()
