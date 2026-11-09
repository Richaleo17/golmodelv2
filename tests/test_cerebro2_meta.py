"""Metacalibración del auditor (job ex-post). Datos SINTÉTICOS con resultado CONOCIDO: cada métrica tiene un valor esperado
calculado a mano, de modo que una fórmula mal escrita (p. ej. dividir por el total equivocado) no pasa desapercibida.
"""
import copy

import pytest

from golmodel.cerebro2.metacalibracion import MetricasAuditor, RegistroMeta, metricas, resolver_ticket
from tests.test_tickets import _cfg

FALTA = "rechazo_por_falta_de_auditoria"
SUST = "rechazo_sustantivo"


def _r(estado, gano, cuota=None, categoria=None):
    return RegistroMeta(estado, categoria, gano, cuota)


def _conjunto():
    """Escenario con resultado conocido (ver los valores esperados abajo)."""
    return (
        [_r("RECHAZADO", g, 4.0, SUST) for g in (False, False, False, True)]      # 4 rechazos: 3 habrían perdido, 1 ganado
        + [_r("RECHAZADO", False, 4.0, FALTA)]                                    # rechazo por falta de auditoría: EXCLUIDO
        + [_r("ABSTENCION", None, None, FALTA)] * 2                               # abstenciones: EXCLUIDAS
        + [_r("CON_RESERVAS", g, 3.0) for g in (True, True, False)]               # 3 aceptados con reservas: 2 ganan
        + [_r("APROBADO", True, 2.0)]                                             # 1 aprobado que gana
        + [_r("RECHAZADO", None, 4.0, SUST)]                                      # rechazo sin resolver (partido pendiente)
        + [_r("EN_OBSERVACION", True, 5.0)]                                       # ni aceptado ni rechazado: no cuenta
    )


def test_metricas_con_resultados_conocidos():
    m = metricas(_conjunto(), _cfg())
    assert (m.n_rechazados, m.n_aceptados, m.n_excluidos_falta_auditoria, m.n_sin_resolver) == (4, 4, 3, 1)
    assert m.tasa_rechazos_correctos.p == pytest.approx(0.75)                     # 3 de 4 habrían perdido
    assert m.tasa_rechazos_correctos.k == 3 and m.tasa_rechazos_correctos.n == 4
    assert m.tasa_rechazos_incorrectos.p == pytest.approx(0.25)                   # 1 de 4 habría ganado
    assert m.tasa_rechazos_correctos.p + m.tasa_rechazos_incorrectos.p == pytest.approx(1.0)
    assert m.tasa_aprobados_correctos.p == pytest.approx(0.75)                    # 3 de 4 aceptados ganaron
    assert m.yield_rechazados == pytest.approx(0.0)                               # (-1-1-1+3)/4
    assert m.yield_aceptados == pytest.approx(1.0)                                # (2+2-1+1)/4
    assert m.valor_auditor == pytest.approx(1.0)


def test_metricas_intervalos_y_alarma():
    m = metricas(_conjunto(), _cfg())
    t = m.tasa_rechazos_correctos
    assert t.lo < t.p < t.hi and 0.0 <= t.lo and t.hi <= 1.0                      # Wilson contiene a la tasa
    lo, hi = m.yield_rechazados_ic
    assert lo <= m.yield_rechazados <= hi
    assert m.muestra_suficiente is False and m.alarma_falsos_rechazos is False    # n=4 << 200: no hay alarma
    assert metricas(_conjunto(), _cfg()).yield_rechazados_ic == m.yield_rechazados_ic   # bootstrap con semilla fija


def test_la_exclusion_de_rechazos_por_falta_de_auditoria_es_explicita():
    # Si el rechazo por falta de auditoría se contara, n_rechazados sería 5 y las tasas cambiarían.
    m = metricas([_r("RECHAZADO", False, 4.0, FALTA)] * 10 + [_r("RECHAZADO", True, 4.0, SUST)], _cfg())
    assert m.n_rechazados == 1 and m.n_excluidos_falta_auditoria == 10
    assert m.tasa_rechazos_incorrectos.p == pytest.approx(1.0) and m.tasa_rechazos_correctos.p == pytest.approx(0.0)


