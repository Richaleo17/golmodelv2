"""«Soñadoras» (motor/sonadoras.py, integracion/sonadoras.py): lógica pura con candidatas construidas a mano, auditoría de Cerebro 2, API y UI.
Datos SINTÉTICOS. Una soñadora tiene EV negativo por diseño: lo que se prueba es que la ARITMÉTICA es honesta (nada se relaja para forzarla),
que usa la probabilidad conjunta REAL de dependencia.py, que nunca es APROBADA y que no toca el ticket del día."""
from __future__ import annotations

import copy
import inspect
import json
from datetime import timedelta

import numpy as np
import pytest

from golmodel.cerebro2.decision import Veredicto
from golmodel.integracion import benchmark as bm
from golmodel.integracion import sonadoras as son
from golmodel.modelos import incertidumbre as inc
from golmodel.motor import dependencia as dep
from golmodel.motor import sonadoras as sn
from golmodel.motor import tickets as tk
from tests.test_tickets import AHORA, _cand, _cfg, _version


def _pool(n=14, edge=1.30, **kw):
    """n partidos de ligas distintas con una selección muy probable (p≈0.88) y cuota = edge/p (edge>1: cuota por encima de la justa)."""
    ligas = ["EPL", "MX1", "SA", "LL", "BUN", "L1", "ERE"]
    return [_cand(100 + i, ligas[i % len(ligas)], 2.1 + 0.02 * (i % 5), 1.7, edge=edge, **kw) for i in range(n)]


def _gen(pool, cfg=None, stake=0.1, semilla=11, version=None):
    return sn.generar_sonadoras(pool, version or _version(), cfg or _cfg(), semilla=semilla, ahora=AHORA, stake_u=stake)


# ------------------------------------------------------------------ cuando SÍ hay soñadora
def test_arma_una_combinada_que_cumple_todas_las_restricciones_duras():
    cfg, pool = _cfg(), _pool()
    r = _gen(pool, cfg)
    assert r.estado == "PROPUESTA" and r.motivo is None
    s, sc = r.sonadora, cfg["sonadoras"]
    assert sc["n_min"] <= s.n <= sc["n_max"] and s.n == len(s.piernas)
    assert len({p.partido_id for p in s.piernas}) == s.n                                    # máx. 1 selección por partido
    assert all(len(p.selecciones) == 1 for p in s.piernas)
    assert s.cuota_combinada > sc["cuota_objetivo"] and s.cuota_combinada == pytest.approx(float(np.prod([p.cuota for p in s.piernas])))
    assert all(p.p_conservadora > sc["p_min_pierna"] and p.crs_total < sc["crs_max"] for p in s.piernas)


@pytest.mark.parametrize("edge", [1.20, 1.30, 1.45, 1.75])
def test_usa_el_menor_n_que_supera_el_objetivo(edge):
    """Con 15 selecciones de la MISMA cuota c, n debe ser el menor entero >= n_min con c^n > objetivo (exacto)."""
    pool = [_cand(100 + i, ["EPL", "MX1", "SA"][i % 3], 2.1, 1.7, edge=edge) for i in range(15)]
    cuota = pool[0].cuota
    assert all(c.cuota == cuota for c in pool)
    esperado = max(10, next(n for n in range(1, 99) if cuota ** n > 100.0))
    r = _gen(pool)
    if esperado > 15:
        assert r.estado == "NO_DISPONIBLE"
    else:
        assert r.estado == "PROPUESTA" and r.sonadora.n == esperado and r.sonadora.cuota_combinada > 100.0


