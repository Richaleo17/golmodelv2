"""Almacén de VersionCalibradorTickets (migración 005) sobre PostgreSQL real, y su uso en la ruta en vivo (benchmark)."""
from __future__ import annotations

import copy

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from golmodel.backtest.calibracion_tickets import VersionCalibradorTickets
from golmodel.integracion import almacen_calibradores as al
from golmodel.integracion import benchmark as bm
from tests.test_integracion import AHORA as AHORA_BD, cargado, cfg, dsn, mundo  # noqa: F401
from tests.test_integracion_benchmark import bd, con  # noqa: F401
from tests.test_integracion_candidatas import _dia, poblada, v3  # noqa: F401


def _v(c=0.8123456789012345, con_="oos_real", sigma_liga=0.1, sigma_g=0.0, hasta=datetime(2026, 1, 1, tzinfo=timezone.utc)):
    return VersionCalibradorTickets(datos_hasta=hasta, config_hash="h1", n_draws=2000, semilla=3, metodo="global_walkforward",
                                    segmentos_calibrados=[["EPL", "1X2", "2-3"]], segmentos_fallback=[["MX1", "OU2.5", "<1.3"]],
                                    c_global=c, sigma_liga=sigma_liga, sigma_g=sigma_g, calibrado_con=con_)


def test_ida_y_vuelta_exacta_de_la_version(con):  # noqa: F811
    for sl in (0.1, {"EPL": 0.2, "MX1": 0.15}):
        v = _v(sigma_liga=sl, sigma_g=0.05)
        i = al.guardar_version(con, v, gates_ok=True, resultado={"folds": [{"k": 1}]})
        f = con.execute("SELECT * FROM versiones_calibrador_tickets WHERE id = %s", (i,)).fetchone()
        assert al.de_fila(f) == v                                          # == sobre el dataclass: bit a bit, incl. el float de c_global
        assert f["gates_ok"] is True and f["resultado_json"] == {"folds": [{"k": 1}]}


def test_el_resultado_con_fechas_y_numpy_se_serializa(con):  # noqa: F811
    r = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc), "x": np.float64(1.5), "n": np.int64(3), "nan_no": 1}
    i = al.guardar_version(con, _v(), gates_ok=True, resultado=r)
    g = con.execute("SELECT resultado_json FROM versiones_calibrador_tickets WHERE id = %s", (i,)).fetchone()["resultado_json"]
    assert g["x"] == 1.5 and g["n"] == 3 and g["t"].startswith("2026-01-01")


def test_oos_real_sin_gates_ok_se_rechaza_antes_de_llegar_a_la_bd(con):  # noqa: F811
    with pytest.raises(ValueError, match="oos_real"):
        al.guardar_version(con, _v(con_="oos_real"), gates_ok=False, resultado={})


def test_solo_se_carga_una_version_valida(con):  # noqa: F811
    antes = al.cargar_ultima_valida(con)
    al.guardar_version(con, _v(c=0.5, con_="sinteticos"), gates_ok=True, resultado={})        # sintética: no
    al.guardar_version(con, _v(c=0.6, con_="no_calibrado"), gates_ok=False, resultado={})     # falló gates: no
    assert al.cargar_ultima_valida(con) == antes                                               # nada de lo anterior se carga
    i = al.guardar_version(con, _v(c=0.7, con_="oos_real"), gates_ok=True, resultado={})
    assert al.cargar_ultima_valida(con) == (i, _v(c=0.7, con_="oos_real"))


def test_la_mas_reciente_valida_gana_y_hasta_filtra_as_of(con):  # noqa: F811
    t1, t2 = datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 4, 1, tzinfo=timezone.utc)
    a = al.guardar_version(con, _v(c=0.3), gates_ok=True, resultado={}, creado_en=t1)
    b = al.guardar_version(con, _v(c=0.4), gates_ok=True, resultado={}, creado_en=t2)
    assert al.cargar_ultima_valida(con, datetime(2026, 5, 1, tzinfo=timezone.utc))[0] == b      # la más reciente disponible
    assert al.cargar_ultima_valida(con, t2 - timedelta(seconds=1))[0] == a                      # antes de t2 solo existía la primera
    assert al.cargar_ultima_valida(con, t1)[0] == a                                             # el borde es inclusivo
    assert al.cargar_ultima_valida(con, t1 - timedelta(seconds=1)) is None                      # antes de t1 no existía ninguna
    assert al.cargar_ultima_valida(con)[0] == b                                                 # sin `hasta`: la más reciente


