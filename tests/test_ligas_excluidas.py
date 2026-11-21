"""Ligas excluidas por decisión del responsable (2026-11-20): `ligas_excluidas` → `ligas.activa = false` y nada sale de ellas."""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from golmodel import repositorio as repo
from golmodel.config import cargar
from golmodel.servicios import vistas

CFG = cargar()


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_exc_{uuid.uuid4().hex[:8]}"
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


def _partido(c, liga, inicio, estado="programado", goles=None):
    lid = c.execute("SELECT id FROM ligas WHERE codigo = %s", (liga,)).fetchone()["id"]
    a = c.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'X') RETURNING id", (f"A-{liga}-{uuid.uuid4().hex[:4]}",)).fetchone()["id"]
    b = c.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'X') RETURNING id", (f"B-{liga}-{uuid.uuid4().hex[:4]}",)).fetchone()["id"]
    gl, gv = goles or (None, None)
    return c.execute("""INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id, estado, goles_local, goles_visitante)
                        VALUES (%s, '2026', %s, %s, %s, %s, %s, %s) RETURNING id""", (lid, inicio, a, b, estado, gl, gv)).fetchone()["id"]


def test_config_excluye_las_ligas_pedidas():
    assert set(CFG["ligas_excluidas"]) >= {"EPL", "SA", "BUN", "ERE", "BEL", "MK:soccer_japan_j_league"}
    assert not set(CFG["ligas_excluidas"]) & set(CFG["ligas_activas"])                 # coherentes: no hay liga activa y excluida a la vez
    for sec in ("corners", "tarjetas", "props"):
        assert not set(CFG["ligas_excluidas"]) & set(CFG[sec]["ligas"])                # tampoco en mercados extra
    assert "japan" not in CFG["competiciones_mercado"]["patrones"]
    menores = set(CFG["ligas_excluidas_patrones"])
    assert {"efl_champ", "bundesliga2", "ligue_two", "serie_b", "segunda", "spl", "turkey", "argentina", "saudi", "japan"} <= menores
    assert not menores & set(CFG["competiciones_mercado"]["patrones"])                  # lo excluido tampoco se descubre
    assert {"uefa_champs", "uefa_europa", "nations_league", "libertadores", "copa"} <= set(CFG["competiciones_mercado"]["patrones"])


def test_sincronizar_oculta_en_historial_dia_y_proximos(bd):
    ahora = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    manana = ahora + timedelta(days=1)
    _partido(bd, "EPL", manana)
    _partido(bd, "LL", manana)
    _partido(bd, "EPL", ahora - timedelta(days=3), "finalizado", (2, 1))
    _partido(bd, "LL", ahora - timedelta(days=3), "finalizado", (1, 1))
    d = manana.date()

    assert {p["liga"] for p in vistas.dia(bd, CFG, d, ahora)} == {"EPL", "LL"}          # antes de sincronizar: todo igual que siempre
    cfg = copy.deepcopy(CFG)
    cfg["ligas_excluidas"] = ["EPL"]
    assert repo.sincronizar_ligas(bd, cfg) == ["EPL"]

    assert {p["liga"] for p in vistas.dia(bd, cfg, d, ahora)} == {"LL"}
    assert {p["liga"] for p in repo.partidos_de_dias(bd, [d])} == {"LL"}
    assert set(repo.historial(bd)["liga"]) == {"LL"}                                       # tampoco entra a la calibración del modelo

    cfg["ligas_excluidas"] = []                                                           # quitarla de la lista la reactiva
    assert repo.sincronizar_ligas(bd, cfg) == []
    assert {p["liga"] for p in vistas.dia(bd, cfg, d, ahora)} == {"EPL", "LL"}


def test_dashboard_guardado_ignora_ligas_excluidas(bd):
    from golmodel.servicios import aciertos
    ahora = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    ids = {}
    for liga in ("EPL", "LL"):
        pid = _partido(bd, liga, ahora - timedelta(days=1), "finalizado", (2, 0))
        ids[liga] = pid
    # lo guardado al cierre: una fila de cada liga (prediccion_id no se valida: la tabla no tiene FK hacia predicciones)
    for liga, pid in ids.items():
        bd.execute("""INSERT INTO dashboard_registro (partido_id, kpi, fuente, dia, prediccion_id, pick, p, acierto, grupo)
                      VALUES (%s, 'Ganador', 'decision', %s, 1, 'x', 0.6, true, 'g')""", (pid, (ahora - timedelta(days=1)).date()))
    d0, d1 = (ahora - timedelta(days=2)).date(), ahora.date()
    assert len(aciertos.detalle_guardado(bd, d0, d1)) == 2
    cfg = copy.deepcopy(CFG)
    cfg["ligas_excluidas"] = ["EPL"]
    repo.sincronizar_ligas(bd, cfg)
    assert len(aciertos.detalle_guardado(bd, d0, d1)) == 1


def test_patrones_desactivan_las_competiciones_menores_y_dejan_las_demas(bd):
    claves = {"soccer_germany_bundesliga2": False, "soccer_france_ligue_two": False, "soccer_efl_champ": False, "soccer_italy_serie_b": False,
              "soccer_brazil_serie_b": False, "soccer_spain_segunda_division": False, "soccer_spl": False, "soccer_turkey_super_league": False,
              "soccer_argentina_primera_division": False, "soccer_saudi_arabia_pro_league": False, "soccer_japan_j_league": False,
              "soccer_uefa_champs_league": True, "soccer_uefa_europa_league": True, "soccer_spain_copa_del_rey": True,
              "soccer_conmebol_copa_libertadores": True, "soccer_uefa_nations_league": True}
    for k in claves:
        repo.asegurar_liga_mercado(bd, k, k)
    repo.sincronizar_ligas(bd, CFG)
    activas = {r["codigo"][3:]: r["activa"] for r in bd.execute("SELECT codigo, activa FROM ligas WHERE codigo LIKE 'MK:%'")}
    assert activas == claves
    assert not bd.execute("SELECT activa FROM ligas WHERE codigo = 'BUN'").fetchone()["activa"]       # las 5 con modelo propio, también
    assert bd.execute("SELECT activa FROM ligas WHERE codigo = 'LL'").fetchone()["activa"]