def test_la_probabilidad_es_la_conjunta_real_de_dependencia_py_bit_a_bit():
    cfg, pool, ver = _cfg(), _pool(), _version()
    s = _gen(pool, cfg, version=ver).sonadora
    por = {c.partido_id: c for c in pool}
    piernas = [dep.Pierna(p.partido_id, p.liga, por[p.partido_id].lambda_local, por[p.partido_id].lambda_visitante, por[p.partido_id].rho,
                          tuple(p.selecciones), inc.tau_idiosincratico(ver.c_global, por[p.partido_id].peso_efectivo_local),
                          inc.tau_idiosincratico(ver.c_global, por[p.partido_id].peso_efectivo_visitante))
               for p in s.piernas]
    r = dep.probabilidad_conjunta(piernas, inc.FactorSamplerJerarquico(sigma_g=ver.sigma_g, sigma_liga=ver.sigma_liga), cfg, 11)
    assert (s.p_media, s.p_conservadora, s.p_independencia, s.ic, s.lift, s.error_mc) == (r.p_media, r.p_conservadora, r.p_independencia, r.ic, r.lift, r.error_mc)


def test_la_probabilidad_real_es_pequena_y_el_ev_es_el_calculo_directo():
    s = _gen(_pool(14, edge=1.30)).sonadora
    assert 0.0 < s.p_media < 0.5 and s.p_conservadora <= s.p_media
    assert s.ev_real == pytest.approx(s.p_media * s.cuota_combinada - 1.0) and s.ev_cons == pytest.approx(s.p_conservadora * s.cuota_combinada - 1.0)


def test_stake_fijo_no_depende_de_la_probabilidad_ni_de_la_cuota_y_no_hay_kelly():
    a = _gen(_pool(14, edge=1.30), stake=0.1).sonadora
    b = _gen(_pool(14, edge=1.60), stake=0.1).sonadora
    assert a.stake_u == b.stake_u == 0.1 and a.cuota_combinada != b.cuota_combinada and a.p_media != b.p_media
    assert _gen(_pool(14), stake=0.25).sonadora.stake_u == 0.25
    campos = set(a.__dataclass_fields__)
    assert not {c for c in campos if "kelly" in c or "crecimiento" in c}                     # ningún campo de Kelly / crecimiento log


def test_de_varias_selecciones_del_mismo_partido_toma_la_de_mayor_cuota():
    pool = [c for c in _pool(12, edge=1.30) if c.partido_id != 100]                              # 11 partidos sin el 100
    barata = _cand(100, "EPL", 2.1, 1.7, edge=1.30, sel=("OU:1.5:over",))
    cara = _cand(100, "EPL", 2.1, 1.7, edge=1.60, sel=("OU:1.5:over",))
    pool += [cara, barata]                                                                    # la barata va ÚLTIMA: "gana la última" daría la equivocada
    mismo = [c for c in pool if c.partido_id == 100]
    assert len(mismo) == 2 and cara.cuota > barata.cuota
    s = _gen(pool).sonadora
    assert len({p.partido_id for p in s.piernas}) == s.n                                       # nunca dos del mismo partido
    elegida = [p for p in s.piernas if p.partido_id == 100]
    assert len(elegida) == 1 and elegida[0].cuota == cara.cuota                                # y es la de mayor cuota


def test_es_determinista_y_no_depende_del_orden_de_las_candidatas():
    pool = _pool()
    a, b = _gen(pool), _gen(list(reversed(pool)))
    assert json.dumps(a.a_json(), sort_keys=True, default=str) == json.dumps(b.a_json(), sort_keys=True, default=str)
    assert _gen(pool, semilla=12).sonadora.p_media != a.sonadora.p_media                     # la semilla sí cambia la realización de MC


# ------------------------------------------------------------------ cuando NO hay (lo normal): aritmética honesta
def test_con_cuotas_justas_la_cuota_objetivo_es_inalcanzable_y_lo_dice_con_el_maximo():
    r = _gen(_pool(15, edge=1.0))                                                           # cuota = justa (1/p ≈ 1.14): 15 legs ≈ 8x
    assert r.estado == "NO_DISPONIBLE" and r.sonadora is None and "cuota_objetivo_inalcanzable" in r.motivo
    d = r.diagnostico
    assert d["n_partidos_elegibles"] == 15 and d["cuota_maxima_alcanzable"] < 100 and d["n_para_el_maximo"] == 15
    assert f"{d['cuota_maxima_alcanzable']:.1f}x" in r.motivo


