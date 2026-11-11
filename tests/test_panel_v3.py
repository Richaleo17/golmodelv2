"""/panel sin cálculo bajo independencia (decisión 2026-11-09): solo el ticket V3 (5-8 piernas) o la abstención con su motivo.
Los parlays por perfil, «más seguros» y los tickets de 5 equipos de V2 (producto de probabilidades) se retiraron.
PostgreSQL real poblada por el pipeline V2 real; datos SINTÉTICOS."""
from __future__ import annotations

import inspect
from datetime import timedelta

import pytest

from golmodel.integracion import benchmark as bm
from golmodel.servicios import sugerencias
from tests.test_integracion import AHORA as AHORA_BD, cargado, cfg, dsn, mundo  # noqa: F401
from tests.test_integracion_benchmark import _ticket_con_partidos_reales, bd, con  # noqa: F401
from tests.test_integracion_candidatas import _dia, poblada  # noqa: F401
from tests.test_tickets import AHORA, _cfg, _version

RETIRADOS = ("parlays", "tickets", "mas_seguros", "advertencia")


def _panel(con, cfg):  # noqa: F811
    return sugerencias.panel(con, cfg, _dia(con), AHORA_BD)


def _abstencion(con, dia, ahora=AHORA):  # noqa: F811
    salida, veredicto, error = bm.decidir([], {}, _version(), _cfg(), semilla=1, ahora=ahora)
    bm.persistir(con, bm.DecisionV3(dia, ahora, None, salida, veredicto, error))


def test_el_panel_no_trae_los_bloques_de_v2_y_si_la_clave_v3(con, cfg):  # noqa: F811
    pan = _panel(con, cfg)
    assert not set(RETIRADOS) & set(pan)
    assert "v3" in pan and {"mejores", "valor", "rompe", "partidos", "resumen"} <= set(pan)      # el resto sigue


def test_sin_decision_v3_el_panel_lo_dice_con_none(con, cfg):  # noqa: F811
    con.execute("DROP TABLE auditoria_decisiones CASCADE")      # sin la 004 (DDL transaccional: el fixture hace rollback)
    pan = _panel(con, cfg)           # (una sola vez: decision_v3 hace rollback al ver la tabla ausente, lo que también deshace el DROP)
    assert pan["v3"] is None
    assert "todavía no hay decisión V3" in sugerencias.texto_panel(pan)


def test_la_abstencion_aparece_con_su_motivo_y_sin_ticket(con, cfg):  # noqa: F811
    _abstencion(con, _dia(con))
    v3 = _panel(con, cfg)["v3"]
    assert v3["ticket"] is None and v3["estado"] == "ABSTENCION" and v3["motivo"] == "sin_piernas_elegibles"
    assert v3["modo_experimental"] is True and v3["calibrado_con"] == "sinteticos"      # el de _version() de tests
    txt = sugerencias.texto_panel(_panel(con, cfg))
    assert "ABSTENCIÓN: sin_piernas_elegibles" in txt and "Ticket V3" in txt


def test_el_ticket_v3_trae_piernas_con_hora_y_jugada_legible(con, cfg):  # noqa: F811
    dia = _dia(con)
    d = _ticket_con_partidos_reales(con, dia)
    bm.persistir(con, d)
    v3 = _panel(con, cfg)["v3"]
    t = v3["ticket"]
    assert v3["estado"] == d.veredicto.estado and t["n_bloques"] == d.ticket.n and len(t["bloques"]) == d.ticket.n
    assert t["p_parlay"] == pytest.approx(d.ticket.p_conservadora) and t["p_independencia"] == pytest.approx(d.ticket.p_independencia)
    for b in t["bloques"]:
        assert len(b["hora"]) == 5 and b["hora"][2] == ":"
        assert b["jugada"] == "Más de 1.5 goles"                       # texto de apuestas, no el código OU:1.5:over
        assert b["local"] != "?" and "OU:" not in b["jugada"]
    txt = sugerencias.texto_panel(_panel(con, cfg))
    assert f"{d.ticket.n} partidos" in txt and "Más de 1.5 goles" in txt and v3["advertencia"] in txt
    assert "independencia" not in txt.lower().replace("si fueran independientes", "")      # solo la comparación explícita


def test_la_ultima_decision_del_dia_manda_en_el_panel(con, cfg):  # noqa: F811
    dia = _dia(con)
    bm.persistir(con, _ticket_con_partidos_reales(con, dia))
    _abstencion(con, dia, ahora=AHORA + timedelta(minutes=5))
    assert _panel(con, cfg)["v3"]["ticket"] is None                       # la abstención más reciente reemplaza al ticket


def test_sugerencias_no_calcula_nada_por_producto_de_probabilidades():
    src = inspect.getsource(sugerencias)
    assert "math.prod" not in src and "itertools" not in src
    for retirada in ("armar_perfil", "armar_tickets", "_combinaciones", "_ficha", "ADVERTENCIA_INDEPENDENCIA"):
        assert not hasattr(sugerencias, retirada), retirada


def test_la_config_ya_no_tiene_las_claves_de_los_bloques_retirados():
    from golmodel.config import cargar
    cs = cargar()["sugerencias"]
    assert not {"perfiles", "mas_seguros", "ticket", "top_piernas"} & set(cs)
    assert {"p_segura", "max_mejores", "rompe"} <= set(cs)                  # lo que sí usa el panel


def test_la_api_sirve_el_panel_con_v3_y_sin_advertencia_de_independencia(con, bd, cfg, mundo):  # noqa: F811
    from datetime import datetime, timezone
    import pandas as pd
    from fastapi.testclient import TestClient
    from golmodel import db
    from golmodel.api import main
    from tests.test_integracion import JORNADA, cadenas
    dia = _dia(con)
    d = _ticket_con_partidos_reales(con, dia)
    bm.persistir(con, d)
    con.commit()
    main._servicio = main.Servicio(cfg, cadenas(mundo, cfg), dsn=bd,
                                   reloj=lambda: pd.Timestamp(JORNADA).to_pydatetime() + timedelta(hours=5))
    try:
        j = TestClient(main.app).get(f"/panel/{dia.isoformat()}").json()
    finally:
        main._servicio = None
    assert not set(RETIRADOS) & set(j)
    assert j["v3"]["ticket"]["n_bloques"] == d.ticket.n and "independencia" not in j["texto"].lower().replace("si fueran independientes", "")
