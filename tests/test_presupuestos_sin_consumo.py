"""Regresión del 500 en /costo y /actualizar: `sincronizar_presupuestos` hacía int(None) cuando no había consumo de API hoy (p. ej. al cambiar el día UTC).

La consulta `repo.consumo_actual` devuelve NULL para hoy/mes sin llamadas; el valor debe tomarse como 0 tanto para presupuestos diarios como mensuales."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from golmodel.api import main
from golmodel import repositorio as repo


def _servicio(presupuestos):
    s = object.__new__(main.Servicio)                       # sin construir proveedores ni BD
    s.presupuestos = presupuestos
    return s


@pytest.mark.parametrize("periodo", ["dia", "mes"])
@pytest.mark.parametrize("hoy,mes,esperado_dia,esperado_mes", [(None, None, 0, 0), (None, 7, 0, 7), (3, None, 3, 0), (3, 9, 3, 9), (0, 0, 0, 0)])
def test_sin_consumo_el_usado_es_cero_y_no_revienta(monkeypatch, periodo, hoy, mes, esperado_dia, esperado_mes):
    monkeypatch.setattr(repo, "consumo_actual", lambda conn: {"odds_api": {"hoy_utc": hoy, "mes_utc": mes, "restante_reportado": None}})
    p = SimpleNamespace(periodo=periodo, usado=-1, restante_reportado="x")
    _servicio({"odds_api": p}).sincronizar_presupuestos(object())
    assert p.usado == (esperado_dia if periodo == "dia" else esperado_mes) and p.restante_reportado is None


def test_un_proveedor_sin_presupuesto_se_ignora(monkeypatch):
    monkeypatch.setattr(repo, "consumo_actual", lambda conn: {"otro": {"hoy_utc": None, "mes_utc": None, "restante_reportado": None}})
    _servicio({}).sincronizar_presupuestos(object())             # no lanza