def test_con_p_conservadora_mayor_a_075_la_cuota_justa_de_15_piernas_no_llega_a_100x():
    """El hallazgo aritmético: p > 0.75 => cuota justa < 1.333; 1.333^15 ≈ 75x < 100x. Superar 100x exige cuotas por ENCIMA de la justa."""
    assert (1 / 0.75) ** 15 < 100
    r = _gen(_pool(15, edge=1.0))
    assert r.diagnostico["cuota_maxima_alcanzable"] < (1 / 0.75) ** 15


@pytest.mark.parametrize("etiqueta,kw,razon", [
    ("CRS alto", dict(crs=40), "crs_alto"), ("CRS no disponible", dict(crs=None), "crs_no_disponible"), ("veto CRS", dict(veto=True), "crs_veto"),
    ("dato obsoleto", dict(edad_h=30.0), "dato_obsoleto"), ("dato del futuro", dict(edad_h=-2.0), "info_posterior_a_ahora")])
def test_las_selecciones_no_elegibles_se_descartan_con_su_razon(etiqueta, kw, razon):
    r = _gen(_pool(14, edge=1.30, **kw))
    assert r.estado == "NO_DISPONIBLE" and r.diagnostico["n_selecciones_elegibles"] == 0 and r.diagnostico["descartes"][razon] == 14, etiqueta


def test_crs_39_es_elegible_y_crs_40_no_la_frontera_es_estricta():
    assert _gen(_pool(14, edge=1.30, crs=39)).estado == "PROPUESTA"
    assert _gen(_pool(14, edge=1.30, crs=40)).estado == "NO_DISPONIBLE"


def test_p_conservadora_baja_no_pasa_el_filtro():
    pool = [_cand(100 + i, "EPL", 1.0, 0.9, edge=3.0, sel=("OU:2.5:over",)) for i in range(14)]   # p ≈ 0.28: nada cerca de 0.75
    r = _gen(pool)
    assert r.estado == "NO_DISPONIBLE" and r.diagnostico["descartes"]["p_conservadora_no_supera_el_minimo"] == 14


def test_las_piernas_sgp_no_cuentan_porque_son_dos_selecciones_del_mismo_partido():
    pool = [_cand(100 + i, "EPL", 2.3, 1.7, edge=1.3, sel=("OU:1.5:over", "1X2:1")) for i in range(14)]
    r = _gen(pool)
    assert r.estado == "NO_DISPONIBLE" and r.diagnostico["descartes"]["no_es_seleccion_simple"] == 14


def test_menos_partidos_que_n_min_no_hay_soñadora():
    r = _gen(_pool(9, edge=1.5))
    assert r.estado == "NO_DISPONIBLE" and "menos_de_10_partidos" in r.motivo and r.diagnostico["n_partidos_elegibles"] == 9


def test_el_umbral_de_cuota_es_estricto_y_no_se_relaja():
    pool = _pool(14, edge=1.30)
    maxima = _gen(pool).diagnostico["cuota_maxima_alcanzable"]
    cfg = _cfg()
    cfg["sonadoras"]["cuota_objetivo"] = maxima * 0.999
    assert _gen(pool, cfg).estado == "PROPUESTA"
    cfg["sonadoras"]["cuota_objetivo"] = maxima * 1.001
    assert _gen(pool, cfg).estado == "NO_DISPONIBLE"
    cfg["sonadoras"]["cuota_objetivo"] = maxima                                             # igual NO basta: es "mayor que"
    r = _gen(pool, cfg)
    assert r.estado == "NO_DISPONIBLE" and r.motivo.startswith("cuota_objetivo_inalcanzable:") and "con_la_seleccion" not in r.motivo


