"""Smoke test de la UI Streamlit con `streamlit.testing.v1.AppTest`: la UI REAL habla con la API REAL (FastAPI TestClient) sobre
PostgreSQL REAL. Verifica que el ticket V3 y /tickets/{fecha} son accesibles desde la pantalla "Parlay con cuotas" y que una
abstención se muestra como abstención (no como un parlay inventado).

Limitación honesta: AppTest ejecuta el script y expone sus elementos; NO es un navegador (no mide estilo, layout ni CSS).
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from golmodel import db  # noqa: E402
from golmodel.integracion import benchmark as bm  # noqa: E402
from tests.test_integracion import JORNADA, cadenas, cargado, cfg, dsn, mundo  # noqa: E402,F401
from tests.test_integracion_benchmark import _ticket_con_partidos_reales, bd  # noqa: E402,F401
from tests.test_integracion_candidatas import _dia, poblada  # noqa: E402,F401

APP = str(Path(__file__).resolve().parents[1] / "ui" / "app.py")


@pytest.fixture
def entorno(bd, cfg, mundo, monkeypatch):  # noqa: F811
    """API real (TestClient) conectada a la BD de prueba; httpx.request de la UI se enruta a ella."""
    import httpx
    from fastapi.testclient import TestClient
    from golmodel.api import main
    momento = pd.Timestamp(JORNADA).to_pydatetime() + timedelta(hours=5)
    main._servicio = main.Servicio(cfg, cadenas(mundo, cfg), dsn=bd, reloj=lambda: momento)
    cli = TestClient(main.app)
    monkeypatch.setattr(httpx, "request", lambda metodo, url, **kw: cli.request(metodo, url.replace("http://localhost:8000", ""),
                                                                               **{k: v for k, v in kw.items() if k != "timeout"}))
    monkeypatch.setenv("GOLMODEL_FECHA", pd.Timestamp(JORNADA).tz_convert("America/Mexico_City").date().isoformat())
    for v in ("APP_PASSWORD", "APP_VIEWER_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("UI_BIND", "127.0.0.1")
    yield bd
    main._servicio = None


def _ir_a_parlay(at: AppTest) -> AppTest:
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    nav = next(r for r in at.radio if r.key == "nav")
    at = nav.set_value(nav.options[3]).run(timeout=60)              # sección «Cuotas»
    sub = next(r for r in at.radio if r.key == "subnav")
    return sub.set_value(sub.options[0]).run(timeout=60)           # «Parlay con cuotas»


def _textos(at: AppTest) -> str:
    partes = [m.value for m in at.markdown] + [w.value for w in at.warning] + [i.value for i in at.info] + \
             [c.value for c in at.caption] + [x.label for x in at.metric]
    return "\n".join(map(str, partes))


def test_la_ui_muestra_la_abstencion_v3_con_su_motivo_y_ningun_parlay(entorno, bd):  # noqa: F811
    from tests.test_tickets import AHORA, _cfg, _version
    with db.conectar(bd) as c:                                                 # la ÚLTIMA decisión del día es una abstención
        salida, veredicto, error = bm.decidir([], {}, _version(), _cfg(), semilla=1, ahora=AHORA)
        bm.persistir(c, bm.DecisionV3(_dia(c), AHORA, None, salida, veredicto, error))
    at = _ir_a_parlay(AppTest.from_file(APP, default_timeout=60))
    assert not at.exception, [e.value for e in at.exception]
    t = _textos(at)
    assert "V3: abstención" in t and "sin_piernas_elegibles" in t               # con su motivo, nunca un parlay inventado
    assert "Ticket V3" not in t and "Prob. conservadora" not in [m.label for m in at.metric]


def test_sin_decision_v3_la_ui_lo_dice_y_no_inventa(entorno, bd, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GOLMODEL_FECHA", "2030-01-01")                         # día sin ninguna decisión
    at = _ir_a_parlay(AppTest.from_file(APP, default_timeout=60))
    assert not at.exception, [e.value for e in at.exception]
    assert "Todavía no hay decisión V3" in _textos(at) and "Ticket V3" not in _textos(at)


def test_la_ui_muestra_el_ticket_v3_con_su_aviso_y_sus_metricas(entorno, bd):  # noqa: F811
    with db.conectar(bd) as c:
        dia = _dia(c)
        d = _ticket_con_partidos_reales(c, dia)
        bm.persistir(c, d)
    at = _ir_a_parlay(AppTest.from_file(APP, default_timeout=60))
    assert not at.exception, [e.value for e in at.exception]
    t = _textos(at)
    titulos = [m.value for m in at.markdown if m.value.startswith("### 🎫 Ticket V3 ·")]
    assert len(titulos) == 1 and d.veredicto.estado in titulos[0] and f"{d.ticket.n} partidos" in titulos[0]   # el título del ticket
    assert "calibración provisional" in t.lower()
    assert any("PROVISIONAL" in w.value for w in at.warning)                   # el aviso del ticket V3 llega a pantalla
    assert "Prob. conservadora" in [m.label for m in at.metric]
    assert "Cuota casa" in [m.label for m in at.metric] and "Stake" in [m.label for m in at.metric]
    assert "NO promovido" in t                                                  # la nota de que no es apuesta real


# ------------------------------------------------------------------ pantalla «Qué meter» (panel): solo ticket V3 o abstención

ENCABEZADOS_RETIRADOS = ("Parlays sugeridos", "Más parlays seguros", "Tickets de pago alto")
TARJETAS_RETIRADAS = ("🛡️ Seguro", "⚖️ Equilibrado", "🎲 Ambicioso", "Muy seguro")


def _sin_bloques_de_v2(at: AppTest):
    """Ni encabezados, ni tarjetas, ni avisos de independencia, ni el texto copiable de los bloques retirados.
    (La nota que EXPLICA el retiro puede nombrarlos: por eso se mira estructura, no palabras sueltas.)"""
    encabezados = [m.value for m in at.markdown if m.value.lstrip().startswith("###")]
    for r in ENCABEZADOS_RETIRADOS:
        assert not any(r in h for h in encabezados), (r, encabezados)
    tarjetas = [m.value for m in at.markdown if m.value.startswith('<div class="parlay">')]
    for r in TARJETAS_RETIRADAS:
        assert not any(r in x for x in tarjetas), r
    assert not any("independencia" in w.value.lower() for w in at.warning)          # ningún aviso de independencia
    copiable = "\n".join(str(c.value) for c in at.code)
    for r in ("Parlays por perfil", "Más parlays seguros", "Tickets de pago alto", "independencia"):
        assert r.lower() not in copiable.lower().replace("si fueran independientes", ""), r


def _ir_a_panel(at: AppTest) -> AppTest:
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    nav = next(r for r in at.radio if r.key == "nav")
    return nav.set_value(nav.options[1]).run(timeout=60)             # sección «Qué meter»


def _todo(at: AppTest) -> str:
    return _textos(at) + "\n" + "\n".join(str(c.value) for c in at.code)         # (el resumen copiable va en un st.code)


def test_el_panel_muestra_el_ticket_v3_y_ningun_bloque_de_v2(entorno, bd):  # noqa: F811
    with db.conectar(bd) as c:
        bm.persistir(c, _ticket_con_partidos_reales(c, _dia(c)))
    at = _ir_a_panel(AppTest.from_file(APP, default_timeout=60))
    assert not at.exception, [e.value for e in at.exception]
    t = _todo(at)
    assert "### 🎫 Ticket V3 (5-8 partidos)" in t
    assert any(m.value.startswith('<div class="parlay"><h4>🎫 Ticket V3') for m in at.markdown)       # la tarjeta
    assert any("PROVISIONAL" in w.value for w in at.warning)
    _sin_bloques_de_v2(at)
    assert "NO promovido" in t


def test_el_panel_muestra_la_abstencion_con_su_motivo(entorno, bd):  # noqa: F811
    from tests.test_tickets import AHORA, _cfg, _version
    with db.conectar(bd) as c:
        salida, veredicto, error = bm.decidir([], {}, _version(), _cfg(), semilla=1, ahora=AHORA)
        bm.persistir(c, bm.DecisionV3(_dia(c), AHORA, None, salida, veredicto, error))
    at = _ir_a_panel(AppTest.from_file(APP, default_timeout=60))
    assert not at.exception, [e.value for e in at.exception]
    t = _todo(at)
    assert "V3: abstención" in t and "sin_piernas_elegibles" in t
    assert not any(m.value.startswith('<div class="parlay"><h4>🎫 Ticket V3') for m in at.markdown)
    _sin_bloques_de_v2(at)


def test_el_panel_sin_decision_v3_lo_dice_y_no_inventa(entorno, bd, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GOLMODEL_FECHA", "2030-01-01")
    at = _ir_a_panel(AppTest.from_file(APP, default_timeout=60))
    assert not at.exception, [e.value for e in at.exception]
    assert "Todavía no hay decisión V3" in _todo(at)
