"""Cerebro 2 contra PostgreSQL REAL: persistencia, candados de la BD y job de metacalibración desde las tablas.

Sigue la convención de tests/test_integracion.py: usa GOLMODEL_TEST_ADMIN_DSN y se OMITE (skip, con motivo) si no hay
PostgreSQL. Para correrlo aquí:
  GOLMODEL_TEST_ADMIN_DSN='postgresql://golmodel:golmodel_local@localhost:5432/postgres?connect_timeout=5' pytest -rs ...
La BD se crea desde cero con schema.sql + seed.sql y las migraciones pasan por el RUNNER REAL de la app (incluida la 004).
Cada test corre en una transacción que se revierte: no se contaminan entre sí.
"""
import dataclasses
import os
import uuid
from datetime import datetime
from pathlib import Path

import psycopg
import pytest

from golmodel import db
from golmodel.cerebro2 import persistencia, riesgo
from golmodel.cerebro2.decision import GateEval, Veredicto
from golmodel.cerebro2.metacalibracion import calcular_desde_bd
from golmodel.migraciones import aplicar
from tests.test_cerebro2 import _eval, _ideal
from tests.test_tickets import AHORA, _cfg

RAIZ = Path(__file__).resolve().parents[1]
ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")


@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible (define GOLMODEL_TEST_ADMIN_DSN)")
    nombre = f"golmodel_c2_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn_ = ADMIN.replace("/postgres?", f"/{nombre}?")
    with psycopg.connect(dsn_, autocommit=True) as c:
        c.execute((RAIZ / "db/schema.sql").read_text())
        c.execute("SET search_path TO golmodel, public")
        c.execute((RAIZ / "db/seed.sql").read_text())
    with db.conectar(dsn_) as c:
        assert "004_cerebro2.sql" in aplicar(c)                    # la 004 llega por el runner real
    yield dsn_
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


@pytest.fixture
def conn(dsn):
    c = db.conectar(dsn)
    yield c
    c.rollback()
    c.close()


def _partidos(conn, resultados: dict):
    """Inserta partidos {id: (estado, goles_local, goles_visitante)} (liga MX1) con equipos propios."""
    for pid, (estado, gl, gv) in resultados.items():
        conn.execute("INSERT INTO equipos (id, nombre, pais) VALUES (%s, %s, 'México'), (%s, %s, 'México')",
                     (1000 + 2 * pid, f"L{pid}", 1001 + 2 * pid, f"V{pid}"))
        conn.execute("INSERT INTO partidos (id, liga_id, temporada, inicio, local_id, visitante_id, estado, goles_local, "
                     "goles_visitante) VALUES (%s, (SELECT id FROM ligas WHERE codigo='MX1'), '2026-A', "
                     "'2026-11-09 18:00+00'::timestamptz + (%s * interval '1 hour'), %s, %s, %s, %s, %s)",
                     (pid, pid, 1000 + 2 * pid, 1001 + 2 * pid, estado, gl, gv))


