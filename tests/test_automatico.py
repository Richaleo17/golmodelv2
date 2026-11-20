"""Actualizaciones automáticas: qué toca correr según la hora, lo último que corrió y los partidos próximos."""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from golmodel.config import cargar
from golmodel.jobs import automatico

CFG = cargar()
MX = ZoneInfo("America/Mexico_City")


def _t(h, m=0):
    return datetime(2026, 11, 18, h, m, tzinfo=MX).astimezone(timezone.utc)


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_auto_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    raiz = Path(__file__).resolve().parents[1]
    c = psycopg.connect(admin_dsn.replace("/postgres?", f"/{nombre}?"), row_factory=dict_row)
    c.execute((raiz / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    c.execute((raiz / "db/seed.sql").read_text())
    from golmodel import migraciones
    migraciones.aplicar(c)
    c.commit()
    yield c
    c.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def _ejec(c, tipo, inicio, estado="ok", por="job"):
    c.execute("INSERT INTO ejecuciones (tipo, iniciada_en, estado, disparada_por) VALUES (%s, %s, %s, %s)", (tipo, inicio, estado, por))


def test_secuencia_del_dia(bd):
    assert automatico.decidir(bd, CFG, _t(2)) is None                      # de madrugada no corre nada
    assert automatico.decidir(bd, CFG, _t(7, 5)) == "solo_cuotas"          # antes de la hora de la completa: cuotas (nunca corrió)
    assert automatico.decidir(bd, CFG, _t(7, 15)) == "actualizar_todo"     # la completa del día
    _ejec(bd, "actualizar_todo", _t(7, 15))
    assert automatico.decidir(bd, CFG, _t(8)) is None                       # cuotas recién traídas
    assert automatico.decidir(bd, CFG, _t(11, 20)) is None                  # sin partidos cerca: cada 6 h
    assert automatico.decidir(bd, CFG, _t(13, 20)) == "solo_cuotas"        # 6 h después
    _ejec(bd, "solo_cuotas", _t(13, 20), estado="en_curso")
    assert automatico.decidir(bd, CFG, _t(13, 25)) is None                  # nunca encima de otra actualización


def test_mas_seguido_si_hay_partido_cerca(bd):
    _ejec(bd, "actualizar_todo", _t(7, 15))
    _ejec(bd, "solo_cuotas", _t(11))
    assert automatico.decidir(bd, CFG, _t(13, 10)) is None                  # 2 h 10 min: sin partido cerca no toca
    liga = bd.execute("SELECT id FROM ligas LIMIT 1").fetchone()["id"]
    a = bd.execute("INSERT INTO equipos (nombre, pais) VALUES ('A', 'X') RETURNING id").fetchone()["id"]
    b = bd.execute("INSERT INTO equipos (nombre, pais) VALUES ('B', 'X') RETURNING id").fetchone()["id"]
    bd.execute("INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id) VALUES (%s, '2026', %s, %s, %s)",
               (liga, _t(15), a, b))
    assert automatico.decidir(bd, CFG, _t(13, 10)) == "solo_cuotas"        # partido a < 3 h: cada 2 h


def test_apagado_y_arranque_solo_en_render(bd, monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["auto"]["activo"] = False
    assert automatico.decidir(bd, cfg, _t(9)) is None
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("GOLMODEL_AUTO", raising=False)

    class S:
        pass
    s = S()
    s.cfg = CFG
    assert automatico.arrancar(s) is None                                   # fuera de Render (pruebas, local): no arranca


def test_alineaciones_solo_con_id_de_api_football_y_en_la_ventana(bd):
    _ejec(bd, "actualizar_todo", _t(7, 15))
    _ejec(bd, "solo_cuotas", _t(13, 0))
    liga = bd.execute("SELECT id FROM ligas WHERE codigo = 'LL'").fetchone()["id"]
    a = bd.execute("INSERT INTO equipos (nombre, pais) VALUES ('A1', 'X') RETURNING id").fetchone()["id"]
    b = bd.execute("INSERT INTO equipos (nombre, pais) VALUES ('B1', 'X') RETURNING id").fetchone()["id"]
    pid = bd.execute("INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id) VALUES (%s, '2026', %s, %s, %s) RETURNING id",
                     (liga, _t(15), a, b)).fetchone()["id"]
    assert automatico.decidir(bd, CFG, _t(13, 10)) is None                           # sin id de API-Football: nada que pedir
    bd.execute("UPDATE partidos SET api_football_id = 123 WHERE id = %s", (pid,))
    assert automatico.decidir(bd, CFG, _t(13, 10)) is None                           # faltan 110 min: aún fuera de la ventana de 90
    assert automatico.decidir(bd, CFG, _t(13, 45)) == "solo_alineaciones"            # a 75 min: toca
    _ejec(bd, "solo_alineaciones", _t(13, 45))
    assert automatico.decidir(bd, CFG, _t(13, 50)) is None                           # recién intentado: espera 15 min
    assert automatico.decidir(bd, CFG, _t(14, 5)) == "solo_alineaciones"             # a los 20 min vuelve a intentar
    bd.execute("INSERT INTO alineaciones (partido_id, equipo_id, jugador_nombre, titular, confirmada, fuente) VALUES (%s, %s, 'J', true, true, 'api_football')",
               (pid, a))
    assert automatico.decidir(bd, CFG, _t(14, 5)) is None                            # ya hay alineación confirmada: no insiste
    cfg = copy.deepcopy(CFG)
    cfg["auto"]["alineaciones"] = False
    bd.execute("DELETE FROM alineaciones")
    assert automatico.decidir(bd, cfg, _t(14, 5)) is None                            # se puede apagar
