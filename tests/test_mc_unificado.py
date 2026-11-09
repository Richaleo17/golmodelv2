"""Unificación de las rutas de Monte Carlo (deuda 2026-11-09): `calibracion_tickets` y `tickets.py` calibran y predicen sobre la
MISMA distribución, idéntica bit a bit con los mismos inputs y semilla (no "dentro del error de Monte Carlo")."""
from __future__ import annotations

import dataclasses
import inspect

import numpy as np
import pytest

from golmodel.backtest import calibracion_tickets as ct
from golmodel.backtest.calibracion_tickets import CestaHistorica, PiernaHistorica
from golmodel.modelos import incertidumbre as inc
from golmodel.motor import dependencia as dep
from golmodel.motor import tickets as tk
from tests.test_tickets import AHORA, _cfg, _pool, _version

CAMPOS = [f.name for f in dataclasses.fields(dep.ResultadoConjunta)]


def _cesta_de(ticket, pool, *, ids=True):
    por = {(c.partido_id, tuple(c.selecciones)): c for c in pool}
    ps = []
    for pt in ticket.piernas:                                       # EN EL ORDEN DE LAS PIERNAS DEL TICKET
        c = por[(pt.partido_id, tuple(pt.selecciones))]
        ps.append(PiernaHistorica(c.liga, tuple(c.calibrada.segmento), c.lambda_local, c.lambda_visitante, c.rho,
                                  tuple(c.selecciones), c.peso_efectivo_local, c.peso_efectivo_visitante,
                                  partido_id=c.partido_id if ids else None))
    return CestaHistorica(ps, True)


def _cfg_calibracion(cfg, ver):
    return {"calibracion_tickets": {"n_draws": int(cfg["dependencia"]["n_draws"]), "q": float(cfg["dependencia"]["percentil_conservador"]),
                                    "sigma_g": ver.sigma_g, "sigma_liga": ver.sigma_liga}}


def _identico(res: dep.ResultadoConjunta, t: tk.Ticket):
    """Todos los campos, con == (bit a bit), no approx."""
    assert res.p_media == t.p_media
    assert res.p_conservadora == t.p_conservadora
    assert res.p_independencia == t.p_independencia
    assert res.ic == t.ic
    assert res.lift == t.lift
    assert res.error_mc == t.error_mc
    assert res.metodo == t.metodo
    assert (res.semilla, res.n_draws) == (t.semilla, t.n_draws)


@pytest.mark.parametrize("sigma_g,sigma_liga", [(0.0, 0.1), (0.05, {"EPL": 0.2, "MX1": 0.1, "SA": 0.15}), (0.0, 0.0)])
@pytest.mark.parametrize("semilla", [11, 12345])
def test_calibracion_y_tickets_dan_el_mismo_resultado_bit_a_bit(sigma_g, sigma_liga, semilla):
    cfg, pool = _cfg(), _pool()
    ver = _version(c=0.7, sigma_liga=sigma_liga, sigma_g=sigma_g)
    salida = tk.generar_tickets(pool, ver, cfg, modo_experimental=True, semilla=semilla, ahora=AHORA)
    assert salida.por_tamano, "el escenario debe producir tickets para comparar"
    for t in salida.por_tamano.values():                                 # un ticket de cada tamaño factible
        cesta = _cesta_de(t, pool)
        res = ct._resultados([cesta], ver.c_global, _cfg_calibracion(cfg, ver), semilla)[0]   # índice 0 => misma semilla
        _identico(res, t)


def test_el_orden_de_las_piernas_es_el_del_ticket_y_otro_orden_puede_diferir_solo_en_el_ultimo_bit():
    """El producto de floats no es asociativo: la igualdad exacta es para el MISMO orden de piernas. Otro orden coincide hasta ruido numérico."""
    cfg, pool, ver = _cfg(), _pool(), _version()
    t = tk.generar_tickets(pool, ver, cfg, modo_experimental=True, semilla=11, ahora=AHORA).ticket
    c = _cesta_de(t, pool)
    inv = CestaHistorica(list(reversed(c.piernas)), True)
    a = ct._resultados([c], ver.c_global, _cfg_calibracion(cfg, ver), 11)[0]
    b = ct._resultados([inv], ver.c_global, _cfg_calibracion(cfg, ver), 11)[0]
    assert a.p_media == pytest.approx(b.p_media, rel=1e-12) and a.p_conservadora == pytest.approx(b.p_conservadora, rel=1e-12)


def test_el_partido_id_fija_el_ruido_con_otro_id_la_distribucion_cambia():
    cfg, pool, ver = _cfg(), _pool(), _version()
    t = tk.generar_tickets(pool, ver, cfg, modo_experimental=True, semilla=11, ahora=AHORA).ticket
    con_ids = ct._resultados([_cesta_de(t, pool)], ver.c_global, _cfg_calibracion(cfg, ver), 11)[0]
    sin_ids = ct._resultados([_cesta_de(t, pool, ids=False)], ver.c_global, _cfg_calibracion(cfg, ver), 11)[0]
    assert con_ids.p_media != sin_ids.p_media                            # el id importa (otro flujo de ruido)...
    assert con_ids.p_media == pytest.approx(sin_ids.p_media, abs=5 * max(con_ids.error_mc, sin_ids.error_mc))   # ...pero no el nivel


