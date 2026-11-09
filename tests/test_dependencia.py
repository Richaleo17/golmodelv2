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
