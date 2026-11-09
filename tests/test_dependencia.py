"""Pruebas de la probabilidad conjunta (motor/dependencia.py).

Invariante duro (pre-registro §2.6): con σ=τ=0 la conjunta se reduce EXACTAMENTE al
producto ingenuo. Negativo obligatorio: con piernas correlacionadas el producto por
independencia (`math.prod`) es incorrecto (lift ≠ 1).
"""
import numpy as np
import pytest

from golmodel.modelos.incertidumbre import FactorSamplerJerarquico
from golmodel.motor import dependencia as dep

CFG = {"dependencia": {"n_draws": 4000, "percentil_conservador": 0.25, "ic": [0.025, 0.975]}}


def _pierna(liga, sels, tau=0.0, lh=1.4, la=1.1):
    return dep.Pierna(partido_id=id(sels) % 10000, liga=liga, lambda_local=lh,
                      lambda_visitante=la, rho=0.0, selecciones=tuple(sels),
                      tau_local=tau, tau_visit=tau)


def test_reduce_a_independencia_invariante():
    # σ=0 y τ=0 → la conjunta ES el producto ingenuo (invariante §2.6).
    piernas = [_pierna("EPL", ("OU:2.5:over",)), _pierna("MX1", ("1X2:1",))]
    sampler = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga=0.0)
    r = dep.probabilidad_conjunta(piernas, sampler, CFG, semilla=1)
    assert r.p_media == pytest.approx(r.p_independencia, rel=1e-12)
    assert r.lift == pytest.approx(1.0, rel=1e-9)
    assert r.metodo == "independencia"


def test_negativo_independencia_incorrecta_con_correlacion():
    # Dos piernas Over de la MISMA liga con σ_liga>0 → correlación positiva → lift ≠ 1.
    # El producto por independencia sería incorrecto; esto es lo que dependencia corrige.
    piernas = [_pierna("EPL", ("OU:2.5:over",)), _pierna("EPL", ("OU:2.5:over",))]
    sampler = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga={"EPL": 0.25})
    r = dep.probabilidad_conjunta(piernas, sampler, CFG, semilla=2)
    assert r.metodo == "montecarlo_jerarquico"
    assert abs(r.lift - 1.0) > 0.01                        # se separa materialmente del ingenuo


def test_conservadora_y_rango():
    piernas = [_pierna("EPL", ("OU:2.5:over",), tau=0.1),
               _pierna("EPL", ("1X2:1",), tau=0.1)]
    sampler = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga={"EPL": 0.2})
    r = dep.probabilidad_conjunta(piernas, sampler, CFG, semilla=3)
    assert 0.0 <= r.p_conservadora <= r.p_media <= 1.0
    assert r.ic[0] <= r.p_media <= r.ic[1]
    assert r.error_mc >= 0.0


def test_reproducibilidad_misma_semilla():
    piernas = [_pierna("EPL", ("OU:2.5:over",), tau=0.1), _pierna("SA", ("1X2:1",), tau=0.1)]
    s = FactorSamplerJerarquico(sigma_g=0.05, sigma_liga={"EPL": 0.2, "SA": 0.15})
    r1 = dep.probabilidad_conjunta(piernas, s, CFG, semilla=7)
    r2 = dep.probabilidad_conjunta(piernas, s, CFG, semilla=7)
    assert (r1.p_media, r1.p_conservadora, r1.p_independencia, r1.lift) == \
           (r2.p_media, r2.p_conservadora, r2.p_independencia, r2.lift)


def test_independencia_se_conserva_y_se_reporta():
    # p_independencia siempre presente (§10.1-F): se muestra el número ingenuo aparte.
    piernas = [_pierna("EPL", ("OU:2.5:over",)), _pierna("MX1", ("1X2:1",))]
    s = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga={"EPL": 0.2})
    r = dep.probabilidad_conjunta(piernas, s, CFG, semilla=4)
    assert 0.0 < r.p_independencia <= 1.0 and r.n_draws == 4000 and r.semilla == 4