# ------------------------------------------------------------------ validación y aislamiento
@pytest.mark.parametrize("cambio", [dict(n_min=12, n_max=10), dict(cuota_objetivo=1.0), dict(p_min_pierna=1.2), dict(n_min=0)])
def test_configuracion_invalida_falla_en_voz_alta(cambio):
    cfg = _cfg()
    cfg["sonadoras"].update(cambio)
    with pytest.raises(ValueError):
        _gen(_pool(), cfg)


@pytest.mark.parametrize("stake", [0.0, -0.1, float("nan"), float("inf")])
def test_stake_invalido_falla(stake):
    with pytest.raises(ValueError, match="stake_u"):
        _gen(_pool(), stake=stake)


def test_sin_version_o_con_semilla_negativa_falla_cerrado():
    with pytest.raises(ValueError, match="VersionCalibradorTickets ausente"):
        sn.generar_sonadoras(_pool(), None, _cfg(), semilla=1, ahora=AHORA, stake_u=0.1)
    with pytest.raises(ValueError, match="semilla"):
        _gen(_pool(), semilla=-1)


def test_no_altera_el_ticket_del_dia_ni_comparte_estado_con_tickets():
    pool, cfg, ver = _pool(), _cfg(), _version()
    antes = json.dumps(tk.generar_tickets(pool, ver, cfg, modo_experimental=True, semilla=11, ahora=AHORA).a_json(), sort_keys=True)
    _gen(pool, cfg, version=ver)
    despues = json.dumps(tk.generar_tickets(pool, ver, cfg, modo_experimental=True, semilla=11, ahora=AHORA).a_json(), sort_keys=True)
    assert antes == despues
    nombres = _identificadores(sn)
    assert not {"generar_tickets", "CachePierna", "riesgo", "cerebro2"} & nombres             # módulo separado: ni tickets.generar ni Cerebro 2
    assert not {n for n in nombres if "kelly" in n.lower() or "stake_max" in n.lower()}      # sin Kelly


def _identificadores(modulo) -> set[str]:
    import ast
    ids = set()
    for n in ast.walk(ast.parse(inspect.getsource(modulo))):
        if isinstance(n, ast.Name):
            ids.add(n.id)
        elif isinstance(n, ast.Attribute):
            ids.add(n.attr)
        elif isinstance(n, (ast.ImportFrom, ast.Import)):
            ids.update(a.name for a in n.names)
            if isinstance(n, ast.ImportFrom) and n.module:
                ids.add(n.module)
    return ids


def test_el_modulo_solo_importa_primitivas_de_dependencia_incertidumbre_y_tipos_de_tickets():
    importados = {l.split()[1] for l in inspect.getsource(sn).splitlines() if l.startswith(("from golmodel", "import golmodel"))}
    assert importados <= {"golmodel.modelos", "golmodel.modelos.mercados", "golmodel.motor", "golmodel.motor.tickets"}, importados


# ------------------------------------------------------------------ Cerebro 2: audita, máximo CON_RESERVAS
def _salida_cerebro(pool=None):
    cfg, ver = _cfg(), _version()
    s = _gen(pool or _pool(), cfg, version=ver).sonadora
    return son.como_salida_tickets(s, ver, cfg, AHORA, 14), cfg


def test_cerebro2_audita_la_sonadora_y_el_estado_nunca_es_aprobado():
    from golmodel.cerebro2 import riesgo
    salida, cfg = _salida_cerebro()
    v = riesgo.evaluar(salida, {}, cfg)
    assert v.estado in ("CON_RESERVAS", "EN_OBSERVACION", "RECHAZADO", "ABSTENCION") and v.estado != "APROBADO"
    assert v.modo_experimental is True and v.techo_estado == "CON_RESERVAS"
    assert son.limitar_estado(v.estado) == v.estado


def test_cerebro2_no_es_un_sello_de_goma_con_ev_conservador_negativo_rechaza_por_su_gate():
    import dataclasses
    from golmodel.cerebro2 import riesgo
    salida, cfg = _salida_cerebro()
    tk_ = salida.ticket
    pc = 0.5 / tk_.cuota_combinada                                                           # p_conservadora tal que EV_cons = -50 %
    malo = dataclasses.replace(salida, ticket=dataclasses.replace(tk_, p_conservadora=pc, ev_cons=pc * tk_.cuota_combinada - 1.0))
    v = riesgo.evaluar(malo, {}, cfg)
    assert v.estado == "RECHAZADO" and any(g.gate == "ev_cons_ticket" and g.severidad == "FALLA" for g in v.gates)


