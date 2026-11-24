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


def _partido_en(bd, liga_codigo, inicio, api_id=None):
    liga = bd.execute("SELECT id FROM ligas WHERE codigo = %s", (liga_codigo,)).fetchone()["id"]
    a = bd.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'X') RETURNING id", (f"A-{uuid.uuid4().hex[:5]}",)).fetchone()["id"]
    b = bd.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'X') RETURNING id", (f"B-{uuid.uuid4().hex[:5]}",)).fetchone()["id"]
    return bd.execute("INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id, api_football_id) VALUES (%s, '2026', %s, %s, %s, %s) "
                      "RETURNING id", (liga, inicio, a, b, api_id)).fetchone()["id"], a


def test_alineaciones_se_piden_en_la_ventana_y_solo_si_falta(bd):
    _ejec(bd, "actualizar_todo", _t(7, 15))
    _ejec(bd, "solo_cuotas", _t(13, 0))
    pid, local = _partido_en(bd, "LL", _t(15))                                        # LaLiga: ESPN publica su alineación
    assert automatico.decidir(bd, CFG, _t(13, 10)) is None                           # faltan 110 min: aún fuera de la ventana de 100
    assert automatico.decidir(bd, CFG, _t(13, 45)) == "solo_alineaciones"            # a 75 min: toca
    _ejec(bd, "solo_alineaciones", _t(13, 45))
    assert automatico.decidir(bd, CFG, _t(13, 50)) is None                           # recién intentado: espera 10 min
    assert automatico.decidir(bd, CFG, _t(13, 58)) == "solo_alineaciones"            # 13 min después vuelve a intentar
    bd.execute("INSERT INTO alineaciones (partido_id, equipo_id, jugador_nombre, titular, confirmada, fuente) VALUES (%s, %s, 'J', true, true, 'espn')",
               (pid, local))
    assert automatico.decidir(bd, CFG, _t(13, 58)) is None                           # ya hay alineación confirmada: no insiste
    cfg = copy.deepcopy(CFG)
    cfg["auto"]["alineaciones"] = False
    bd.execute("DELETE FROM alineaciones")
    assert automatico.decidir(bd, cfg, _t(13, 58)) is None                           # se puede apagar


def test_alineaciones_ligas_sin_fuente_solo_con_id_de_api_football(bd):
    _ejec(bd, "actualizar_todo", _t(7, 15))
    _ejec(bd, "solo_cuotas", _t(13, 0))
    pid, _ = _partido_en(bd, "MXF", _t(15))                                          # Liga MX Femenil: ESPN no está mapeada
    assert automatico.decidir(bd, CFG, _t(13, 45)) is None
    bd.execute("UPDATE partidos SET api_football_id = 123 WHERE id = %s", (pid,))
    assert automatico.decidir(bd, CFG, _t(13, 45)) == "solo_alineaciones"           # con id de API-Football sí


# ------------------------------------------------------------------ previa (~50 min antes de cada horario)

def test_previa_50_min_antes_de_cada_horario_como_la_champions(bd):
    _ejec(bd, "actualizar_todo", _t(7, 15))
    _ejec(bd, "solo_cuotas", _t(10, 0))
    _partido_en(bd, "LL", _t(11, 45))
    _partido_en(bd, "LL", _t(11, 50))                                                 # 5 min después: lo cubre la misma previa
    _partido_en(bd, "LL", _t(14, 0))
    assert automatico.decidir(bd, CFG, _t(10, 50)) != "previa_partido"                # a 55 min: todavía no
    assert automatico.decidir(bd, CFG, _t(10, 56)) == "previa_partido"                # a 49 min de las 11:45
    _ejec(bd, "previa_partido", _t(10, 56))
    assert automatico.decidir(bd, CFG, _t(11, 2)) != "previa_partido"                 # 11:45 y 11:50 ya cubiertos
    assert automatico.decidir(bd, CFG, _t(13, 11)) == "previa_partido"                # el horario de las 14:00 lleva la suya
    _ejec(bd, "previa_partido", _t(13, 11), estado="error")
    assert automatico.decidir(bd, CFG, _t(13, 20)) != "previa_partido"                # un fallo no se reintenta en bucle


def test_previa_fuera_del_horario_diurno_y_no_si_ya_es_tarde(bd):
    _partido_en(bd, "EPL", _t(6, 30))
    assert automatico.decidir(bd, CFG, _t(5, 45)) == "previa_partido"                 # partido de las 6:30: antes de las 7:00 también
    assert automatico.decidir(bd, CFG, _t(6, 20)) is None                             # faltan 10 min (< 15): ya no sirve
    cfg = copy.deepcopy(CFG)
    cfg["auto"]["previa"]["activo"] = False
    assert automatico.decidir(bd, cfg, _t(5, 45)) is None                             # se puede apagar


