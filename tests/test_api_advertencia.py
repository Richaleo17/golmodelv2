"""El endpoint /parlays/{fecha} avisa que sus parlays se generan bajo independencia (sin cambiar su forma ni su lógica)."""
import copy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from golmodel.api import main

TEXTO = "parlays generados bajo independencia; probabilidad conjunta sobreestimada. Pendiente integración de V3."
PARLAYS = [
    {"id": 1, "ranking": 1, "n_bloques": 3, "p_parlay": 0.21, "cuota_combinada": 5.1, "es_apuesta_del_dia": True,
     "bloques": [{"orden": 1, "seleccion_a": "OU:1.5:over"}]},
    {"id": 2, "ranking": 2, "n_bloques": 4, "p_parlay": 0.17, "cuota_combinada": 7.0, "es_apuesta_del_dia": False, "bloques": []},
]


@pytest.fixture
def cliente(monkeypatch):
    class _S:
        cfg = {"zona_horaria": "America/Mexico_City"}
        reloj = staticmethod(lambda: datetime(2026, 11, 9, 18, tzinfo=timezone.utc))
    main.app.dependency_overrides[main.servicio] = lambda: _S()
    main.app.dependency_overrides[main.conexion] = lambda: None
    monkeypatch.setattr(main.vistas, "parlays", lambda conn, d: copy.deepcopy(PARLAYS))
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_el_texto_de_la_advertencia_es_el_acordado():
    assert main.ADVERTENCIA_INDEPENDENCIA == TEXTO


def test_cada_parlay_lleva_la_advertencia_y_conserva_todo_lo_demas(cliente):
    r = cliente.get("/parlays/2026-11-09")
    assert r.status_code == 200
    cuerpo = r.json()
    assert isinstance(cuerpo, list) and len(cuerpo) == 2                 # la forma NO cambia: sigue siendo una lista
    for recibido, original in zip(cuerpo, PARLAYS):
        assert recibido["advertencia"] == TEXTO
        assert {k: v for k, v in recibido.items() if k != "advertencia"} == original   # nada más cambió


def test_sin_parlays_la_respuesta_sigue_siendo_una_lista_vacia(cliente, monkeypatch):
    monkeypatch.setattr(main.vistas, "parlays", lambda conn, d: [])
    assert cliente.get("/parlays/2026-11-09").json() == []               # no se envuelve: la UI itera la lista


# ------------------------------------------------------------------ /panel/{fecha}: SIN cálculo bajo independencia (retirado 2026-11-09)

PANEL = {"fecha": "2026-11-09", "mejores": [], "rompe": [], "valor": [], "v3": None}


def test_el_panel_ya_no_lleva_advertencia_de_independencia_porque_no_calcula_bajo_independencia(cliente, monkeypatch):
    monkeypatch.setattr(main.sugerencias, "panel", lambda conn, cfg, d, ahora: copy.deepcopy(PANEL))
    j = cliente.get("/panel/2026-11-09").json()
    assert "advertencia" not in j                                       # no hay cálculo bajo independencia que avisar
    assert "independencia" not in j["texto"].lower()
    assert {k: j[k] for k in PANEL} == PANEL                            # el resto del panel no cambia
    assert not {"parlays", "tickets", "mas_seguros"} & set(j)           # los bloques de V2 ya no existen


def test_texto_panel_no_menciona_independencia_ni_los_bloques_retirados():
    from golmodel.servicios import sugerencias
    txt = sugerencias.texto_panel(copy.deepcopy(PANEL))
    for retirado in ("independencia", "Parlays por perfil", "Más parlays seguros", "Tickets de pago alto"):
        assert retirado.lower() not in txt.lower(), retirado
    assert not hasattr(sugerencias, "ADVERTENCIA_INDEPENDENCIA")


def test_los_parlays_legados_de_v2_conservan_su_advertencia_y_el_ticket_v3_trae_la_suya(cliente, monkeypatch):
    mezcla = [{"fuente": "v3", "ranking": 1, "advertencia": "aviso v3"}, {"fuente": "v2", "ranking": 1}]
    monkeypatch.setattr(main.vistas, "parlays", lambda conn, d: copy.deepcopy(mezcla))
    r = cliente.get("/parlays/2026-11-09").json()
    assert r[0]["advertencia"] == "aviso v3" and r[1]["advertencia"] == TEXTO
