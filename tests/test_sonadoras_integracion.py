"""«Soñadoras» sobre PostgreSQL real (pipeline V2 real), API y UI. La aritmética real hace que NO_DISPONIBLE sea lo normal; el camino PROPUESTA se
ejercita con una soñadora fabricada sobre partidos reales (se sustituye SOLO `generar_sonadoras`, el resto —BD, Cerebro 2, presentación— corre de verdad)."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from golmodel.motor import sonadoras as sn
from golmodel.integracion import sonadoras as son
from tests.test_integracion import AHORA as AHORA_BD, JORNADA, cadenas, cargado, cfg, dsn, mundo  # noqa: F401
from tests.test_integracion_benchmark import bd, con  # noqa: F401
from tests.test_integracion_candidatas import _dia, poblada, v3  # noqa: F401
from tests.test_sonadoras import _gen, _pool
from tests.test_tickets import _cand, _cfg, _version

ZONA = ZoneInfo("America/Mexico_City")


def _fabricada(con):  # noqa: F811
    """Una soñadora PROPUESTA con partido_id reales (los primeros 10 partidos de la BD de prueba)."""
    ids = [r["id"] for r in con.execute("SELECT id FROM partidos ORDER BY id LIMIT 10")]
    assert len(ids) == 10
    pool = [dataclasses.replace(_cand(i, ["EPL", "MX1", "SA"][k % 3], 2.1 + 0.02 * (k % 5), 1.7, edge=1.50), info_hasta=AHORA_BD - timedelta(hours=1))
            for k, i in enumerate(ids)]                                                       # info_hasta con zona horaria, como en el pipeline real
    r = sn.generar_sonadoras(pool, _version(), _cfg(), semilla=11, ahora=AHORA_BD, stake_u=0.1)
    assert r.estado == "PROPUESTA", r.motivo
    return r


def test_en_el_mundo_real_no_hay_sonadora_y_la_presentacion_dice_por_que(con, cfg, v3):  # noqa: F811
    d = son.generar_sonadoras_dia(con, _dia(con), AHORA_BD, cfg, v3)
    assert d.salida.estado == "NO_DISPONIBLE" and d.veredicto is None and d.estado_cerebro2 is None
    p = son.presentar(con, d, ZONA)
    assert p["titulo"] == "🚨 Soñadoras (Alto Riesgo / EV Negativo)"
    assert p["aviso"] == "Esta apuesta es un juego de azar con ventaja para la casa. No forma parte de la estrategia de inversión principal."
    assert p["sonadora"] is None and p["motivo"] and p["promovible"] is False and p["stake_u"] == pytest.approx(0.1)
    dg = p["diagnostico"]
    assert dg["n_candidatas"] > 0 and dg["cuota_maxima_alcanzable"] < v3["sonadoras"]["cuota_objetivo"] and dg["umbrales"]["p_min_pierna"] == 0.75


def test_stake_del_usuario_se_valida(con, cfg, v3):  # noqa: F811
    assert son.generar_sonadoras_dia(con, _dia(con), AHORA_BD, cfg, v3, stake_u=0.3).stake_u == 0.3
    for malo in (0, -0.1, 0.9):
        with pytest.raises(ValueError, match="stake_u"):
            son.generar_sonadoras_dia(con, _dia(con), AHORA_BD, cfg, v3, stake_u=malo)


def test_camino_propuesta_audita_con_cerebro2_y_presenta_piernas_legibles(con, cfg, v3, monkeypatch):  # noqa: F811
    fab = _fabricada(con)
    monkeypatch.setattr(son.sn, "generar_sonadoras", lambda *a, **k: fab)
    d = son.generar_sonadoras_dia(con, _dia(con), AHORA_BD, cfg, v3)
    assert d.veredicto is not None and d.estado_cerebro2 in ("CON_RESERVAS", "EN_OBSERVACION", "RECHAZADO", "ABSTENCION")
    assert d.estado_cerebro2 != "APROBADO" and d.veredicto.modo_experimental is True
    p = son.presentar(con, d, ZONA)
    x = p["sonadora"]
    assert x["n"] == 10 and len(x["piernas"]) == 10 and p["estado"] == "PROPUESTA" and p["promovible"] is False
    assert x["probabilidad_real"] == d.salida.sonadora.p_media and x["probabilidad_real_texto"].endswith("%")
    for leg in x["piernas"]:
        assert leg["local"] != "?" and len(leg["hora"]) == 5 and leg["jugada"] == "Más de 1.5 goles" and leg["cuota"] > 1.0
    assert x["uno_de_cada"] == pytest.approx(1 / x["probabilidad_real"])


def test_si_el_auditor_dijera_aprobado_la_decision_queda_en_con_reservas(con, cfg, v3, monkeypatch):  # noqa: F811
    fab = _fabricada(con)
    monkeypatch.setattr(son.sn, "generar_sonadoras", lambda *a, **k: fab)
    real = son.riesgo.evaluar
    monkeypatch.setattr(son.riesgo, "evaluar", lambda *a, **k: dataclasses.replace(real(*a, **k), estado="APROBADO"))
    d = son.generar_sonadoras_dia(con, _dia(con), AHORA_BD, cfg, v3)
    assert d.veredicto.estado == "APROBADO" and d.estado_cerebro2 == "CON_RESERVAS"           # el tope se aplica aunque el auditor se equivoque


def test_la_sonadora_no_se_persiste_ni_cambia_el_ticket_del_dia(con, cfg, v3, monkeypatch):  # noqa: F811
    from golmodel.integracion import benchmark as bm
    antes_aud = con.execute("SELECT count(*) AS n FROM auditoria_decisiones").fetchone()["n"]
    t0 = bm.generar_decision_v3(con, _dia(con), AHORA_BD, cfg, v3)
    fab = _fabricada(con)
    monkeypatch.setattr(son.sn, "generar_sonadoras", lambda *a, **k: fab)
    son.generar_sonadoras_dia(con, _dia(con), AHORA_BD, cfg, v3)
    t1 = bm.generar_decision_v3(con, _dia(con), AHORA_BD, cfg, v3)
    assert con.execute("SELECT count(*) AS n FROM auditoria_decisiones").fetchone()["n"] == antes_aud       # nada escrito por las soñadoras
    import json
    assert json.dumps(t0.salida.a_json(), sort_keys=True) == json.dumps(t1.salida.a_json(), sort_keys=True)  # el ticket del día, idéntico


# ------------------------------------------------------------------ API
@pytest.fixture
def cliente(con, bd, cfg, mundo):  # noqa: F811
    import pandas as pd
    from datetime import timedelta
    from fastapi.testclient import TestClient
    from golmodel import db
    from golmodel.api import main
    main._servicio = main.Servicio(cfg, cadenas(mundo, cfg), dsn=bd, reloj=lambda: pd.Timestamp(JORNADA).to_pydatetime() + timedelta(hours=5))
    yield TestClient(main.app), _dia(con).isoformat()
    main._servicio = None


def test_api_sonadoras_no_disponible_con_titulo_aviso_y_diagnostico(cliente):
    cli, dia = cliente
    j = cli.get(f"/sonadoras/{dia}").json()
    assert j["estado"] == "NO_DISPONIBLE" and j["titulo"].startswith("🚨 Soñadoras") and "juego de azar" in j["aviso"]
    assert j["diagnostico"]["cuota_maxima_alcanzable"] >= 1 and j["stake_u"] == pytest.approx(0.1)


def test_api_stake_u_valido_e_invalido(cliente):
    cli, dia = cliente
    assert cli.get(f"/sonadoras/{dia}", params={"stake_u": 0.25}).json()["stake_u"] == 0.25
    for malo in ("0", "-1", "0.9", "abc"):
        assert cli.get(f"/sonadoras/{dia}", params={"stake_u": malo}).status_code == 422


def test_api_propuesta_y_no_toca_parlays_ni_tickets(cliente, con, monkeypatch):
    cli, dia = cliente
    antes = (cli.get(f"/parlays/{dia}").json(), cli.get(f"/tickets/{dia}").json())
    fab = _fabricada(con)
    monkeypatch.setattr(son.sn, "generar_sonadoras", lambda *a, **k: fab)
    j = cli.get(f"/sonadoras/{dia}").json()
    assert j["estado"] == "PROPUESTA" and j["sonadora"]["n"] == 10 and j["estado_cerebro2"] != "APROBADO" and j["promovible"] is False
    assert (cli.get(f"/parlays/{dia}").json(), cli.get(f"/tickets/{dia}").json()) == antes


# ------------------------------------------------------------------ UI
from tests.test_ui_smoke import _ir_a_panel, _todo, entorno  # noqa: E402,F401
from streamlit.testing.v1 import AppTest  # noqa: E402
from tests.test_ui_smoke import APP  # noqa: E402

AVISO = "Esta apuesta es un juego de azar con ventaja para la casa. No forma parte de la estrategia de inversión principal."


def test_la_ui_muestra_la_seccion_con_su_titulo_y_aviso_y_el_motivo_si_no_hay(entorno):  # noqa: F811
    at = _ir_a_panel(AppTest.from_file(APP, default_timeout=90))
    assert not at.exception, [e.value for e in at.exception]
    assert "### 🚨 Soñadoras (Alto Riesgo / EV Negativo)" in [m.value for m in at.markdown]
    assert AVISO in [w.value for w in at.warning]
    t = _todo(at)
    assert "Hoy no hay soñadora" in t and "cuota máxima alcanzable" in t and "No se relajan los umbrales" in t


def test_la_ui_muestra_probabilidad_real_estado_y_que_no_es_promovible(entorno, bd, monkeypatch):  # noqa: F811
    from golmodel import db
    with db.conectar(bd) as c:
        fab = _fabricada(c)
    monkeypatch.setattr(son.sn, "generar_sonadoras", lambda *a, **k: fab)
    at = _ir_a_panel(AppTest.from_file(APP, default_timeout=90))
    assert not at.exception, [e.value for e in at.exception]
    assert AVISO in [w.value for w in at.warning]
    tarjeta = next(m.value for m in at.markdown if m.value.startswith('<div class="parlay"><h4>🎰'))
    assert "Probabilidad real:" in tarjeta and "no promovible" in tarjeta and "nunca APROBADA" in tarjeta
    assert "Stake 0.10 u" in tarjeta and "fijo simbólico (sin Kelly)" in tarjeta
    assert "Ese EV es optimista" in tarjeta and "EV según el modelo" in tarjeta


def test_la_ui_permite_un_monto_propio_y_lo_manda_a_la_api(entorno, bd, monkeypatch):  # noqa: F811
    visto = {}
    real = son.generar_sonadoras_dia

    def espia(*a, **k):
        visto["stake_u"] = k.get("stake_u")
        return real(*a, **k)
    monkeypatch.setattr(son, "generar_sonadoras_dia", espia)
    at = _ir_a_panel(AppTest.from_file(APP, default_timeout=90))
    assert visto["stake_u"] is None                                                           # por defecto: stake fijo simbólico
    next(c for c in at.checkbox if c.key == "son_usar").check().run(timeout=90)
    at.number_input(key="son_monto").set_value(0.3).run(timeout=90)
    assert not at.exception, [e.value for e in at.exception]
    assert visto["stake_u"] == pytest.approx(0.3)