def test_pierna_sgp_dos_selecciones():
    # Pierna SGP (2 selecciones del mismo partido): conjunta por intersección de celdas.
    piernas = [_pierna("EPL", ("1X2:1", "OU:2.5:over"))]
    s = FactorSamplerJerarquico()
    r = dep.probabilidad_conjunta(piernas, s, CFG, semilla=5)
    assert 0.0 < r.p_media <= 1.0


def test_calibracion_estimador_vs_analitico():
    """MIDE la dependencia, no solo la detecta: caso con verdad analítica conocida.

    Dos piernas idénticas (misma λ, misma liga, mismo evento) con único factor aleatorio
    `u ~ lognormal(0, σ)`. La conjunta verdadera es E_u[p(λu)^2], integrable por cuadratura.
    El MC debe caer dentro de ~4·error_mc de ese valor con n_draws grande.
    """
    from scipy.integrate import quad
    from scipy.stats import norm

    from golmodel.modelos.matriz import matriz_marcadores
    from golmodel.modelos.mercados import probabilidad

    sigma, lh, la, sel = 0.2, 1.4, 1.1, ("OU:2.5:over",)

    def integrand(x):
        u = np.exp(x)
        p = probabilidad(matriz_marcadores(lh * u, la * u, 0.0), *sel)
        return (p * p) * norm.pdf(x, 0.0, sigma)

    verdad, _ = quad(integrand, -8 * sigma, 8 * sigma)

    piernas = [_pierna("EPL", sel, lh=lh, la=la), _pierna("EPL", sel, lh=lh, la=la)]
    sampler = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga={"EPL": sigma})
    cfg = {"dependencia": {"n_draws": 60000, "percentil_conservador": 0.25, "ic": [0.025, 0.975]}}
    r = dep.probabilidad_conjunta(piernas, sampler, cfg, semilla=11)

    assert abs(r.p_media - verdad) <= 4.0 * r.error_mc          # cuantifica bien (dentro del IC MC)
    assert abs(r.p_media - r.p_independencia) > 2.0 * r.error_mc  # y la dependencia es real, no ruido


# --- vectorización: equivalencia, presupuesto, memoria, clip -------------------

@pytest.mark.slow
def test_equivalencia_vectorizado_vs_bucle_referencia():
    # Misma semilla/parámetros: p_media vectorizado dentro de ~2·error_mc del bucle de referencia.
    piernas = [_pierna("EPL", ("OU:2.5:over",), tau=0.15), _pierna("MX1", ("1X2:1",), tau=0.15)]
    s = FactorSamplerJerarquico(sigma_g=0.05, sigma_liga={"EPL": 0.2, "MX1": 0.1})
    cfg = {"dependencia": {"n_draws": 4000, "percentil_conservador": 0.25, "ic": [0.025, 0.975]}}
    rv = dep.probabilidad_conjunta(piernas, s, cfg, 7)
    rr = dep._probabilidad_conjunta_referencia(piernas, s, cfg, 7)
    assert abs(rv.p_media - rr.p_media) <= 2.0 * rv.error_mc + 1e-12


def _ms(fn):
    import time
    t = time.perf_counter(); fn(); return (time.perf_counter() - t) * 1000.0


def test_presupuesto_tiempo():
    # Budget pre-registrado (§2.7): 121 celdas × 20k draws × 6 piernas ≤ 50 ms/cesta.
    piernas = [_pierna("EPL", ("OU:2.5:over",), tau=0.15) for _ in range(6)]
    s = FactorSamplerJerarquico(sigma_g=0.05, sigma_liga={"EPL": 0.2})
    cfg = {"dependencia": {"n_draws": 20000, "percentil_conservador": 0.25, "ic": [0.025, 0.975]}}
    dep.probabilidad_conjunta(piernas, s, cfg, 0)  # warmup
    mejor = min(_ms(lambda: dep.probabilidad_conjunta(piernas, s, cfg, k)) for k in range(5))
    assert mejor <= 50.0


def test_presupuesto_memoria():
    import tracemalloc
    piernas = [_pierna("EPL", ("OU:2.5:over",), tau=0.15) for _ in range(6)]
    s = FactorSamplerJerarquico(sigma_g=0.05, sigma_liga={"EPL": 0.2})
    cfg = {"dependencia": {"n_draws": 20000, "percentil_conservador": 0.25, "ic": [0.025, 0.975]}}
    tracemalloc.start()
    dep.probabilidad_conjunta(piernas, s, cfg, 1)
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert peak <= 50e6                                         # ≤ 50 MB (§2.7)


