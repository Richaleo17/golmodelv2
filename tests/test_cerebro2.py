"""Pruebas de cerebro2/ (el auditor independiente).

Mapa de condiciones de la autorización:
  1 MECE + fail-closed ......... test_mece_*, test_fail_closed_*      4 trazabilidad ......... test_trazabilidad_*
  2 devig independiente ........ test_devig_*, test_consenso_*        6 modo_experimental .... test_modo_experimental_*
  3 fallbacks (3 casos) ........ test_fallback_*
Los fixtures son SINTÉTICOS y usan el config REAL (config/v3_umbrales.yaml): verifican el mecanismo, no que los
umbrales sean correctos (validación OOS real pendiente).
"""
import dataclasses
import itertools
import json
import random
from datetime import timedelta

import pytest

from golmodel.cerebro2 import benchmark_mercado as bm
from golmodel.cerebro2 import riesgo
from golmodel.cerebro2.benchmark_mercado import MercadoPierna
from golmodel.cerebro2.decision import ESTADOS, SEVERIDADES, GateEval, resolver_estado
from golmodel.modelos import devig
from golmodel.motor import tickets as tk
from tests.test_tickets import AHORA, _cand, _cfg, _run, _version

CASAS = ("pinnacle", "bet365", "williamhill")


# ------------------------------------------------------------------ fixtures

def _q(p, margen=0.05):
    return {"OU:1.5:over": 1 / (p * (1 + margen)), "OU:1.5:under": 1 / ((1 - p) * (1 + margen))}


def _mercado(salida, desfase=-0.03, casas=CASAS, esperadas=None, sin_cuota=(), c1=False):
    """Cuotas crudas de varias casas alrededor de la probabilidad del modelo (+desfase). `sin_cuota`: casas sin cuota."""
    m = {}
    for p in salida.ticket.piernas:
        pm = p.p_calibrada + desfase
        por_casa = {c: _q(pm + 0.004 * i, 0.04 + 0.01 * i) for i, c in enumerate(casas) if c not in sin_cuota}
        extra = dict(fuente_devig_c1="pinnacle", metodo_devig_c1="shin") if c1 else {}
        m[(p.partido_id, tuple(p.selecciones))] = MercadoPierna(por_casa, tuple(esperadas or casas), "L1", **extra)
    return m


def _ideal(flag=False, **kw):
    s = _run(version=_version(con="oos_real"), flag=flag, cfg=_cfg())
    return s, _mercado(s, **kw)


def _eval(s, m, cfg=None, **kw):
    return riesgo.evaluar(s, m, cfg or _cfg(), **kw)


def _con_ticket(s, **cambios):
    return dataclasses.replace(s, ticket=dataclasses.replace(s.ticket, **cambios))


def _con_pierna(s, i, **cambios):
    piernas = list(s.ticket.piernas)
    piernas[i] = dataclasses.replace(piernas[i], **cambios)
    return _con_ticket(s, piernas=tuple(piernas))


def _con_auditoria(s, **cambios):
    return dataclasses.replace(s, auditoria=dataclasses.replace(s.auditoria, **cambios))


def _gate(v, nombre, i=None):
    g = [x for x in v.gates if x.gate == nombre]
    return g if i is None else g[i]


# ------------------------------------------------------------------ condición 1: MECE


def _oraculo(gates):
    """Implementación INDEPENDIENTE (cadena de ifs) de la precedencia, distinta de la de rangos del código."""
    sev = [(g.severidad, g.requerido) for g in gates]
    if not sev:
        return "ABSTENCION"
    if any(s == "NO_EVALUABLE" and r for s, r in sev):
        return "ABSTENCION"
    if any(s == "FALLA" for s, _ in sev):
        return "RECHAZADO"
    if any(s in ("PENDIENTE", "NO_EVALUABLE") for s, _ in sev):
        return "EN_OBSERVACION"
    if any(s == "AVISO" for s, _ in sev):
        return "CON_RESERVAS"
    return "APROBADO"


def _g(sev, req=False):
    return GateEval("x", "ticket", sev, requerido=req)


def test_mece_exhaustivo_todas_las_combinaciones_de_3_gates():
    opciones = [_g(s, r) for s in SEVERIDADES for r in (False, True)]
    alcanzados = set()
    for combo in itertools.product(opciones, repeat=3):                       # 12^3 = 1728 combinaciones
        e = resolver_estado(list(combo))
        assert e in ESTADOS and isinstance(e, str)                            # exactamente UN estado, nunca None
        assert e == _oraculo(combo), [(g.severidad, g.requerido) for g in combo]
        alcanzados.add(e)
    assert alcanzados == set(ESTADOS)                                         # exhaustivo: los 5 son alcanzables