def test_valores_dorados_de_la_ruta_de_tickets_no_cambiaron_con_la_unificacion():
    """Capturados con el código ANTERIOR a la unificación (HEAD af5e29e): la ruta de tickets no debe moverse ni un bit."""
    t = tk.generar_tickets(_pool(), _version(), _cfg(), modo_experimental=True, semilla=11, ahora=AHORA).ticket
    assert (t.p_media, t.p_conservadora, t.p_independencia) == (0.3408726577068025, 0.29887761724372924, 0.34427851137144955)
    assert t.n == 6


# ------------------------------------------------------------------ una sola fuente de aleatoriedad

def test_probabilidad_conjunta_no_genera_aleatorios_propios():
    src = inspect.getsource(dep.probabilidad_conjunta)
    for prohibido in ("default_rng", "_rng(", ".normal(", ".muestrear(", "SeedSequence", "random"):
        assert prohibido not in src, prohibido
    assert "factores_compartidos(" in src and "p_pierna_sorteos(" in src and "resumir_conjunta(" in src


def test_ambas_rutas_pasan_por_las_mismas_primitivas(monkeypatch):
    cfg, pool, ver = _cfg(), _pool(), _version()
    llamadas = {"ruta": None, "n": {}}
    for nombre in ("factores_compartidos", "p_pierna_sorteos", "resumir_conjunta", "ruido_idiosincratico"):
        real = getattr(dep, nombre)

        def espia(*a, _real=real, _n=nombre, **k):
            llamadas["n"].setdefault(llamadas["ruta"], {}).setdefault(_n, 0)
            llamadas["n"][llamadas["ruta"]][_n] += 1
            return _real(*a, **k)
        monkeypatch.setattr(dep, nombre, espia)
    llamadas["ruta"] = "tickets"
    t = tk.generar_tickets(pool, ver, cfg, modo_experimental=True, semilla=11, ahora=AHORA).ticket
    llamadas["ruta"] = "calibracion"
    ct._resultados([_cesta_de(t, pool)], ver.c_global, _cfg_calibracion(cfg, ver), 11)
    for ruta in ("tickets", "calibracion"):
        assert set(llamadas["n"][ruta]) == {"factores_compartidos", "p_pierna_sorteos", "resumir_conjunta", "ruido_idiosincratico"}, ruta
    assert llamadas["n"]["calibracion"]["p_pierna_sorteos"] == t.n         # una vez por pierna


def test_ruido_idiosincratico_es_determinista_y_depende_solo_de_semilla_y_partido():
    p = dep.Pierna(7, "EPL", 1.4, 1.1, 0.0, ("OU:2.5:over",), 0.2, 0.3)
    a, b = dep.ruido_idiosincratico(p, 500, 3), dep.ruido_idiosincratico(p, 500, 3)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    otra_liga = dataclasses.replace(p, liga="MX1", lambda_local=2.0, rho=0.1)          # liga/λ/ρ no entran al flujo
    assert np.array_equal(dep.ruido_idiosincratico(otra_liga, 500, 3)[0], a[0])
    assert not np.array_equal(dep.ruido_idiosincratico(dataclasses.replace(p, partido_id=8), 500, 3)[0], a[0])
    assert not np.array_equal(dep.ruido_idiosincratico(p, 500, 4)[0], a[0])


# ------------------------------------------------------------------ contrato de entrada

def test_partido_id_repetido_en_una_cesta_falla_en_voz_alta():
    s = inc.FactorSamplerJerarquico(sigma_g=0.0, sigma_liga=0.1)
    ps = [dep.Pierna(1, "EPL", 1.4, 1.1, 0.0, ("OU:2.5:over",), 0.1, 0.1), dep.Pierna(1, "EPL", 1.4, 1.1, 0.0, ("1X2:1",), 0.1, 0.1)]
    with pytest.raises(ValueError, match="partido_id repetido"):
        dep.probabilidad_conjunta(ps, s, _cfg(), 1)


def test_cesta_vacia_falla_en_voz_alta():
    with pytest.raises(ValueError, match="al menos una pierna"):
        dep.probabilidad_conjunta([], inc.FactorSamplerJerarquico(sigma_g=0.0, sigma_liga=0.0), _cfg(), 1)


def test_piernas_sin_id_reciben_ids_posicionales_distintos_y_la_cesta_se_calcula():
    cfg, pool, ver = _cfg(), _pool(), _version()
    t = tk.generar_tickets(pool, ver, cfg, modo_experimental=True, semilla=11, ahora=AHORA).ticket
    r = ct._resultados([_cesta_de(t, pool, ids=False)], ver.c_global, _cfg_calibracion(cfg, ver), 11)[0]
    assert 0.0 < r.p_conservadora < r.p_media < 1.0

