"""Muestreo ESTRATIFICADO de cestas (enmienda v0.13 del pre-registro). Prueba `muestrear_dia` aislado, con un mundo sintético de piernas.

Qué se exige (y por qué):
* cubre el rango de probabilidad: con la config real, >= 8 estratos poblados; con el muestreo UNIFORME anterior (línea base) NO — así la prueba distingue
  la corrección del comportamiento viejo (si el estratificado no mejorara la cobertura, el test fallaría);
* cuotas iguales por estrato (con llenado por escasez) y sin cestas repetidas;
* NO filtra por aprobación: cestas de baja P y piernas que el motor no recomendaría siguen presentes (el gate juzga cestas sin sesgo de selección);
* determinista dado el rng; cada día usa su propio rng (independencia, probada en test_integracion_cestas)."""
from __future__ import annotations

import math
from collections import Counter
from types import SimpleNamespace as NS

import numpy as np
import pytest

from golmodel.config import cargar_v3
from golmodel.integracion import cestas_historicas as chs

CH = cargar_v3()["calibracion_tickets"]["cestas_historicas"]
TAMANOS = [5, 6, 7, 8]                                    # tamaños de ticket de producción (tickets.tamano_*)
UNIFORME = {**CH, "sobremuestreo": 1, "mezcla_alta_p": 0.0, "estratos_ancho": 1.0}      # línea base: el muestreo anterior (sin estratos ni sesgo)
PS = [0.88, 0.80, 0.72, 0.64, 0.55, 0.47, 0.38, 0.30, 0.22, 0.12]


def _mundo(n_partidos=20):
    info = {}
    for pid in range(1, n_partidos + 1):
        info[pid] = [(f"cuota-{pid}-{j}", NS(p_modelo=p, partido_id=pid, selecciones=(f"S{j}",))) for j, p in enumerate(PS)]
    return info, list(info)


def _muestra(ch=CH, n_dia=60, semilla=1, n_partidos=20, tamanos=TAMANOS):
    info, pids = _mundo(n_partidos)
    return chs.muestrear_dia(info, pids, np.random.default_rng(semilla), tamanos, n_dia, ch), info


def _estratos(m):
    return Counter(x[3] for x in m)


# ------------------------------------------------------------------ cobertura del rango de probabilidad
def test_el_estratificado_puebla_al_menos_8_estratos_y_el_uniforme_anterior_no():
    nuevo, _ = _muestra(CH, n_dia=200)
    viejo, _ = _muestra(UNIFORME, n_dia=200)
    assert len(_estratos(nuevo)) >= 8                                             # lo que exige la decisión 12 (>= 8 buckets)
    assert len(_estratos(viejo)) < 8                                              # control: el muestreo uniforme NO cubría el rango (por eso la enmienda)
    assert max(_estratos(viejo).values()) > 0.6 * len(viejo)                      # y concentraba la masa en pocos estratos


def test_cuotas_iguales_por_estrato_con_llenado_por_escasez():
    m, _ = _muestra(CH, n_dia=200)
    c = _estratos(m)
    assert len(m) == 200                                                          # hay candidatas de sobra: se completa el cupo del día
    grandes = [k for k in c.values() if k >= 15]
    assert len(grandes) >= 8 and max(grandes) - min(grandes) <= 2                  # los estratos con candidatas suficientes quedan con cuotas (casi) iguales
    escaso = [e for e, k in c.items() if k < 15]
    assert all(c[e] < min(grandes) for e in escaso)                                # un estrato escaso aporta lo que tiene (el resto se reparte), nunca más
    conc, _ = _muestra(UNIFORME, n_dia=200)
    assert max(c.values()) < 0.15 * len(m) < max(_estratos(conc).values())         # el mayor estrato pesa <15 % (vs. la concentración del uniforme)


def test_p_nominal_y_estrato_son_correctos_y_consistentes():
    m, _ = _muestra(CH, n_dia=80)
    n_est = math.ceil(1 / CH["estratos_ancho"])
    for piernas, n, P, e in m:
        assert n == len(piernas) and n in TAMANOS
        assert P == pytest.approx(math.prod(pm.p_modelo for _, pm in piernas), rel=1e-12)       # P nominal = producto de p_modelo
        assert e == min(int(P / CH["estratos_ancho"]), n_est - 1) and 0 <= e < n_est
        assert 0 < P <= 1