def test_mece_fuzz_con_longitudes_variables():
    rng = random.Random(7)
    opciones = [_g(s, r) for s in SEVERIDADES for r in (False, True)]
    for _ in range(3000):
        combo = [rng.choice(opciones) for _ in range(rng.randint(0, 12))]
        assert resolver_estado(combo) == _oraculo(combo)


def test_mece_sin_gates_es_abstencion_nunca_aprobado():
    assert resolver_estado([]) == "ABSTENCION"


@pytest.mark.parametrize("gates,esperado", [
    ([_g("NO_EVALUABLE", True), _g("FALLA")], "ABSTENCION"),          # falta un input requerido: gana sobre un FALLA
    ([_g("FALLA"), _g("PENDIENTE")], "RECHAZADO"),                    # la etapa más severa gana
    ([_g("PENDIENTE"), _g("AVISO")], "EN_OBSERVACION"),
    ([_g("NO_EVALUABLE", False)], "EN_OBSERVACION"),                  # no requerido ≡ PENDIENTE
    ([_g("NO_APLICABLE"), _g("OK")], "APROBADO"),                     # NO_APLICABLE es neutro
    ([_g("AVISO"), _g("OK")], "CON_RESERVAS"),                        # AVISO jamás cuenta como PASS
])
def test_mece_precedencia(gates, esperado):
    assert resolver_estado(gates) == esperado


def test_mece_severidad_desconocida_no_se_traga_pero_evaluar_la_convierte_en_abstencion(monkeypatch):
    with pytest.raises(KeyError):
        resolver_estado([_g("RARA")])
    s, m = _ideal()
    monkeypatch.setattr(riesgo, "_g_lift", lambda t, u: GateEval("lift", "ticket", "RARA", True))
    v = _eval(s, m)
    assert v.estado == "ABSTENCION" and "error_de_calculo" in v.razon_principal


# ------------------------------------------------------------------ condición 1/3: fail-closed


def test_fail_closed_excepcion_inyectada_en_un_gate(monkeypatch):
    s, m = _ideal()
    monkeypatch.setattr(riesgo, "_g_ev_cons_ticket", lambda t, u: (_ for _ in ()).throw(RuntimeError("boom")))
    v = _eval(s, m)
    assert v.estado == "ABSTENCION"
    g = _gate(v, "ev_cons_ticket", 0)
    assert g.severidad == "NO_EVALUABLE" and g.requerido and g.razon == "error_de_calculo:RuntimeError"
    assert any(x.gate == "lift" and x.severidad == "OK" for x in v.gates)     # el resto SÍ se evaluó: no tumbó todo


def test_fail_closed_excepcion_fuera_de_los_gates(monkeypatch):
    s, m = _ideal()
    monkeypatch.setattr(riesgo, "id_ticket", lambda salida: (_ for _ in ()).throw(ValueError("x")))
    v = _eval(s, m)
    assert v.estado == "ABSTENCION" and v.razon_principal.startswith("error_de_calculo:ValueError")


@pytest.mark.parametrize("basura", [None, 42, "texto", object(), {}, [], (1, 2)], ids=lambda x: type(x).__name__)
def test_fail_closed_ninguna_entrada_produce_estado_none_ni_error(basura):
    v = riesgo.evaluar(basura, basura, _cfg())
    assert v.estado in ESTADOS and v.estado not in ("APROBADO", "CON_RESERVAS")
    assert v.razon_principal and v.gates                                      # con razón y evidencia, nunca vacío


def test_fail_closed_config_sin_un_umbral():
    cfg = _cfg()
    del cfg["umbrales_provisionales"]["lift_max"]
    s, m = _ideal()
    v = _eval(s, m, cfg)
    assert v.estado == "ABSTENCION" and "KeyError" in v.razon_principal      # nunca se inventa un umbral


def test_fail_closed_si_tickets_se_abstuvo():
    s = _run(cfg=_cfg(umbrales_provisionales={"umbral_ev_cons_ticket": 50.0}))
    assert s.estado == "ABSTENCION"
    v = _eval(s, None)
    assert v.estado == "ABSTENCION" and v.razon_principal == "tickets_se_abstuvo:ninguna_cesta_factible"
    assert v.categoria == "tickets_sin_valor" and v.ticket_resumen is None


# ------------------------------------------------------------------ condición 3: tres fallbacks