def test_clip_lambda_tau_grande_sin_nan():
    # τ enorme empuja λ a valores extremos; el clip [0.02,12] evita el NaN de la matriz.
    piernas = [_pierna("EPL", ("OU:2.5:over",), tau=3.0)]
    s = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga={"EPL": 1.0})
    r = dep.probabilidad_conjunta(piernas, s, CFG, 1)
    assert np.isfinite(r.p_media) and 0.0 <= r.p_media <= 1.0


# --- camino cacheable (usado por tickets.py): factores/ruido deterministas por canal --------------

def _pierna_id(pid, liga, sel, lh=1.4, la=1.1, tau=0.1):
    return dep.Pierna(pid, liga, lh, la, 0.0, tuple(sel), tau, tau)


def test_camino_cacheable_reduce_a_independencia():
    s = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga=0.0)
    f = dep.factores_compartidos(s, ["EPL", "MX1"], 2000, 1)
    ps = [dep.Pierna(1, "EPL", 1.4, 1.1, 0.0, ("OU:2.5:over",)), dep.Pierna(2, "MX1", 1.3, 1.2, 0.0, ("1X2:1",))]
    pares = [dep.p_pierna_sorteos(p, f, 1) for p in ps]
    r = dep.resumir_conjunta([a for a, _ in pares], [i for _, i in pares], CFG, 1)
    assert r.p_media == pytest.approx(r.p_independencia, rel=1e-12) and r.metodo == "independencia"


def test_factores_no_dependen_de_la_composicion_de_la_cesta():
    # Condición de validez de la caché: u[liga] y g idénticos sea cual sea el conjunto de ligas pedido.
    s = FactorSamplerJerarquico(sigma_g=0.05, sigma_liga={"EPL": 0.2, "SA": 0.1, "MX1": 0.15})
    a = dep.factores_compartidos(s, ["EPL", "SA", "MX1"], 500, 3)
    b = dep.factores_compartidos(s, ["SA"], 500, 3)
    assert np.array_equal(a.u["SA"], b.u["SA"]) and np.array_equal(a.g, b.g)


def test_p_pierna_sorteos_determinista():
    s = FactorSamplerJerarquico(sigma_g=0.05, sigma_liga={"EPL": 0.2})
    f = dep.factores_compartidos(s, ["EPL"], 1000, 4)
    p = _pierna_id(7, "EPL", ("OU:2.5:over",))
    (a1, i1), (a2, i2) = dep.p_pierna_sorteos(p, f, 4), dep.p_pierna_sorteos(p, f, 4)
    assert np.array_equal(a1, a2) and i1 == i2


def test_camino_cacheable_coincide_con_probabilidad_conjunta():
    # Flujos de aleatorios distintos pero MISMA distribución: p_media coincide dentro de ~4·SE combinado.
    piernas = [_pierna_id(1, "EPL", ("OU:2.5:over",)), _pierna_id(2, "EPL", ("1X2:1",)), _pierna_id(3, "SA", ("OU:2.5:over",))]
    s = FactorSamplerJerarquico(sigma_g=0.0, sigma_liga={"EPL": 0.2, "SA": 0.15})
    cfg = {"dependencia": {"n_draws": 30000, "percentil_conservador": 0.25, "ic": [0.025, 0.975]}}
    r1 = dep.probabilidad_conjunta(piernas, s, cfg, 5)
    f = dep.factores_compartidos(s, ["EPL", "SA"], 30000, 5)
    pares = [dep.p_pierna_sorteos(p, f, 5) for p in piernas]
    r2 = dep.resumir_conjunta([a for a, _ in pares], [i for _, i in pares], cfg, 5)
    assert abs(r1.p_media - r2.p_media) <= 4.0 * np.hypot(r1.error_mc, r2.error_mc)
    assert r1.p_independencia == pytest.approx(r2.p_independencia, rel=1e-12)