def test_previa_pide_cuotas_solo_de_los_partidos_proximos(monkeypatch):
    from types import SimpleNamespace

    from golmodel.ingesta import pipeline as pl
    p = object.__new__(pl.Pipeline)
    p.cfg = CFG
    partidos = [{"id": 1, "liga": "LL", "inicio": _t(11, 45), "solo_mercado": False},
                {"id": 2, "liga": "MK:soccer_uefa_champs_league", "inicio": _t(14), "solo_mercado": True},
                {"id": 3, "liga": "EPL", "inicio": _t(20), "solo_mercado": False}]
    llamadas = []
    real = pl.Pipeline._etapa_cuotas

    def espia(self, st, *, tipo="actual", cap=pl.Capacidad.CUOTAS, partidos=None, _general=False):
        if partidos is not None:
            llamadas.append(sorted(x["id"] for x in partidos))
            return {"cuotas": 0, "avisos": []}
        if _general:
            llamadas.append("general")
            return {"cuotas": 0, "avisos": []}
        return real(self, st, tipo=tipo, cap=cap, partidos=partidos, _general=_general)
    monkeypatch.setattr(pl.repo, "partidos_de_dias", lambda conn, dias: partidos)
    monkeypatch.setattr(pl.Pipeline, "_previa_hace_general", lambda self, conn, ahora: False)
    monkeypatch.setattr(pl.Pipeline, "_etapa_cuotas", espia)
    monkeypatch.setattr(pl.Pipeline, "_competiciones_mercado", lambda self, st: llamadas.append("mercado") or {"creditos": 2})
    r = p._etapa_cuotas(SimpleNamespace(conn=None, ahora=_t(10, 55), tipo="previa_partido", dias=lambda: []))
    assert llamadas == [[1]] and r["partidos_previa"] == 1                            # solo LaLiga 11:45; nada de mercado
    llamadas.clear()
    r = p._etapa_cuotas(SimpleNamespace(conn=None, ahora=_t(13, 10), tipo="previa_partido", dias=lambda: []))
    assert llamadas == [[], "mercado"] and r["mercado"] == {"creditos": 2}           # Champions 14:00: competiciones de mercado
    # Si a la consulta general le falta poco, la previa la hace (todas las ligas) y queda marcada como completa
    llamadas.clear()
    monkeypatch.setattr(pl.Pipeline, "_previa_hace_general", lambda self, conn, ahora: True)
    r = p._etapa_cuotas(SimpleNamespace(conn=None, ahora=_t(13, 10), tipo="previa_partido", dias=lambda: []))
    assert llamadas == ["general"] and r["completa"] is True


def _previa_con_cuotas(bd, inicio, completa):
    eid = bd.execute("INSERT INTO ejecuciones (tipo, iniciada_en, estado, disparada_por) VALUES ('previa_partido', %s, 'ok', 'job') "
                     "RETURNING id", (inicio,)).fetchone()["id"]
    bd.execute("INSERT INTO ejecucion_etapas (ejecucion_id, orden, etapa, estado, resumen) VALUES (%s, 2, 'cuotas', 'ok', %s::jsonb)",
               (eid, '{"completa": true}' if completa else '{"partidos_previa": 2}'))


def test_previa_completa_cuenta_como_consulta_general_de_cuotas(bd):
    _ejec(bd, "actualizar_todo", _t(7, 15))
    _ejec(bd, "solo_cuotas", _t(11, 11))
    _partido_en(bd, "MXF", _t(14, 0))                                                 # sin fuente de alineaciones: aísla las cuotas
    _previa_con_cuotas(bd, _t(13, 10), completa=False)                                # previa chica: solo partidos próximos
    assert automatico.decidir(bd, CFG, _t(13, 14)) == "solo_cuotas"                  # la general sigue tocando (2 h desde 11:11)
    _previa_con_cuotas(bd, _t(13, 12), completa=True)                                 # previa que hizo la general
    assert automatico.decidir(bd, CFG, _t(13, 14)) is None                            # ya no se repite a los 3 minutos
    assert automatico.ultima_general_cuotas(bd) == _t(13, 12)


def test_la_previa_decide_hacer_la_general_si_le_falta_menos_del_adelanto(bd):
    from golmodel.ingesta import pipeline as pl
    p = object.__new__(pl.Pipeline)
    p.cfg = CFG
    _ejec(bd, "solo_cuotas", _t(11, 11))
    _partido_en(bd, "LL", _t(14, 0))                                                  # partido cerca: la general toca cada 2 h
    assert p._previa_hace_general(bd, _t(13, 10))                                     # a la general le falta 1 min: la hace la previa
    assert not p._previa_hace_general(bd, _t(11, 40))                                 # le faltan 91 min: previa chica
    cfg = copy.deepcopy(CFG)
    del cfg["auto"]["previa"]["adelantar_general_min"]
    p.cfg = cfg
    assert not p._previa_hace_general(bd, _t(13, 10))                                 # sin la clave: comportamiento anterior


def test_registro_de_parlays_guarda_una_vez_y_es_inmutable(bd):
    import psycopg
    from golmodel.servicios import parlays_usuario as pu
    pierna = lambda pid, p: {"partido_id": pid, "liga": "LL", "partido": f"A{pid} vs B{pid}", "inicio": "2026-11-18T20:00:00+00:00",
                             "tipo": "gana", "jugada": f"Gana A{pid}", "p": p, "detalle": "x"}
    armado = {"que_meter": [{"titulo": "Opción A · la más probable", "n": 3, "p": 0.5, "piernas": [pierna(1, 0.8), pierna(2, 0.8), pierna(3, 0.78)]}],
              "sonadoras": [], "boletos": [{"titulo": "🏆 Boleto seguro", "n": 2, "p": 0.6, "piernas": [pierna(4, 0.8), pierna(5, 0.75)]}]}
    dia = _t(12).date()
    assert pu.registrar(bd, CFG, dia, _t(7, 15), "actualizar_todo", armado=armado) == 2
    assert pu.registrar(bd, CFG, dia, _t(13, 10), "previa_partido", armado=armado) == 0          # mismas piernas: no se duplica
    fila = bd.execute("SELECT * FROM parlays_registro WHERE seccion = 'que_meter'").fetchone()
    assert fila["n"] == 3 and [x["partido_id"] for x in fila["piernas"]] == [1, 2, 3] and "detalle" not in fila["piernas"][0]
    with pytest.raises(psycopg.errors.RestrictViolation):
        bd.execute("UPDATE parlays_registro SET p = 0.9")