class _Reloj:
    def __init__(self, saltos):
        self.saltos, self.i = list(saltos), 0

    def __call__(self):
        t = self.saltos[min(self.i, len(self.saltos) - 1)]
        self.i += 1
        return t


def test_fallback_datos_incompletos():
    s, m = _ideal()
    for cambio in (dict(p_conservadora=None), dict(p_conservadora=float("nan")), dict(cuota=None), dict(crs_total=None),
                   dict(info_hasta=None), dict(p_calibrada=None)):
        v = _eval(_con_pierna(s, 0, **cambio), m)
        assert v.estado == "ABSTENCION", cambio                                # ni APROBADO ni CON_RESERVAS
        assert any("datos_incompletos" in (g.razon or "") for g in v.gates), cambio
    v = _eval(_con_ticket(s, cuota_combinada=None), m)
    assert v.estado == "ABSTENCION" and any("datos_incompletos:cuota_combinada" == (g.razon or "") for g in v.gates)


def test_fallback_error_de_calculo(monkeypatch):
    s, m = _ideal()
    monkeypatch.setattr(riesgo, "_g_crs", lambda p, u: 1 / 0)
    v = _eval(s, m)
    assert v.estado == "ABSTENCION" and any((g.razon or "") == "error_de_calculo:ZeroDivisionError" for g in v.gates)


def test_fallback_timeout():
    s, m = _ideal()
    v = _eval(s, m, reloj=_Reloj([0.0, 999.0]), timeout_s=5)                   # el plazo se agota entre gates
    assert v.estado == "ABSTENCION" and v.razon_principal == "timeout"
    v2 = _eval(s, m, reloj=_Reloj([0.0]), timeout_s=5)                         # sin agotarse: evalúa con normalidad
    assert v2.estado == "APROBADO" and v2.razon_principal is None


# ------------------------------------------------------------------ condición 2: devig independiente


def test_devig_independiente_de_cerebro1():
    s, m = _ideal(c1=True)
    p = s.ticket.piernas[0]
    cuotas = m[(p.partido_id, tuple(p.selecciones))].cuotas_por_casa
    grupo = ("OU:1.5:over", "OU:1.5:under")
    p_c1 = float(devig.shin([cuotas["pinnacle"][x] for x in grupo])[0])        # Cerebro 1: Pinnacle + Shin
    r = bm.consenso_independiente(tuple(p.selecciones), m[(p.partido_id, tuple(p.selecciones))], _cfg())
    assert r.estado == "ok" and r.p_mercado is not None
    assert abs(r.p_mercado - p_c1) > 1e-4                                      # p_mercado_cerebro1 != p_mercado_cerebro2
    assert (r.fuente_devig, r.metodo_devig, r.independiente_de_c1) == ("consenso_multicasa", "power", True)


def test_devig_sin_independencia_si_usa_la_misma_fuente_y_metodo_que_cerebro1():
    s, m = _ideal()
    k = next(iter(m))
    m[k] = dataclasses.replace(m[k], fuente_devig_c1="consenso_multicasa", metodo_devig_c1="power")
    g = _gate(_eval(s, m), "mercado_independiente", 0)
    assert g.severidad == "AVISO" and g.razon == "devig_no_independiente_de_cerebro1"


def test_consenso_dos_casas_es_aviso_y_estado_maximo_con_reservas():
    s, m = _ideal(casas=("pinnacle", "bet365"))                                # solo 2 casas existen
    v = _eval(s, m)
    g = _gate(v, "mercado_independiente")
    assert all(x.severidad == "AVISO" and x.razon.startswith("consenso_insuficiente") for x in g)
    assert v.estado == "CON_RESERVAS"                                          # sin la bandera, y aun así NO es APROBADO
    assert all(x.valor["p_mercado"] is None for x in g)                        # no hay consenso: no se inventa


def test_consenso_tres_casas_una_sin_cuota_es_incompleto_sin_inventar_cuota():
    s, m = _ideal(sin_cuota=("williamhill",))                                  # se esperaban 3, una no cotiza
    v = _eval(s, m)
    for g in _gate(v, "mercado_independiente"):
        assert g.severidad == "AVISO" and g.razon.startswith("consenso_incompleto")
        assert g.valor["casas_faltantes"] == ("williamhill",) and g.valor["casas_usadas"] == ("bet365", "pinnacle")
        assert g.valor["p_mercado"] is None                                    # NADA imputado
    assert v.estado == "CON_RESERVAS"


