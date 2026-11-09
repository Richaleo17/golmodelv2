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