def test_misma_marca_de_tiempo_desempata_por_id(con):  # noqa: F811
    t = datetime(2026, 3, 1, tzinfo=timezone.utc)
    al.guardar_version(con, _v(c=0.3), gates_ok=True, resultado={}, creado_en=t)
    b = al.guardar_version(con, _v(c=0.4), gates_ok=True, resultado={}, creado_en=t)
    assert al.cargar_ultima_valida(con, t)[0] == b


def test_sin_la_migracion_devuelve_none_y_la_conexion_sigue_usable(con):  # noqa: F811
    con.execute("DROP TABLE versiones_calibrador_tickets")
    assert al.cargar_ultima_valida(con) is None
    assert con.execute("SELECT 1 AS uno").fetchone()["uno"] == 1                                # el SAVEPOINT evitó abortar la transacción


# ------------------------------------------------------------------ la ruta en vivo usa la versión válida y NO otra
def test_benchmark_usa_la_version_valida_guardada_y_si_no_hay_la_provisional(con, cfg, v3):  # noqa: F811
    dia = _dia(con)
    base = bm.generar_decision_v3(con, dia, AHORA_BD, cfg, v3)
    assert base.salida.auditoria.calibrado_con == "no_calibrado"                               # sin versión válida: la provisional
    al.guardar_version(con, _v(c=0.9, con_="sinteticos"), gates_ok=True, resultado={})
    assert bm.generar_decision_v3(con, dia, AHORA_BD, cfg, v3).salida.auditoria.calibrado_con == "no_calibrado"   # sintética: NO se usa
    v = _v(c=0.9, con_="oos_real", hasta=AHORA_BD - timedelta(days=2))
    al.guardar_version(con, v, gates_ok=True, resultado={}, creado_en=AHORA_BD - timedelta(days=1))
    d = bm.generar_decision_v3(con, dia, AHORA_BD, cfg, v3)
    assert d.salida.auditoria.calibrado_con == "oos_real" and d.salida.auditoria.c_seg["c_global"] == 0.9
    sin_beta = copy.deepcopy(v3)
    sin_beta["modo_operativo_beta"]["activo"] = False
    d_off = bm.generar_decision_v3(con, dia, AHORA_BD, cfg, sin_beta)
    assert d_off.salida.auditoria.modo_experimental is True and d_off.beta == {"solicitado": False, "efectivo": False}   # sin Beta: la bandera viene de la config
    assert d_off.salida.auditoria.calibrado_con == "oos_real"                                    # (una versión oos_real SOLA no apaga el techo)
    if v3["modo_operativo_beta"]["activo"]:                                                     # con el Beta encendido en la config del repositorio (v0.15.2)
        assert d.beta["efectivo"] is True and d.salida.auditoria.modo_experimental is False and d.salida.auditoria.techo_estado == "SIN_TOPE"
        assert v3["modo_experimental"] is True                                                  # y la config de modo_experimental sigue intacta
    # as-of: una versión creada DESPUÉS de `ahora` no existe todavía para esa decisión
    futura = _v(c=0.123, con_="oos_real")
    al.guardar_version(con, futura, gates_ok=True, resultado={}, creado_en=AHORA_BD + timedelta(days=1))
    assert bm.generar_decision_v3(con, dia, AHORA_BD, cfg, v3).salida.auditoria.c_seg["c_global"] == 0.9


def test_una_version_explicita_gana_sobre_la_guardada(con, cfg, v3):  # noqa: F811
    al.guardar_version(con, _v(c=0.9, con_="oos_real"), gates_ok=True, resultado={})
    d = bm.generar_decision_v3(con, _dia(con), AHORA_BD, cfg, v3, version_cal=bm.version_provisional(v3, AHORA_BD))
    assert d.salida.auditoria.calibrado_con == "no_calibrado"