def test_consenso_casa_con_grupo_incompleto_se_descarta_con_su_razon():
    s, m = _ideal()
    k = next(iter(m))
    por_casa = dict(m[k].cuotas_por_casa)
    por_casa["williamhill"] = {"OU:1.5:over": 1.30}                            # falta el 'under'
    r = bm.consenso_independiente(("OU:1.5:over",), dataclasses.replace(m[k], cuotas_por_casa=por_casa), _cfg())
    assert r.casas_descartadas == {"williamhill": "grupo_incompleto:OU:1.5:under"} and r.estado == "consenso_incompleto"


def test_consenso_cuatro_esperadas_y_tres_utiles_es_ok_y_registra_la_faltante():
    s, m = _ideal(casas=("pinnacle", "bet365", "williamhill"), esperadas=("pinnacle", "bet365", "williamhill", "marathonbet"))
    k = next(iter(m))
    r = bm.consenso_independiente(("OU:1.5:over",), m[k], _cfg())
    assert r.estado == "ok" and r.casas_faltantes == ("marathonbet",) and r.p_mercado is not None


def test_consenso_sgp_y_seleccion_sin_grupo_no_son_auditables():
    cfg = _cfg()
    assert bm.consenso_independiente(("1X2:1", "OU:2.5:over"), None, cfg).razon == "pierna_sgp_sin_grupo_devig"
    assert bm.consenso_independiente(("DC:1X",), None, cfg).razon == "seleccion_sin_grupo_devig_exhaustivo"
    assert bm.consenso_independiente(("OU:1.5:over",), None, cfg).razon == "sin_cuotas_de_mercado"


def test_consenso_ignora_casas_no_elegibles():
    s, m = _ideal()
    k = next(iter(m))
    por_casa = dict(m[k].cuotas_por_casa)
    por_casa["casa_rara"] = _q(0.9)
    r = bm.consenso_independiente(("OU:1.5:over",), dataclasses.replace(m[k], cuotas_por_casa=por_casa,
                                                                         casas_esperadas=CASAS + ("casa_rara",)), _cfg())
    assert r.casas_descartadas == {"casa_rara": "no_elegible"} and r.casas_usadas == tuple(sorted(CASAS))


def test_devig_registra_fuente_metodo_y_casas_en_el_veredicto():
    v = _eval(*_ideal())
    assert (v.fuente_devig, v.metodo_devig) == ("consenso_multicasa", "power")
    assert v.casas_usadas == tuple(sorted(CASAS)) and v.casas_esperadas == tuple(sorted(CASAS))
    for g in _gate(v, "mercado_independiente"):
        for campo in ("fuente_devig", "metodo_devig", "casas_usadas", "casas_esperadas"):
            assert g.valor[campo]


def test_js_simetrica_acotada_y_cero_si_coinciden():
    assert bm.js_bits(0.7, 0.7) == pytest.approx(0.0)
    assert bm.js_bits(0.9, 0.5) == pytest.approx(bm.js_bits(0.5, 0.9))
    assert 0.0 <= bm.js_bits(0.999, 0.001) <= 1.0


# ------------------------------------------------------------------ condición 6: modo_experimental


def test_modo_experimental_caso_ideal_con_reservas_y_sin_bandera_aprobado():
    exper = _eval(*_ideal(flag=True))
    libre = _eval(*_ideal(flag=False))
    assert exper.estado == "CON_RESERVAS" and libre.estado == "APROBADO"
    assert [g.gate for g in exper.gates if g.severidad != "OK"] == ["modo_experimental"]   # el ÚNICO motivo del tope
    assert libre.razon_principal is None


def test_modo_experimental_nunca_aprobado_con_la_bandera_activa():
    for desfase in (-0.10, -0.03, 0.0, 0.05):
        for casas in (CASAS, CASAS[:2]):
            assert _eval(*_ideal(flag=True, desfase=desfase, casas=casas)).estado != "APROBADO"


def test_modo_experimental_candado_oos_real_heredado():
    s, m = _ideal(flag=False)
    v = _eval(_con_auditoria(s, calibrado_con="sinteticos"), m)               # bandera apagada sin calibración real
    assert v.estado == "RECHAZADO"
    g = _gate(v, "consistencia_bandera", 0)
    assert g.severidad == "FALLA" and "oos_real" in g.razon
    v2 = _eval(_con_auditoria(s, techo_estado="CON_RESERVAS"), m)             # techo incoherente con la bandera
    assert v2.estado == "RECHAZADO"


# ------------------------------------------------------------------ gates concretos


