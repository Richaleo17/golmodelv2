"""integracion/candidatas.py con PostgreSQL REAL poblada por el pipeline V2 real (mundo sintético de test_integracion).

Verificado con datos SINTÉTICOS: comprueba el contrato y la reproducibilidad del adaptador, no la calidad del modelo.
Requiere PostgreSQL (GOLMODEL_TEST_ADMIN_DSN); se omite con motivo si no hay.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from golmodel import db
from golmodel.integracion.candidatas import MOTIVOS, _matriz_decision, construir_candidatas
from golmodel.modelos.calibracion_mercado import SENTINEL_SIN_CALIBRADOR, Calibrador, segmento_de
from golmodel.modelos.mercados import APTAS_PARLAY
from golmodel.motor.tickets import Candidata
from golmodel.servicios.prediccion import cuotas_ejecucion
# fixtures del mundo real (se reutilizan tal cual: mismo pipeline V2, no un mock)
from tests.test_integracion import AHORA, HOY, cargado, cfg, correr, dsn, mundo  # noqa: F401
from tests.test_tickets import _cfg


@pytest.fixture(scope="module")
def poblada(dsn, cfg, mundo, cargado):  # noqa: F811
    conn, _eid, et = correr(dsn, cfg, mundo, "actualizar_todo", AHORA)
    assert not {k: v["error"] for k, v in et.items() if v["estado"] == "error"}
    conn.close()
    return dsn


@pytest.fixture
def conn(poblada):
    c = db.conectar(poblada)
    yield c
    c.rollback()                      # cada test puede mutar dentro de su transacción sin contaminar a los demás
    c.close()


@pytest.fixture(scope="module")
def v3():
    return _cfg()


def _dia(conn):
    return conn.execute("SELECT dia_mx(min(inicio)) AS d FROM partidos WHERE estado = 'programado'").fetchone()["d"]


def _run(conn, cfg, v3, ahora=AHORA, **kw):
    return construir_candidatas(conn, _dia(conn), ahora, cfg, v3, **kw)


# ------------------------------------------------------------------ contrato con datos

def test_con_datos_las_candidatas_estan_completas_y_son_validas(conn, cfg, v3):
    r = _run(conn, cfg, v3)
    assert r.candidatas, r.omisiones
    ids_con = {c.partido_id for c in r.candidatas}
    assert len(ids_con) >= len(HOY) - 1
    for c in r.candidatas:
        assert isinstance(c, Candidata)
        # TODOS los campos no nulos, salvo crs_total que es Optional por contrato
        for campo, valor in c.__dict__.items():
            assert valor is not None or campo == "crs_total", campo
        assert c.crs_total is None or 0 <= c.crs_total <= 100
        assert c.lambda_local > 0 and c.lambda_visitante > 0 and c.peso_efectivo_local > 0 and c.peso_efectivo_visitante > 0
        assert c.cuota > 1.0 and 0.0 < c.calibrada.p_calibrada < 1.0
        assert set(c.selecciones) <= APTAS_PARLAY and len(c.selecciones) in (1, 2)
        assert c.info_hasta.tzinfo is not None and c.info_hasta <= AHORA                  # nunca el futuro
        assert isinstance(c.crs_veto, bool)
        assert c.calibrada.segmento == segmento_de(c.liga, c.selecciones, c.cuota, v3)    # una sola definición de segmento


def test_sin_registro_de_calibradores_es_identidad_marcada_no_calibrada(conn, cfg, v3):
    for c in _run(conn, cfg, v3).candidatas:
        assert c.calibrada.calibrado is False
        assert c.calibrada.calibrador_version == SENTINEL_SIN_CALIBRADOR


def test_p_cruda_coincide_con_la_p_modelo_que_persistio_v2(conn, cfg, v3):
    """Cruce independiente: el adaptador recalcula desde la matriz guardada; V2 guardó p_modelo al predecir."""
    verificadas = 0
    for c in _run(conn, cfg, v3).candidatas:
        if len(c.selecciones) != 1:
            continue
        fila = conn.execute("""
            SELECT ps.p_modelo FROM predicciones_seleccion ps JOIN predicciones p ON p.id = ps.prediccion_id
            WHERE p.partido_id = %s AND ps.seleccion = %s AND p.origen = 'produccion'
            ORDER BY p.creada_en DESC, p.id DESC LIMIT 1""", (c.partido_id, c.selecciones[0])).fetchone()
        assert c.calibrada.p_calibrada == pytest.approx(fila["p_modelo"], abs=1e-9)
        verificadas += 1
    assert verificadas >= 5


def test_la_cuota_es_la_de_ejecucion_de_v2(conn, cfg, v3):
    import pandas as pd
    from golmodel import repositorio as repo
    for c in _run(conn, cfg, v3).candidatas:
        if len(c.selecciones) != 1:
            continue
        partido = conn.execute("SELECT * FROM partidos WHERE id = %s", (c.partido_id,)).fetchone()
        esperado = cuotas_ejecucion(repo.cuotas_vigentes(conn, c.partido_id), partido, AHORA, cfg)[c.selecciones[0]]
        assert esperado["vigente"] and c.cuota == pytest.approx(esperado["cuota"])
        assert c.cuota == pytest.approx(float(conn.execute(
            "SELECT cuota FROM cuotas WHERE id = %s", (esperado["cuota_id"],)).fetchone()["cuota"]))


def test_lambda_rho_y_peso_son_los_de_la_prediccion_y_su_version(conn, cfg, v3):
    for c in _run(conn, cfg, v3).candidatas[:12]:
        p = conn.execute("""SELECT pr.*, pa.local_id, pa.visitante_id FROM predicciones pr JOIN partidos pa ON pa.id = pr.partido_id
                            WHERE pr.partido_id = %s ORDER BY pr.creada_en DESC, pr.id DESC LIMIT 1""", (c.partido_id,)).fetchone()
        assert (c.lambda_local, c.lambda_visitante, c.rho) == (p["lambda_local"], p["lambda_visitante"], p["rho"])
        pe = {r["equipo_id"]: r["peso_efectivo"] for r in conn.execute(
            "SELECT equipo_id, peso_efectivo FROM parametros_equipo WHERE version_id = %s", (p["version_id"],))}
        assert (c.peso_efectivo_local, c.peso_efectivo_visitante) == (pe[p["local_id"]], pe[p["visitante_id"]])


# ------------------------------------------------------------------ SGP (cálculo independiente del adaptador)

def test_sgp_se_arma_con_la_interseccion_de_celdas_y_su_cuota(conn, cfg, v3):
    pid = conn.execute("SELECT partido_id AS p FROM predicciones ORDER BY partido_id LIMIT 1").fetchone()["p"]
    a, b = sorted(("1X2:1", "OU:2.5:over"))
    casa = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    conn.execute("INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente, capturada_en) "
                 "VALUES (%s, %s, %s, %s, 3.40, 'manual', %s)", (pid, casa, a, b, AHORA))
    c = next(x for x in _run(conn, cfg, v3).candidatas if x.partido_id == pid and len(x.selecciones) == 2)
    assert c.selecciones == (a, b) and c.cuota == pytest.approx(3.40)
    claves = [(x.partido_id, x.selecciones) for x in _run(conn, cfg, v3).candidatas]
    assert claves == sorted(claves)                                          # orden determinista también con SGP
    fila = conn.execute("SELECT * FROM predicciones WHERE partido_id = %s ORDER BY creada_en DESC, id DESC LIMIT 1", (pid,)).fetchone()
    usa = fila["crs_modo_ajuste"] == "activo" and fila["matriz_crs"] is not None
    m = np.array(fila["matriz_crs"] if usa else fila["matriz"]).reshape(11, 11)
    esperado = sum(m[i, j] for i in range(11) for j in range(11) if i > j and i + j > 2.5)       # 1 y over 2.5, a mano
    assert c.calibrada.p_calibrada == pytest.approx(esperado, abs=1e-12)
    assert c.calibrada.segmento[1] == "1X2+OU:2.5"


# ------------------------------------------------------------------ None explícito / omisión con motivo (no inventar)

def test_sin_prediccion_asof_no_inventa_candidatas(conn, cfg, v3):
    r = _run(conn, cfg, v3, ahora=AHORA - timedelta(days=1))          # antes de que existiera cualquier predicción
    assert r.candidatas == ()
    assert r.omisiones and {o.motivo for o in r.omisiones} == {"sin_prediccion"}


def test_partido_ya_iniciado_se_omite(conn, cfg, v3):
    r = _run(conn, cfg, v3, ahora=AHORA + timedelta(days=2))
    assert r.candidatas == () and {o.motivo for o in r.omisiones} == {"ya_inicio"}


@pytest.mark.parametrize("lado", ["local_id", "visitante_id"])
def test_sin_peso_efectivo_omite_solo_ese_partido_con_motivo(conn, cfg, v3, lado):
    base = _run(conn, cfg, v3)
    pid = base.candidatas[0].partido_id
    ids = conn.execute(f"SELECT pa.{lado} AS eq, version_id FROM partidos pa JOIN predicciones pr ON pr.partido_id = pa.id "
                       "WHERE pa.id = %s ORDER BY pr.creada_en DESC, pr.id DESC LIMIT 1", (pid,)).fetchone()
    conn.execute("DELETE FROM parametros_equipo WHERE version_id = %s AND equipo_id = %s", (ids["version_id"], ids["eq"]))
    r = _run(conn, cfg, v3)
    assert [o.partido_id for o in r.omisiones if o.motivo == "sin_peso_efectivo"] == [pid]
    assert pid not in {c.partido_id for c in r.candidatas}
    assert {c for c in r.candidatas} == {c for c in base.candidatas if c.partido_id != pid}   # el resto, intacto


def test_sin_cuotas_de_ejecucion_vigentes_omite_con_motivo(conn, cfg, v3):
    c2 = copy.deepcopy(cfg)
    c2["mercado"]["casa_ejecucion"] = "casa_que_no_existe"
    r = _run(conn, c2, v3)
    assert r.candidatas == () and {o.motivo for o in r.omisiones} == {"sin_cuotas_vigentes"}


def test_los_motivos_de_omision_son_un_catalogo_cerrado(conn, cfg, v3):
    for ahora in (AHORA, AHORA - timedelta(days=1), AHORA + timedelta(days=2)):
        assert {o.motivo for o in _run(conn, cfg, v3, ahora=ahora).omisiones} <= set(MOTIVOS)


# ------------------------------------------------------------------ as-of (sin leakage hacia el futuro)

def test_una_cuota_posterior_a_ahora_no_se_usa(conn, cfg, v3):
    base = _run(conn, cfg, v3)
    c0 = base.candidatas[0]
    casa = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    conn.execute("INSERT INTO cuotas (partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote) "
                 "VALUES (%s, %s, %s, 9.990, 'actual', 'manual', %s, gen_random_uuid())",
                 (c0.partido_id, casa, c0.selecciones[0], AHORA + timedelta(hours=1)))
    assert _run(conn, cfg, v3) == base                                     # as-of AHORA: la cuota futura NO existe
    despues = _run(conn, cfg, v3, ahora=AHORA + timedelta(hours=2))
    c1 = next(c for c in despues.candidatas if (c.partido_id, c.selecciones) == (c0.partido_id, c0.selecciones))
    assert c1.cuota == pytest.approx(9.99)                                 # y sí se ve cuando ya ocurrió


def test_una_prediccion_posterior_a_ahora_no_se_usa(conn, cfg, v3):
    base = _run(conn, cfg, v3)
    pid = base.candidatas[0].partido_id
    conn.execute("""INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                    rho, matriz, masa_truncada)
                    SELECT partido_id, version_id, origen, %s, %s, lambda_local * 3, lambda_visitante, rho, matriz, masa_truncada
                    FROM predicciones WHERE partido_id = %s ORDER BY creada_en DESC, id DESC LIMIT 1""",
                 (AHORA + timedelta(hours=1), AHORA + timedelta(hours=1), pid))
    assert _run(conn, cfg, v3) == base


# ------------------------------------------------------------------ reproducibilidad

def test_mismo_estado_de_bd_mismo_resultado_orden_incluido(conn, cfg, v3):
    a, b = _run(conn, cfg, v3), _run(conn, cfg, v3)
    assert a == b and a.candidatas == b.candidatas
    claves = [(c.partido_id, c.selecciones) for c in a.candidatas]
    assert claves == sorted(claves) and len(set(claves)) == len(claves)


def test_una_conexion_nueva_da_el_mismo_resultado(poblada, conn, cfg, v3):
    otra = db.conectar(poblada)
    try:
        assert construir_candidatas(otra, _dia(otra), AHORA, cfg, v3) == _run(conn, cfg, v3)
    finally:
        otra.close()


# ------------------------------------------------------------------ calibradores (cuando existan)

def test_con_calibrador_del_segmento_p_calibrada_cambia_y_se_marca(conn, cfg, v3):
    base = _run(conn, cfg, v3)
    c0 = base.candidatas[0]
    cal = Calibrador("temperature", 2, {"T": 1.5})
    r = _run(conn, cfg, v3, registro_calibradores={c0.calibrada.segmento: (cal, "cm-test")})
    m = {(c.partido_id, c.selecciones): c for c in r.candidatas}
    c1 = m[(c0.partido_id, c0.selecciones)]
    assert c1.calibrada.calibrado is True and c1.calibrada.calibrador_version == "cm-test"
    assert c1.calibrada.p_calibrada == pytest.approx(float(cal.aplicar(np.array([c0.calibrada.p_calibrada]))[0]))
    assert c1.calibrada.p_calibrada != pytest.approx(c0.calibrada.p_calibrada)
    for c in r.candidatas:                                                    # los demás segmentos siguen en fallback
        if c.calibrada.segmento != c0.calibrada.segmento:
            assert c.calibrada.calibrado is False


# ------------------------------------------------------------------ reglas de traducción (cada una con su mutante)

def test_matriz_de_decision_replica_la_regla_de_v2():
    m, mc = [0.1] * 121, [0.2] * 121
    assert _matriz_decision({"crs_modo_ajuste": "activo", "matriz_crs": mc, "matriz": m})[0, 0] == 0.2
    assert _matriz_decision({"crs_modo_ajuste": "sombra", "matriz_crs": mc, "matriz": m})[0, 0] == 0.1   # sombra: no se usa
    assert _matriz_decision({"crs_modo_ajuste": "activo", "matriz_crs": None, "matriz": m})[0, 0] == 0.1  # sin veto: base
    assert _matriz_decision({"crs_modo_ajuste": "activo", "matriz_crs": mc, "matriz": m}).shape == (11, 11)


def _copiar_prediccion(conn, pid, creada, *, veto=False, vetadas=(), sin_crs=False):
    """Copia la última predicción del partido con otro instante y, opcionalmente, con veto CRS. Respeta los CHECK del
    esquema (veto => crs_total, favorito y selecciones vetadas presentes; alerta coherente con crs_total)."""
    conn.execute("""INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                    rho, matriz, masa_truncada, crs_total, crs_alerta, crs_favorito, crs_veto, crs_selecciones_vetadas)
                    SELECT partido_id, version_id, origen, %s, %s, lambda_local, lambda_visitante, rho, matriz, masa_truncada,
                           CASE WHEN %s THEN 70 WHEN %s THEN NULL ELSE crs_total END,
                           CASE WHEN %s THEN 'ALTA' WHEN %s THEN 'INSUFICIENTE' ELSE crs_alerta END,
                           CASE WHEN %s THEN 'local' WHEN %s THEN NULL ELSE crs_favorito END, %s, %s
                    FROM predicciones WHERE partido_id = %s ORDER BY creada_en DESC, id DESC LIMIT 1""",
                 (creada, creada, veto, sin_crs, veto, sin_crs, veto, sin_crs, veto, list(vetadas), pid))


def test_crs_veto_de_la_candidata_es_el_del_partido_cruzado_con_sus_selecciones(conn, cfg, v3):
    base = _run(conn, cfg, v3)
    pid = base.candidatas[0].partido_id
    assert not any(c.crs_veto for c in base.candidatas if c.partido_id == pid)
    casa = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    conn.execute("INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente, capturada_en) "
                 "VALUES (%s, %s, '1X2:1', 'OU:2.5:over', 3.4, 'manual', %s)", (pid, casa, AHORA))
    _copiar_prediccion(conn, pid, AHORA + timedelta(minutes=5), veto=True, vetadas=["1X2:1"])
    r = _run(conn, cfg, v3, ahora=AHORA + timedelta(minutes=10))
    veto = {c.selecciones: c.crs_veto for c in r.candidatas if c.partido_id == pid}
    assert veto[("1X2:1",)] is True and veto[("1X2:1", "OU:2.5:over")] is True       # contiene la selección vetada
    assert veto[("1X2:2",)] is False and veto[("OU:2.5:over",)] is False             # las demás, no
    assert not any(c.crs_veto for c in r.candidatas if c.partido_id != pid)


def test_info_hasta_es_el_instante_mas_antiguo_entre_prediccion_y_cuota(conn, cfg, v3):
    pid = _run(conn, cfg, v3).candidatas[0].partido_id
    _copiar_prediccion(conn, pid, AHORA + timedelta(minutes=30))                      # predicción más NUEVA que la cuota
    r = _run(conn, cfg, v3, ahora=AHORA + timedelta(minutes=40))
    cs = [c for c in r.candidatas if c.partido_id == pid and len(c.selecciones) == 1]
    assert cs and all(c.info_hasta == AHORA for c in cs)                              # manda la cuota (más vieja)
    # y a la inversa: cuota nueva, predicción vieja -> manda la predicción
    casa = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    conn.execute("INSERT INTO cuotas (partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote) "
                 "VALUES (%s, %s, '1X2:1', 2.0, 'actual', 'manual', %s, gen_random_uuid())", (pid, casa, AHORA + timedelta(minutes=35)))
    r2 = _run(conn, cfg, v3, ahora=AHORA + timedelta(minutes=40))
    c = next(c for c in r2.candidatas if c.partido_id == pid and c.selecciones == ("1X2:1",))
    assert c.info_hasta == AHORA + timedelta(minutes=30)


def test_la_captura_manual_vigente_reemplaza_a_la_casa_de_ejecucion(conn, cfg, v3):
    base = _run(conn, cfg, v3)
    c0 = base.candidatas[0]
    manual = conn.execute("SELECT id FROM casas_apuestas WHERE clave = 'manual'").fetchone()["id"]
    conn.execute("INSERT INTO cuotas (partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote) "
                 "VALUES (%s, %s, %s, 7.70, 'actual', 'manual', %s, gen_random_uuid())", (c0.partido_id, manual, c0.selecciones[0], AHORA))
    c1 = next(c for c in _run(conn, cfg, v3).candidatas if (c.partido_id, c.selecciones) == (c0.partido_id, c0.selecciones))
    assert c1.cuota == pytest.approx(7.70) and c1.cuota != pytest.approx(c0.cuota)


def test_el_peso_efectivo_sale_de_la_version_de_ESA_prediccion_no_de_la_ultima(conn, cfg, v3):
    base = _run(conn, cfg, v3)
    c0 = base.candidatas[0]
    cols = [r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'versiones_modelo' AND column_name <> 'id' "
        "ORDER BY ordinal_position")]
    lista = ", ".join(cols)
    v_old = conn.execute("SELECT version_id FROM predicciones WHERE partido_id = %s ORDER BY creada_en DESC, id DESC LIMIT 1",
                         (c0.partido_id,)).fetchone()["version_id"]
    v_new = conn.execute(f"INSERT INTO versiones_modelo ({lista}) SELECT {lista} FROM versiones_modelo WHERE id = %s RETURNING id",
                         (v_old,)).fetchone()["id"]
    conn.execute("INSERT INTO parametros_equipo (version_id, equipo_id, liga_id, ataque, defensa, peso_efectivo, usa_prior_ascendido) "
                 "SELECT %s, equipo_id, liga_id, ataque, defensa, peso_efectivo * 100, usa_prior_ascendido FROM parametros_equipo "
                 "WHERE version_id = %s", (v_new, v_old))
    r = _run(conn, cfg, v3)                                                      # la predicción sigue siendo de v_old
    assert r.candidatas == base.candidatas


def test_sin_crs_el_campo_es_none_explicito_no_cero(conn, cfg, v3):
    pid = _run(conn, cfg, v3).candidatas[0].partido_id
    _copiar_prediccion(conn, pid, AHORA + timedelta(minutes=5), sin_crs=True)
    r = _run(conn, cfg, v3, ahora=AHORA + timedelta(minutes=10))
    cs = [c for c in r.candidatas if c.partido_id == pid]
    assert cs and all(c.crs_total is None and c.crs_veto is False for c in cs)


def test_selecciones_no_aptas_para_parlay_no_son_candidatas(conn, cfg, v3):
    """Las que empujan (AH ±1.0) no son evaluables por dependencia.py ni aptas en tickets.py: no deben llegar."""
    pid = _run(conn, cfg, v3).candidatas[0].partido_id
    casa = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    no_aptas = sorted({r["codigo"] for r in conn.execute("SELECT codigo FROM selecciones")} - APTAS_PARLAY)
    assert no_aptas and all(s.startswith("AH:") and s.endswith(".0") for s in no_aptas), no_aptas      # ±1.0 (push)
    for s in no_aptas:
        conn.execute("INSERT INTO cuotas (partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote) "
                     "VALUES (%s, %s, %s, 1.9, 'actual', 'manual', %s, gen_random_uuid())", (pid, casa, s, AHORA))
    conn.execute("INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente, capturada_en) "
                 "VALUES (%s, %s, '1X2:1', 'AH:local:-1.0', 3.1, 'manual', %s)", (pid, casa, AHORA))      # SGP con una no apta
    r = _run(conn, cfg, v3)
    assert set().union(*(set(c.selecciones) for c in r.candidatas)) <= APTAS_PARLAY


def test_una_cuota_sgp_vencida_no_se_usa(conn, cfg, v3):
    pid = _run(conn, cfg, v3).candidatas[0].partido_id
    casa = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    conn.execute("INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente, capturada_en) "
                 "VALUES (%s, %s, '1X2:1', 'OU:2.5:over', 3.4, 'manual', %s)", (pid, casa, AHORA - timedelta(days=3)))
    r = _run(conn, cfg, v3)
    assert not [c for c in r.candidatas if len(c.selecciones) == 2]            # vencida: no hay SGP vigente


def test_una_cuota_sgp_posterior_a_ahora_no_se_usa(conn, cfg, v3):
    pid = _run(conn, cfg, v3).candidatas[0].partido_id
    casa = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    conn.execute("INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente, capturada_en) "
                 "VALUES (%s, %s, '1X2:1', 'OU:2.5:over', 3.4, 'manual', %s)", (pid, casa, AHORA + timedelta(hours=1)))
    assert not [c for c in _run(conn, cfg, v3).candidatas if len(c.selecciones) == 2]               # aún no existía
    assert [c for c in _run(conn, cfg, v3, ahora=AHORA + timedelta(hours=1, minutes=5)).candidatas if len(c.selecciones) == 2]