def test_hallazgo_si_existe_una_sonadora_su_ev_segun_el_modelo_es_positivo_no_negativo():
    """Con p_conservadora > 0.75 por selección y <= 15 selecciones, Π p >= 0.75^15 ≈ 1.3%; con cuota combinada > 100x el EV del modelo es >= +34%.
    Es decir: una soñadora solo EXISTE cuando el modelo cree que las cuotas superan a las justas (casi siempre error del modelo, no valor)."""
    assert 0.75 ** 15 * 100 - 1 > 0.3
    s = _gen(_pool(14, edge=1.30)).sonadora
    assert s.ev_real > 0.3 and s.ev_cons > 0.0


def test_aunque_el_auditor_dijera_aprobado_se_degrada_a_con_reservas():
    assert son.limitar_estado("APROBADO") == "CON_RESERVAS"
    for e in ("CON_RESERVAS", "EN_OBSERVACION", "RECHAZADO", "ABSTENCION"):
        assert son.limitar_estado(e) == e


def test_el_envoltorio_fija_modo_experimental_y_techo_aunque_la_config_los_apague():
    cfg = _cfg()
    cfg["modo_experimental"] = False
    s = _gen(_pool(), cfg).sonadora
    salida = son.como_salida_tickets(s, _version(con="oos_real"), cfg, AHORA, 14)
    assert salida.auditoria.modo_experimental is True and salida.auditoria.techo_estado == "CON_RESERVAS"
    assert salida.ticket.promovible is False and salida.ticket.modo_experimental is True and salida.ticket.tipo == "sonadora"


# ------------------------------------------------------------------ stake
def test_resolver_stake_fijo_por_defecto_y_validacion_del_monto_del_usuario():
    cfg = _cfg()
    assert son.resolver_stake(cfg) == pytest.approx(0.001 / 0.01)                           # 0.1 % de la banca = 0.1 u
    assert son.resolver_stake(cfg, 0.3) == 0.3
    for malo in (0, -1, 0.51, float("nan"), float("inf"), "x"):
        with pytest.raises(ValueError, match="stake_u"):
            son.resolver_stake(cfg, malo)


def test_el_stake_por_defecto_es_cero_punto_uno_porciento_de_la_banca():
    cfg = _cfg()
    assert cfg["sonadoras"]["stake_pct_banca"] == 0.001
    assert son.resolver_stake(cfg) * cfg["bankroll"]["unidad_pct_banca"] == pytest.approx(0.001)


def test_n_max_se_respeta_aunque_el_orden_por_eficiencia_necesitaria_mas_piernas():
    """20 selecciones muy eficientes pero de cuota ínfima (≈1.11) y 5 de cuota alta: por eficiencia harían falta >15 piernas; el máximo exacto con 15
    (las 5 caras + 10 baratas) SÍ supera 100x. Debe usar la alternativa de n_max, jamás exceder n_max."""
    baratas = [_cand(200 + i, ["EPL", "MX1", "SA", "LL"][i % 4], 3.5, 3.0, edge=1.10) for i in range(20)]
    caras = [_cand(300 + i, ["BUN", "L1", "ERE", "EPL", "MX1"][i % 5], 1.9, 1.5, edge=2.7) for i in range(5)]
    r = _gen(baratas + caras)
    assert r.diagnostico["n_partidos_elegibles"] == 25, r.diagnostico["descartes"]
    assert r.estado == "PROPUESTA" and r.sonadora.n <= 15 and r.sonadora.cuota_combinada > 100.0
    assert len({p.partido_id for p in r.sonadora.piernas}) == r.sonadora.n