def test_gate_duros_producen_rechazado():
    s, m = _ideal()
    pi = s.ticket.p_independencia
    casos = {
        "lift": _con_ticket(s, p_media=pi * 1.5),
        "ic_ancho": _con_ticket(s, ic=(0.0, s.ticket.p_media * 1.5)),
        "ev_cons_ticket": _con_ticket(s, cuota_combinada=1.0 / s.ticket.p_conservadora),
        "concentracion_liga": _con_pierna(_con_pierna(_con_pierna(s, 0, liga="X"), 1, liga="X"), 2, liga="X"),
        "una_pierna_por_partido": _con_pierna(s, 1, partido_id=s.ticket.piernas[0].partido_id),
        "crs": _con_pierna(s, 0, crs_total=45),
        "ev_cons_pierna": _con_pierna(s, 0, cuota=1.0 / s.ticket.piernas[0].p_conservadora),
    }
    for gate, salida in casos.items():
        mk = m if gate != "una_pierna_por_partido" else _mercado(salida)
        v = _eval(salida, mk)
        assert v.estado == "RECHAZADO", gate
        assert any(g.gate == gate and g.severidad == "FALLA" for g in v.gates), gate
        assert v.categoria == "rechazo_sustantivo"


def test_gates_de_aviso_producen_con_reservas():
    s, m = _ideal()
    t = s.ticket
    avisos = {
        "ev_cons_ticket": _con_ticket(s, cuota_combinada=(1 + 0.07) / t.p_conservadora),          # EV 0.07 ∈ (0.05, 0.10]
        "ic_ancho": _con_ticket(s, ic=(0.0, t.p_media * 0.9)),                                     # 0.9 > 0.8·1.0
        "crs": _con_pierna(s, 0, crs_total=35),
        "calibracion_nivel": _con_pierna(s, 0, calibrado=False, calibrador_version="identidad:sin_calibrador"),
        "frescura": _con_pierna(s, 0, info_hasta=(AHORA - timedelta(hours=10)).isoformat()),
    }
    for gate, salida in avisos.items():
        v = _eval(salida, m)
        assert v.estado == "CON_RESERVAS", (gate, v.razon_principal)
        assert any(g.gate == gate and g.severidad == "AVISO" for g in v.gates), gate


def test_frescura_propia_de_cerebro2_abstencion_si_la_info_es_demasiado_antigua_o_del_futuro():
    s, m = _ideal()
    viejo = _eval(_con_pierna(s, 0, info_hasta=(AHORA - timedelta(hours=30)).isoformat()), m)
    futuro = _eval(_con_pierna(s, 0, info_hasta=(AHORA + timedelta(hours=1)).isoformat()), m)
    assert viejo.estado == futuro.estado == "ABSTENCION"
    assert "info_demasiado_antigua" in viejo.razon_principal and "info_posterior" in futuro.razon_principal


def test_discrepancia_modelo_mercado_bandas():
    s, _ = _ideal()
    for desfase, esperado in ((-0.03, "APROBADO"), (-0.15, "CON_RESERVAS"), (-0.30, "RECHAZADO")):
        v = _eval(s, _mercado(s, desfase=desfase))
        assert v.estado == esperado, (desfase, v.estado, v.razon_principal)
    v = _eval(s, _mercado(s, desfase=-0.30))
    assert any(g.gate == "discrepancia_modelo_mercado" and g.severidad == "FALLA" and g.valor > 0.20 for g in v.gates)


def test_d_mm_y_js_no_aplicables_sin_consenso_y_con_razon():
    s, _ = _ideal()
    v = _eval(s, None)                                                         # sin cuotas de mercado
    assert v.estado == "CON_RESERVAS"                                          # AVISO de mercado: tope, nunca rechazo
    for nombre in ("discrepancia_modelo_mercado", "divergencia_js"):
        for g in _gate(v, nombre):
            assert g.severidad == "NO_APLICABLE" and g.razon.startswith("consenso_no_disponible")


def test_js_gate_aviso_con_discrepancia_grande():
    cons = bm.ResultadoConsenso("ok", None, 0.50, "consenso_multicasa", "power", ("a", "b", "c"), ("a", "b", "c"))
    u = riesgo._umbrales(_cfg())
    base = _ideal()[0].ticket.piernas[0]
    # 0.90 vs 0.50 da 0.147 bits (< 0.15): con UN modelo base, JS solo salta con discrepancias de ~40 pp, que d_mm ya
    # habría rechazado: la redundancia anticipada en el pre-registro §2.9. 0.95 vs 0.50 = 0.205 bits sí salta.
    assert riesgo._g_js(dataclasses.replace(base, p_calibrada=0.90), cons, u).severidad == "OK"
    g = riesgo._g_js(dataclasses.replace(base, p_calibrada=0.95), cons, u)
    assert g.severidad == "AVISO" and g.valor > u["js_degradar_bits"]