def _uno(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


# ------------------------------------------------------------------ persistencia


def test_se_persisten_tickets_y_cerebro2_enlazados_por_ticket_id_con_todos_sus_gates(conn):
    _partidos(conn, {i: ("programado", None, None) for i in range(1, 7)})
    s, m = _ideal(flag=True)
    v = _eval(s, m)
    id_t = persistencia.guardar_decision_tickets(conn, s)
    id_c = persistencia.guardar_veredicto(conn, v)
    filas = conn.execute("SELECT id, componente, estado, ticket_id, version_cerebro2 FROM auditoria_decisiones "
                         "WHERE id IN (%s, %s) ORDER BY id", (id_t, id_c)).fetchall()
    assert [f["componente"] for f in filas] == ["tickets", "cerebro2"]
    assert filas[0]["ticket_id"] == filas[1]["ticket_id"] == v.ticket_id             # se unen por ticket_id
    assert (filas[0]["estado"], filas[1]["estado"]) == ("PROPUESTO", "CON_RESERVAS")
    assert filas[0]["version_cerebro2"] is None and filas[1]["version_cerebro2"] == riesgo.VERSION
    cuenta = _uno(conn, "SELECT count(*) AS n, count(*) FILTER (WHERE decisivo) AS d FROM evaluaciones_riesgo "
                         "WHERE auditoria_id = %s", (id_c,))
    assert cuenta["n"] == len(v.gates) and cuenta["d"] == sum(g.decisivo for g in v.gates) >= 1
    p = _uno(conn, "SELECT payload FROM auditoria_decisiones WHERE id = %s", (id_c,))["payload"]
    assert p["estado"] == "CON_RESERVAS" and p["ticket_id"] == v.ticket_id and p["ticket_resumen"]["n"] == v.ticket_resumen["n"]


def test_campos_de_la_auditoria_no_nulos_y_marca_temporal_del_ticket(conn):
    _partidos(conn, {i: ("programado", None, None) for i in range(1, 7)})
    s, m = _ideal(flag=True)
    v = _eval(s, m)
    aid = persistencia.guardar_veredicto(conn, v)
    r = _uno(conn, "SELECT a.*, a.info_asof AT TIME ZONE current_setting('TimeZone') AS asof_local "
                   "FROM auditoria_decisiones a WHERE id = %s", (aid,))
    for col in ("ticket_id", "estado", "modo_experimental", "techo_estado", "calibrado_con", "version_tickets",
                "version_incertidumbre", "version_dependencia", "version_calibracion_tickets", "version_cerebro2",
                "config_hash_restricciones", "semilla", "n_draws", "payload"):
        assert r[col] is not None, col
    assert r["version_calibracion_mercado"] == ["cm-v1"] and r["modo_experimental"] is True
    assert r["asof_local"] == AHORA                                                  # la del ticket de entrada, no now()
    gates = conn.execute("SELECT gate, severidad, valor, umbral, detalle FROM evaluaciones_riesgo WHERE auditoria_id = %s "
                         "AND gate = 'mercado_independiente' LIMIT 1", (aid,)).fetchone()
    assert {"fuente_devig", "metodo_devig", "casas_usadas", "casas_esperadas"} <= set(gates["valor"])


def test_entrada_basura_se_guarda_con_centinelas_explicitos_y_defaults_conservadores(conn):
    v = riesgo.evaluar(object(), None, _cfg())
    aid = persistencia.guardar_veredicto(conn, v, info_asof_respaldo=datetime(2026, 11, 9))
    r = _uno(conn, "SELECT * FROM auditoria_decisiones WHERE id = %s", (aid,))
    assert r["estado"] == "ABSTENCION" and r["categoria"] == "rechazo_por_falta_de_auditoria"
    assert r["version_tickets"] == "NO_DISPONIBLE" and r["ticket_id"] is None             # None explícito, no inventado
    assert (r["modo_experimental"], r["techo_estado"], r["calibrado_con"]) == (True, "CON_RESERVAS", "no_calibrado")
    with pytest.raises(ValueError):                                                         # sin marca temporal NO se usa now()
        persistencia.guardar_veredicto(conn, v)


def test_la_bd_frena_un_aprobado_con_modo_experimental_aunque_cerebro2_fallara(conn):
    _partidos(conn, {i: ("programado", None, None) for i in range(1, 7)})
    v = _eval(*_ideal(flag=True))
    defectuoso = dataclasses.replace(v, estado="APROBADO")                                  # un Cerebro 2 con un bug
    with pytest.raises(psycopg.errors.CheckViolation):
        persistencia.guardar_veredicto(conn, defectuoso)


def test_persistir_con_partidos_inexistentes_falla_en_voz_alta(conn):
    v = _eval(*_ideal(flag=True))                                                           # no se insertaron los partidos
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        persistencia.guardar_veredicto(conn, v)


# ------------------------------------------------------------------ metacalibración desde la BD


def _veredicto(estado, categoria, partido_id, cuota, *, aprobado=False, tid=None):
    resumen = None if partido_id is None else {
        "tipo": "alta_confianza", "n": 1, "partidos": [partido_id], "cuota_combinada": cuota,
        "piernas": [{"partido_id": partido_id, "liga": "MX1", "selecciones": ["1X2:1"], "cuota": cuota}]}
    return Veredicto(
        estado=estado, razon_principal="x" if estado != "APROBADO" else None, categoria=categoria,
        ticket_id=tid or uuid.uuid4().hex[:16], gates=(GateEval("evaluador", "ticket", "OK", orden=1),),
        modo_experimental=not aprobado, techo_estado="SIN_TOPE" if aprobado else "CON_RESERVAS",
        calibrado_con="oos_real" if aprobado else "sinteticos", info_asof=AHORA.isoformat(),
        versiones={"tickets": "t", "calibracion_mercado": ["cm-v1"], "incertidumbre": "i", "dependencia": "d",
                   "calibracion_tickets": "c", "cerebro2": riesgo.VERSION},
        config_hash="h", semilla=1, n_draws=100, ticket_resumen=resumen)


def test_metacalibracion_desde_la_bd_con_resultados_conocidos(conn):
    # Resultado de cada partido; la pierna de todos los tickets es '1X2:1' (gana si goles_local > goles_visitante).
    _partidos(conn, {1: ("finalizado", 0, 1), 2: ("finalizado", 0, 2), 3: ("finalizado", 1, 1), 4: ("finalizado", 3, 0),
                     5: ("finalizado", 0, 1), 6: ("finalizado", 2, 0), 7: ("finalizado", 1, 0), 8: ("finalizado", 0, 0),
                     9: ("finalizado", 2, 1), 10: ("programado", None, None), 11: ("finalizado", 2, 0)})
    SUST, FALTA = "rechazo_sustantivo", "rechazo_por_falta_de_auditoria"
    for pid in (1, 2, 3, 4):                                       # 4 rechazos sustantivos: pierden, pierden, pierden, GANA
        persistencia.guardar_veredicto(conn, _veredicto("RECHAZADO", SUST, pid, 4.0))
    persistencia.guardar_veredicto(conn, _veredicto("RECHAZADO", FALTA, 5, 4.0))             # excluido
    for _ in range(2):                                                                        # abstenciones: excluidas
        persistencia.guardar_veredicto(conn, _veredicto("ABSTENCION", FALTA, None, None))
    for pid in (6, 7, 8):                                          # 3 con reservas: ganan, ganan, pierde
        persistencia.guardar_veredicto(conn, _veredicto("CON_RESERVAS", None, pid, 3.0))
    persistencia.guardar_veredicto(conn, _veredicto("APROBADO", None, 9, 2.0, aprobado=True))    # aprobado que gana
    persistencia.guardar_veredicto(conn, _veredicto("RECHAZADO", SUST, 10, 4.0))                 # partido pendiente
    persistencia.guardar_veredicto(conn, _veredicto("EN_OBSERVACION", None, 11, 5.0))            # no cuenta

    m = calcular_desde_bd(conn, _cfg())
    assert (m.n_rechazados, m.n_aceptados, m.n_excluidos_falta_auditoria, m.n_sin_resolver) == (4, 4, 3, 1)
    assert m.tasa_rechazos_correctos.p == pytest.approx(0.75)
    assert m.tasa_rechazos_incorrectos.p == pytest.approx(0.25)
    assert m.tasa_aprobados_correctos.p == pytest.approx(0.75)
    assert m.yield_rechazados == pytest.approx(0.0)
    assert m.yield_aceptados == pytest.approx(1.0)
    assert m.valor_auditor == pytest.approx(1.0)


def test_metacalibracion_filtra_por_origen(conn):
    _partidos(conn, {1: ("finalizado", 0, 1)})
    persistencia.guardar_veredicto(conn, _veredicto("RECHAZADO", "rechazo_sustantivo", 1, 4.0), origen="paper")
    assert calcular_desde_bd(conn, _cfg(), origen="paper").n_rechazados == 1
    assert calcular_desde_bd(conn, _cfg(), origen="produccion").n_rechazados == 0


def test_payload_extra_se_anade_sin_pisar_las_claves_propias(conn):
    """`dia` (u otra clave) viaja en el payload de tickets; las claves auditoria/ticket no se pisan; por defecto no cambia nada."""
    from golmodel.cerebro2 import persistencia as per
    from golmodel.motor import tickets as tk
    from tests.test_tickets import _pool, _version
    salida = tk.generar_tickets(_pool(), _version(), _cfg(), modo_experimental=True, semilla=11, ahora=AHORA)
    a = per.guardar_decision_tickets(conn, salida)
    b = per.guardar_decision_tickets(conn, salida, payload_extra={"dia": "2026-11-09", "auditoria": "NO_DEBE_PISAR"})
    pa = _uno(conn, "SELECT payload FROM auditoria_decisiones WHERE id = %s", (a,))["payload"]
    pb = _uno(conn, "SELECT payload FROM auditoria_decisiones WHERE id = %s", (b,))["payload"]
    assert "dia" not in pa and set(pa) == {"auditoria", "ticket"}                   # sin extra: igual que antes
    assert pb["dia"] == "2026-11-09" and isinstance(pb["auditoria"], dict) and pb["ticket"] == pa["ticket"]   # no se pisa
