"""Casas del histórico de football-data y su consenso en Cerebro 2.

El CSV sintético refleja la estructura documentada en `notes.txt` (1X2 para B365/BW/IW/LB/PS/WH/VC; over/under 2.5 solo para
B365 y Pinnacle, esta última como `P>2.5`). Es una SUPOSICIÓN de este test sobre la fuente, no una lectura de un CSV real
(el proxy de la sesión bloquea football-data.co.uk): la corrección final se verifica reingiriendo los datos reales.
"""
from __future__ import annotations

import pytest
import yaml

from golmodel.config import RUTA_V3
from golmodel.ingesta.limpieza import limpiar
from golmodel.ingesta.proveedores import football_data_csv as fdc

CASAS_REALES = {"pinnacle", "bet365", "betway", "interwetten", "ladbrokes", "williamhill", "vcbet"}
PREFIJOS = {"PS": "pinnacle", "B365": "bet365", "BW": "betway", "IW": "interwetten", "LB": "ladbrokes", "WH": "williamhill",
            "VC": "vcbet"}
# 1X2 pre-partido por casa (márgenes distintos, para que el devig tenga qué quitar)
ODDS = {"PS": (2.10, 3.50, 3.60), "B365": (2.05, 3.40, 3.55), "BW": (2.00, 3.40, 3.50), "IW": (1.95, 3.30, 3.45),
        "LB": (2.05, 3.30, 3.50), "WH": (2.00, 3.40, 3.60), "VC": (2.08, 3.45, 3.55)}


def _csv(prefijos=tuple(PREFIJOS), ou=("B365", "P"), cierre=True, n=2):
    cols = ["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    for p in prefijos:
        cols += [f"{p}H", f"{p}D", f"{p}A"] + ([f"{p}CH", f"{p}CD", f"{p}CA"] if cierre else [])
    for p in ou:
        cols += [f"{p}>2.5", f"{p}<2.5"]
    filas = []
    for k in range(n):
        v = ["E0", f"0{8 + k}/11/2026", "15:00", f"Local{k}", f"Visita{k}", "2", "1"]
        for p in prefijos:
            h, d, a = ODDS[p]
            v += [h, d, a] + ([h - 0.05, d, a + 0.05] if cierre else [])
        for p in ou:
            v += [1.90, 2.00]
        filas.append(",".join(map(str, v)))
    return ",".join(cols) + "\n" + "\n".join(filas) + "\n"


def _cuotas(texto, cierre):
    return fdc.cuotas_desde_df(fdc.leer_csv(texto, "EPL"), cierre=cierre)


def test_catalogo_de_casas_del_cargador_es_el_acordado():
    assert dict(fdc.CASAS) == {**PREFIJOS, "Avg": "promedio"}
    assert "Max" not in dict(fdc.CASAS) and "marathon" not in dict(fdc.CASAS).values()      # agregados/otras: no entran como casa real


@pytest.mark.parametrize("cierre", [False, True])
def test_se_cargan_las_siete_casas_con_su_1x2_y_el_nombre_normalizado(cierre):
    cu = _cuotas(_csv(), cierre)
    assert {c.casa for c in cu} == CASAS_REALES
    for pref, casa in PREFIJOS.items():
        h, d, a = ODDS[pref]
        if cierre:
            h, a = h - 0.05, a + 0.05
        mias = {c.seleccion: c.cuota for c in cu if c.casa == casa and c.seleccion.startswith("1X2") and c.inicio.day == 8}
        assert set(mias) == {"1X2:1", "1X2:X", "1X2:2"}, casa
        assert (mias["1X2:1"], mias["1X2:X"], mias["1X2:2"]) == pytest.approx((h, d, a)), casa


def test_over_under_solo_para_las_casas_que_traen_esas_columnas():
    cu = _cuotas(_csv(), False)
    con_ou = {c.casa for c in cu if c.seleccion.startswith("OU:")}
    assert con_ou == {"bet365", "pinnacle"}                                        # el resto no inventa un over/under
    assert all(c.cuota > 1.0 for c in cu)


def test_columnas_ausentes_se_ignoran_y_las_casas_sin_columnas_no_aparecen():
    assert {c.casa for c in _cuotas(_csv(prefijos=("PS", "B365"), ou=()), False)} == {"pinnacle", "bet365"}
    assert {c.casa for c in _cuotas(_csv(prefijos=("PS", "WH", "BW"), ou=()), False)} == {"pinnacle", "williamhill", "betway"}
    assert _cuotas(_csv(prefijos=("PS",), ou=()), False)                            # un CSV mínimo sigue funcionando


def test_celdas_vacias_o_con_texto_cuentan_como_sin_cuota_por_casa():
    t = _csv(prefijos=("PS", "WH"), ou=(), cierre=False).splitlines()
    cabecera = t[0].split(",")
    fila = t[1].split(",")
    fila[cabecera.index("WHH")] = "x"                                                # texto
    fila[cabecera.index("WHD")] = ""                                                 # vacío
    cu = fdc.cuotas_desde_df(fdc.leer_csv("\n".join([t[0], ",".join(fila)]) + "\n", "EPL"), cierre=False)
    assert {c.seleccion for c in cu if c.casa == "williamhill"} == {"1X2:2"}         # solo la celda válida
    assert {c.seleccion for c in cu if c.casa == "pinnacle"} == {"1X2:1", "1X2:X", "1X2:2"}


def test_la_limpieza_acepta_las_casas_nuevas_y_descarta_solo_lo_invalido():
    partidos, cuotas, rep = limpiar(_csv(), "EPL")
    assert len(partidos) == 2 and set(cuotas["casa"]) == CASAS_REALES | set()
    assert cuotas.groupby("casa")["seleccion"].nunique()["williamhill"] == 3        # solo 1X2


def test_las_casas_historicas_comparten_clave_con_the_odds_api_donde_coinciden():
    """`williamhill` es la clave de The Odds API Y la del histórico: el mismo mercado en vivo y en el backtest."""
    assert "williamhill" in CASAS_REALES and fdc.CASAS[5] == ("WH", "williamhill")


# ------------------------------------------------------------------ configuración de casas elegibles

def _elegibles():
    return yaml.safe_load(RUTA_V3.read_text(encoding="utf-8"))["cerebro2"]["consenso_mercado"]["casas_elegibles"]


def test_casas_elegibles_es_la_lista_acordada():
    assert _elegibles() == ["pinnacle", "bet365", "williamhill", "marathonbet", "nordicbet", "tipico_de", "onexbet",
                            "unibet_se", "leovegas_se", "betway", "interwetten", "ladbrokes", "vcbet"]


def test_casas_elegibles_sin_la_clave_inexistente_ni_agregados_y_con_bet365():
    e = _elegibles()
    assert "marathon" not in e and "marathonbet" in e                                # la clave real de The Odds API
    assert "bet365" in e                                                             # la etiqueta 'agregado' del seed no la excluye
    assert not {"promedio", "maximo", "manual"} & set(e)                             # agregados y captura manual: nunca
    assert len(e) == len(set(e)) >= 3


def test_todas_las_casas_reales_del_historico_son_elegibles_y_ningun_agregado():
    """Decisión 2026-11-09: las 7 casas reales de football-data son elegibles (robustez: ausencias de 1-2 casas no tumban el consenso)."""
    elegibles = set(_elegibles())
    assert CASAS_REALES <= elegibles
    assert not {"promedio", "maximo", "manual"} & elegibles
    assert len(elegibles & CASAS_REALES) == 7 >= 3 + 2                              # sobran >= 2 casas sobre el mínimo n_min = 3