def test_estado_por_pierna_senala_la_pierna_culpable():
    s, m = _ideal()
    v = _eval(_con_pierna(s, 2, crs_total=45), m)
    estados = list(v.estado_por_pierna.values())
    assert v.estado == "RECHAZADO" and estados.count("RECHAZADO") == 1 and estados.count("APROBADO") == len(estados) - 1


# ------------------------------------------------------------------ condición 4: trazabilidad


def test_trazabilidad_inputs_completos_todos_los_campos_no_nulos():
    s, m = _ideal(flag=True)
    v = _eval(s, m)
    j = v.a_json()
    for campo in ("estado", "ticket_id", "fuente_devig", "metodo_devig", "casas_usadas", "casas_esperadas",
                  "modo_experimental", "techo_estado", "calibrado_con", "info_asof", "config_hash", "semilla", "n_draws",
                  "ticket_resumen", "razon_principal"):
        assert j[campo] is not None, campo
    for clave in ("tickets", "calibracion_mercado", "incertidumbre", "dependencia", "calibracion_tickets", "cerebro2"):
        assert j["versiones"][clave], clave                                    # versiones de TODOS los módulos
    assert j["versiones"]["cerebro2"] == riesgo.VERSION and j["modo_experimental"] is True
    for g in j["gates"]:                                                       # gate / valor / umbral / resultado
        assert g["gate"] and g["severidad"] in SEVERIDADES and g["ambito"] in ("ticket", "seleccion")
        if g["severidad"] != "OK":
            assert g["razon"]
    assert any(g["decisivo"] for g in j["gates"])                              # cuál lo determinó
    json.dumps(j, default=str)                                                 # serializable


def test_trazabilidad_inputs_faltantes_dan_none_explicito():
    j = riesgo.evaluar(None, None, _cfg()).a_json()
    claves = {"estado", "razon_principal", "categoria", "ticket_id", "gates", "estado_por_pierna", "fuente_devig",
              "metodo_devig", "casas_usadas", "casas_esperadas", "mercado_por_pierna", "modo_experimental", "techo_estado",
              "calibrado_con", "info_asof", "versiones", "config_hash", "semilla", "n_draws", "ticket_resumen"}
    assert claves <= set(j)                                                    # ningún campo AUSENTE
    for campo in ("ticket_id", "fuente_devig", "metodo_devig", "casas_usadas", "casas_esperadas", "modo_experimental",
                  "techo_estado", "calibrado_con", "info_asof", "config_hash", "semilla", "n_draws", "ticket_resumen"):
        assert j[campo] is None, campo                                         # None EXPLÍCITO, no valor inventado
    assert j["versiones"] == {"cerebro2": riesgo.VERSION}


def test_trazabilidad_la_marca_temporal_es_la_del_ticket_de_entrada_no_now():
    s, m = _ideal()
    assert _eval(s, m).info_asof == AHORA.isoformat() == s.auditoria.ahora


def test_trazabilidad_todo_gate_no_ok_lleva_razon_y_todo_estado_no_aprobado_tiene_decisivo():
    s, _ = _ideal(flag=True)
    for m in (None, _mercado(s), _mercado(s, casas=CASAS[:2]), _mercado(s, desfase=-0.30)):
        v = _eval(s, m)
        assert all(g.razon for g in v.gates if g.severidad != "OK")
        if v.estado != "APROBADO":
            assert any(g.decisivo for g in v.gates) and v.razon_principal


def test_trazabilidad_ticket_id_es_determinista_y_distinto_entre_tickets():
    s, m = _ideal()
    assert _eval(s, m).ticket_id == _eval(s, m).ticket_id
    otro = _run(version=_version(con="oos_real"), flag=False, cfg=_cfg(), semilla=12)
    assert _eval(otro, _mercado(otro)).ticket_id != _eval(s, m).ticket_id


def test_reproducibilidad_mismo_input_mismo_veredicto():
    s, m = _ideal(flag=True)
    assert json.dumps(_eval(s, m).a_json(), sort_keys=True, default=str) == json.dumps(_eval(s, m).a_json(), sort_keys=True, default=str)