def test_el_orden_de_salida_es_estable_por_estrato():
    m, _ = _muestra(CH, n_dia=80)
    assert [x[3] for x in m] == sorted(x[3] for x in m)


# ------------------------------------------------------------------ integridad de la muestra
def test_una_pierna_por_partido_sin_repetir_partidos_ni_cestas():
    m, _ = _muestra(CH, n_dia=150)
    claves = set()
    for piernas, n, P, e in m:
        pids = [pm.partido_id for _, pm in piernas]
        assert len(set(pids)) == len(pids)                                         # una sola pierna por partido (independencia entre piernas)
        clave = tuple((pm.partido_id, pm.selecciones) for _, pm in piernas)
        assert clave not in claves
        claves.add(clave)


def test_no_filtra_por_aprobacion_incluye_cestas_de_probabilidad_baja_y_piernas_poco_probables():
    m, _ = _muestra(CH, n_dia=200)
    assert min(x[2] for x in m) < 0.01                                            # cestas muy poco probables (el gate debe verlas)
    assert max(x[2] for x in m) > 0.30                                            # y las muy probables
    p_piernas = [pm.p_modelo for x in m for _, pm in x[0]]
    assert min(p_piernas) <= 0.22 and max(p_piernas) >= 0.80                       # ninguna selección queda excluida por su probabilidad


def test_solo_usa_tamanos_que_caben_en_los_partidos_del_dia():
    m, _ = _muestra(CH, n_dia=40, n_partidos=6)
    assert {x[1] for x in m} <= {5, 6}                                             # con 6 partidos no hay cestas de 7 ni 8
    m2, _ = _muestra(CH, n_dia=40, n_partidos=5)
    assert {x[1] for x in m2} == {5}


def test_espacio_pequeno_devuelve_lo_que_hay_sin_inventar_ni_repetir():
    info = {pid: [(None, NS(p_modelo=0.5, partido_id=pid, selecciones=("A",)))] for pid in range(1, 6)}      # UNA sola cesta posible de 5
    m = chs.muestrear_dia(info, list(info), np.random.default_rng(0), [5], 50, CH)
    assert len(m) == 1 and m[0][1] == 5 and m[0][2] == pytest.approx(0.5 ** 5)


def test_estrato_sin_candidatas_queda_vacio_no_se_fuerza():
    info = {pid: [(None, NS(p_modelo=0.97, partido_id=pid, selecciones=("A",)))] for pid in range(1, 9)}     # P ∈ {.97^5..} todas en el último tramo
    m = chs.muestrear_dia(info, list(info), np.random.default_rng(0), TAMANOS, 50, CH)
    assert set(_estratos(m)) <= {15, 16, 17} and all(x[2] > 0.7 for x in m)           # solo los estratos con candidatas: los demás quedan vacíos


# ------------------------------------------------------------------ determinismo
def test_mismo_rng_misma_muestra_y_otra_semilla_otra_muestra():
    a, _ = _muestra(CH, semilla=5)
    b, _ = _muestra(CH, semilla=5)
    c, _ = _muestra(CH, semilla=6)
    clave = lambda m: [tuple((pm.partido_id, pm.selecciones) for _, pm in x[0]) for x in m]       # noqa: E731
    assert clave(a) == clave(b) and clave(a) != clave(c)


# ------------------------------------------------------------------ tamaños = los de PRODUCCIÓN
def test_los_tamanos_del_backtest_son_los_de_produccion_5_a_8_y_nunca_los_reducidos():
    cfg = cargar_v3()
    assert chs._tamanos(cfg) == [5, 6, 7, 8]                                       # tickets.tamano_alta_min..max
    assert cfg["tickets"]["tamano_reducido_min"] == 2                              # (existe, pero NO entra al backtest)
    cfg2 = {"tickets": {**cfg["tickets"], "tamano_alta_min": 6, "tamano_alta_max": 7}}
    assert chs._tamanos(cfg2) == [6, 7]                                            # sale de la config de tickets, no está duplicado


# ------------------------------------------------------------------ parámetros de la config
def test_los_parametros_provisionales_de_la_enmienda_estan_en_la_config():
    assert {"estratos_ancho", "sobremuestreo", "mezcla_alta_p", "exponente_alta_p"} <= set(CH)
    assert CH["estratos_ancho"] == 0.05                                            # = ancho de bin de la decisión 12
    assert 0 <= CH["mezcla_alta_p"] <= 1 and CH["sobremuestreo"] >= 1 and CH["exponente_alta_p"] > 0