def test_estados_aceptados_es_configurable():
    cfg = copy.deepcopy(_cfg())
    cfg["cerebro2"]["metacalibracion"]["estados_aprobados"] = ["APROBADO"]
    m = metricas(_conjunto(), cfg)
    assert m.n_aceptados == 1 and m.tasa_aprobados_correctos.p == pytest.approx(1.0)


def test_sin_datos_las_metricas_son_none_no_cero():
    m = metricas([], _cfg())
    assert m.tasa_rechazos_correctos.p is None and m.yield_rechazados is None and m.valor_auditor is None
    assert m.alarma_falsos_rechazos is False and m.muestra_suficiente is False


def _rechazados(n_gana, n_pierde, cuota):
    return [_r("RECHAZADO", True, cuota, SUST)] * n_gana + [_r("RECHAZADO", False, cuota, SUST)] * n_pierde


def test_alarma_de_falsos_rechazos_solo_si_el_yield_es_positivo_y_significativo_con_muestra_suficiente():
    cfg = _cfg()
    sube = metricas(_rechazados(200, 50, 2.0), cfg)                               # yield +0.6, n=250
    assert sube.muestra_suficiente and sube.yield_rechazados_ic[0] > 0 and sube.alarma_falsos_rechazos is True
    neutro = metricas(_rechazados(125, 125, 2.0), cfg)                            # yield 0.0: no es significativo
    assert neutro.alarma_falsos_rechazos is False
    pocos = metricas(_rechazados(40, 10, 2.0), cfg)                               # yield +0.6 pero n=50 < 200
    assert pocos.yield_rechazados == pytest.approx(0.6) and pocos.alarma_falsos_rechazos is False
    perdedor = metricas(_rechazados(50, 200, 2.0), cfg)                           # rechaza bien: yield negativo
    assert perdedor.yield_rechazados < 0 and perdedor.alarma_falsos_rechazos is False


def test_metricas_es_serializable():
    assert isinstance(metricas(_conjunto(), _cfg()), MetricasAuditor)
    import json
    json.dumps(metricas(_conjunto(), _cfg()).a_json())


# ------------------------------------------------------------------ resolver_ticket

PARTIDOS = {1: ("finalizado", 2, 1), 2: ("finalizado", 0, 0), 3: ("cancelado", None, None),
            4: ("programado", None, None), 5: ("finalizado", 1, 3)}


def _t(*piernas):
    return {"piernas": [{"partido_id": pid, "selecciones": list(sel)} for pid, sel in piernas]}


@pytest.mark.parametrize("resumen,esperado", [
    (_t((1, ("1X2:1",)), (2, ("OU:1.5:under",))), True),                           # 2-1 gana el local; 0-0 es under 1.5
    (_t((1, ("1X2:1",)), (2, ("OU:1.5:over",))), False),                           # 0-0 NO es over 1.5
    (_t((1, ("1X2:1", "OU:2.5:over"))), True),                                     # SGP: 2-1 -> gana y suma 3 > 2.5
    (_t((1, ("1X2:1", "OU:2.5:under"))), False),                                   # SGP con una selección fallada
    (_t((5, ("1X2:2",)), (5, ("BTTS:si",))), True),                                # 1-3: gana el visitante y ambos anotan
    (_t((1, ("1X2:1",)), (3, ("1X2:1",))), None),                                  # una pierna cancelada: se anula
    (_t((1, ("1X2:1",)), (4, ("1X2:1",))), None),                                  # una pierna pendiente
    (_t((1, ("1X2:1",)), (99, ("1X2:1",))), None),                                 # partido desconocido
    (_t((1, ("AH:local:+1.0",))), None),                                           # push: no se resuelve en v1
    (_t((1, ("SELECCION_FANTASMA",))), None),
    ({"piernas": []}, None), (None, None),
])
def test_resolver_ticket(resumen, esperado):
    assert resolver_ticket(resumen, PARTIDOS) is esperado
