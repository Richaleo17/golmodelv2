"""Modo «Operativo (Beta)» (integracion/beta.py + benchmark + vistas + UI). Datos SINTÉTICOS; PostgreSQL real para persistencia y UI.

Se verifica: (1) apagado por defecto y sin efecto alguno sobre la decisión; (2) activado y con calibración `oos_real` quita el techo y deja decidir al auditor, sin tocar
`modo_experimental` de la config; (3) fail-closed: sin `oos_real` no surte efecto, y una versión `oos_real` SIN el interruptor tampoco apaga el techo; (4) el stake es Kelly
fraccional de `p_conservadora` con la fracción tope 0.10 y el tope en unidades; (5) sello BETA y banner en `auditoria_decisiones` y en la vista; (6) la UI muestra el banner
completo en rojo. Nada de esto modifica `tickets.py`, `dependencia.py` ni `cerebro2/`."""
from __future__ import annotations

import copy
import json
from datetime import date as _d

import pytest
import yaml

from golmodel.config import RUTA_V3, cargar_v3
from golmodel.integracion import benchmark as bm
from golmodel.integracion import beta
from golmodel.motor import bankroll as bk
from golmodel.servicios import vistas
from tests.test_integracion import cargado, cfg, dsn, mundo  # noqa: F401  (fixtures de la BD real)
from tests.test_integracion_benchmark import bd, con  # noqa: F401
from tests.test_integracion_candidatas import _dia, poblada  # noqa: F401
from tests.test_tickets import AHORA, _cand, _cfg, _pool, _version

BANNER = ("⚠️ MODO BETA. La probabilidad central pasó la prueba de calibración y no se detectó sobredispersión por liga y día, pero la incertidumbre de cada partido (τ) "
          "NO está validada y la prueba solo descarta sesgos grandes. Kelly conservador; apuestas con sello BETA. Úsalo bajo tu propio riesgo.")


def _cfg_beta(activo=True, **kw):
    c = _cfg()
    c["modo_operativo_beta"] = {"activo": activo, "kelly_fraccion_parlay": 0.10, "sello": "BETA", "banner": BANNER, **kw}
    return c


def _decidir(cfg, con_cal="oos_real"):
    return bm.decidir(_pool(), {}, _version(con=con_cal), cfg, semilla=11, ahora=AHORA)


# ------------------------------------------------------------------ config
def test_la_config_real_trae_el_modo_beta_apagado_con_el_texto_aprobado_y_modo_experimental_sigue_true():
    cfg = cargar_v3()
    b = cfg["modo_operativo_beta"]
    assert b["activo"] is False and b["kelly_fraccion_parlay"] == 0.10 and b["sello"] == "BETA" and b["banner"] == BANNER
    assert cfg["modo_experimental"] is True                                    # la protección por defecto no se toca
    assert cfg["bankroll"]["stake_max_parlay_u"] == 0.5


@pytest.mark.parametrize("cambio", [{"activo": "true"}, {"activo": 1}, {"activo": None}, {"kelly_fraccion_parlay": 0}, {"kelly_fraccion_parlay": 0.5},
                                    {"kelly_fraccion_parlay": True}, {"kelly_fraccion_parlay": "0.1"}, {"banner": ""}, {"sello": ""}, {"banner": None}])
def test_la_config_beta_se_valida_estricto(tmp_path, cambio):
    cfg = yaml.safe_load(RUTA_V3.read_text(encoding="utf-8"))
    cfg["modo_operativo_beta"].update(cambio)
    ruta = tmp_path / "v3.yaml"
    ruta.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="modo_operativo_beta"):
        cargar_v3(ruta)


def test_la_seccion_beta_es_opcional_y_ausente_equivale_a_apagado(tmp_path):
    cfg = yaml.safe_load(RUTA_V3.read_text(encoding="utf-8"))
    del cfg["modo_operativo_beta"]
    ruta = tmp_path / "v3.yaml"
    ruta.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert "modo_operativo_beta" not in cargar_v3(ruta)
    assert beta.resolver(cfg, _version(con="oos_real"))[2] == {"solicitado": False, "efectivo": False}


# ------------------------------------------------------------------ resolver (puro)
def test_apagado_no_cambia_nada_ni_con_calibracion_oos_real():
    cfg = _cfg()
    modo, cfg2, info = beta.resolver(cfg, _version(con="oos_real"))
    assert modo is True and cfg2 is cfg and info == {"solicitado": False, "efectivo": False}
    c = _cfg_beta(activo=False)
    assert beta.resolver(c, _version(con="oos_real"))[0] is True


def test_activo_con_oos_real_apaga_el_techo_solo_para_esa_decision_y_no_muta_la_config():
    cfg = _cfg_beta()
    copia = copy.deepcopy(cfg)
    modo, cfg2, info = beta.resolver(cfg, _version(con="oos_real"))
    assert modo is False and info["efectivo"] is True and info["sello"] == "BETA" and info["banner"] == BANNER and info["calibrado_con"] == "oos_real"
    assert info["modo_experimental_config"] is True and cfg == copia and cfg["modo_experimental"] is True       # la config NO cambia
    assert cfg2 is not cfg and beta.efectivo(info) and not beta.efectivo({"efectivo": False}) and not beta.efectivo(None)


@pytest.mark.parametrize("con_cal", ["no_calibrado", "sinteticos"])
def test_activo_sin_oos_real_no_surte_efecto_y_lo_explica(con_cal):
    cfg = _cfg_beta()
    modo, cfg2, info = beta.resolver(cfg, _version(con=con_cal))
    assert modo is True and cfg2 is cfg and info["solicitado"] is True and info["efectivo"] is False and "oos_real" in info["motivo"] and con_cal in info["motivo"]
    assert "banner" not in info                                                      # sin efecto, no hay banner de «operativo»


@pytest.mark.parametrize("valor", ["true", 1, None, "si"])
def test_activo_debe_ser_booleano_fail_closed(valor):
    cfg = _cfg_beta(activo=valor)
    with pytest.raises(ValueError, match="booleano"):
        beta.resolver(cfg, _version(con="oos_real"))


@pytest.mark.parametrize("bankroll,esperado", [(0.25, 0.10), (0.10, 0.10), (0.05, 0.05)])
def test_la_fraccion_de_kelly_nunca_supera_la_de_beta_y_respeta_una_mas_conservadora(bankroll, esperado):
    cfg = _cfg_beta()
    cfg["bankroll"]["kelly_fraccion_parlay"] = bankroll
    _, cfg2, info = beta.resolver(cfg, _version(con="oos_real"))
    assert cfg2["bankroll"]["kelly_fraccion_parlay"] == esperado == info["kelly_fraccion_parlay"] and cfg["bankroll"]["kelly_fraccion_parlay"] == bankroll


# ------------------------------------------------------------------ decisión (tickets.py + Cerebro 2, sin tocarlos)
def test_sin_el_interruptor_la_decision_es_identica_aunque_haya_calibracion_oos_real():
    cfg = _cfg()
    cfg_sin = copy.deepcopy(cfg)
    cfg_sin.pop("modo_operativo_beta", None)
    a, va, _ = _decidir(cfg)
    b, vb, _ = _decidir(cfg_sin)
    assert json.dumps(a.a_json(), sort_keys=True) == json.dumps(b.a_json(), sort_keys=True) and va.estado == vb.estado
    assert a.ticket.modo_experimental is True and a.ticket.techo_estado == "CON_RESERVAS" and a.ticket.promovible is False
    assert va.estado != "APROBADO"                                                    # una versión oos_real SOLA no apaga el techo (lo decide la config/humano)
    assert next(g for g in va.gates if g.gate == "modo_experimental").severidad == "AVISO"       # el auditor sigue viendo el tope


def test_beta_activo_con_oos_real_quita_el_techo_y_el_auditor_decide_dentro_de_sin_tope():
    salida, ver, error = _decidir(_cfg_beta())
    t = salida.ticket
    assert error is None and t.modo_experimental is False and t.techo_estado == "SIN_TOPE" and t.promovible is True
    gate = next(g for g in ver.gates if g.gate == "modo_experimental")
    assert gate.severidad == "OK" and gate.valor is False                              # el auditor ya no ve el tope: decide con sus propios gates
    assert salida.estado == "PROPUESTO" and ver.estado is not None


def test_beta_activo_sin_oos_real_mantiene_el_techo_y_no_da_error():
    salida, ver, error = _decidir(_cfg_beta(), con_cal="sinteticos")
    assert error is None and salida.ticket.techo_estado == "CON_RESERVAS" and salida.ticket.modo_experimental is True and ver.estado != "APROBADO"


def test_el_candado_de_tickets_py_sigue_en_pie_si_la_config_apaga_la_bandera_sin_oos_real():
    cfg = _cfg_beta(activo=False)
    cfg["modo_experimental"] = False
    salida, ver, error = _decidir(cfg, con_cal="sinteticos")
    assert salida is None and "oos_real" in error


def test_el_stake_es_kelly_fraccional_de_p_conservadora_con_fraccion_tope_0_10_y_sin_pasar_el_tope_de_unidades():
    cfg = _cfg_beta()
    cfg["bankroll"]["kelly_fraccion_parlay"] = 0.25                                    # la config general permitiría 0.25; Beta lo baja a 0.10
    cfg["bankroll"]["stake_max_parlay_u"] = 1000.0                                     # sin tope efectivo: se ve el Kelly puro
    salida, _, _ = _decidir(cfg)
    t = salida.ticket
    b = cfg["bankroll"]
    esperado = bk.stake_u(bk.kelly(t.p_conservadora, t.cuota_combinada), fraccion=0.10, max_u=1000.0, unidad_pct=b["unidad_pct_banca"])
    con_media = bk.stake_u(bk.kelly(t.p_media, t.cuota_combinada), fraccion=0.10, max_u=1000.0, unidad_pct=b["unidad_pct_banca"])
    con_fraccion_alta = bk.stake_u(bk.kelly(t.p_conservadora, t.cuota_combinada), fraccion=0.25, max_u=1000.0, unidad_pct=b["unidad_pct_banca"])
    assert t.stake_u == pytest.approx(esperado) and t.stake_u > 0
    assert esperado != con_media and esperado != con_fraccion_alta                     # no es p_media ni la fracción 0.25 (el test distingue ambas)
    cfg2 = _cfg_beta()
    cfg2["bankroll"]["stake_max_parlay_u"] = 0.2
    assert _decidir(cfg2)[0].ticket.stake_u <= 0.2                                     # el tope en unidades se respeta


# ------------------------------------------------------------------ persistencia: sello BETA y banner en auditoria_decisiones
def _decision(con, dia, cfg, con_cal="oos_real"):
    """Un ticket de tickets.py con partidos REALES de la BD, decidido con `cfg` (con o sin Beta) y persistible."""
    from tests.test_tickets import DATOS_POOL
    ids = [r["id"] for r in con.execute("SELECT id FROM partidos ORDER BY id LIMIT %s", (len(DATOS_POOL),))]
    pool = [_cand(i, d[1], d[2], d[3]) for i, d in zip(ids, DATOS_POOL)]
    ver = _version(con=con_cal)
    salida, veredicto, error = bm.decidir(pool, {}, ver, cfg, semilla=11, ahora=AHORA)
    from golmodel.integracion.candidatas import ResultadoCandidatas
    return bm.DecisionV3(dia, AHORA, ResultadoCandidatas(dia, AHORA, tuple(pool), ()), salida, veredicto, error, beta.resolver(cfg, ver)[2])


def test_persistir_guarda_el_sello_y_el_banner_y_la_vista_los_devuelve(con):
    dia = _d(2026, 11, 9)
    d = _decision(con, dia, _cfg_beta())
    ids = bm.persistir(con, d)
    p = con.execute("SELECT payload, modo_experimental, techo_estado, calibrado_con FROM auditoria_decisiones WHERE id = %s", (ids["tickets"],)).fetchone()
    assert p["payload"]["beta"]["sello"] == "BETA" and p["payload"]["beta"]["banner"] == BANNER and p["payload"]["beta"]["efectivo"] is True
    assert p["modo_experimental"] is False and p["techo_estado"] == "SIN_TOPE" and p["calibrado_con"] == "oos_real"       # lo que de verdad se decidió
    dec = vistas.decision_v3(con, dia)
    assert dec["sello"] == "BETA" and dec["advertencia"] == BANNER and dec["beta"]["efectivo"] is True and dec["modo_experimental"] is False


def test_sin_beta_el_payload_no_lleva_sello_y_la_vista_sigue_con_la_advertencia_provisional(con):
    dia = _d(2026, 11, 9)
    d = _decision(con, dia, _cfg(), con_cal="sinteticos")
    ids = bm.persistir(con, d)
    assert "beta" not in con.execute("SELECT payload FROM auditoria_decisiones WHERE id = %s", (ids["tickets"],)).fetchone()["payload"]
    dec = vistas.decision_v3(con, dia)
    assert dec["sello"] is None and dec["beta"] is None and dec["advertencia"] == vistas.ADVERTENCIA_V3 and dec["modo_experimental"] is True


def test_beta_solicitado_sin_efecto_deja_el_motivo_en_la_auditoria_pero_ni_sello_ni_banner_en_la_vista(con):
    dia = _d(2026, 11, 9)
    d = _decision(con, dia, _cfg_beta(), con_cal="sinteticos")
    bm.persistir(con, d)
    dec = vistas.decision_v3(con, dia)
    assert dec["beta"]["efectivo"] is False and "SIN EFECTO" in dec["beta"]["motivo"] and dec["sello"] is None
    assert dec["advertencia"] == vistas.ADVERTENCIA_V3 and dec["techo_estado"] == "CON_RESERVAS"


@pytest.mark.parametrize("estado,con_sello,promovible,esperado", [("APROBADO", True, True, True), ("CON_RESERVAS", True, True, False), ("RECHAZADO", True, True, False),
                                                                    ("APROBADO", False, True, False), ("APROBADO", True, False, False)])
def test_apuesta_del_dia_solo_con_sello_beta_aprobado_por_el_auditor_y_promovible(con, estado, con_sello, promovible, esperado):
    dia = _d(2026, 11, 9)
    bm.persistir(con, _decision(con, dia, _cfg_beta()))
    dec = vistas.decision_v3(con, dia)
    dec = {**dec, "estado": estado, "sello": "BETA" if con_sello else None, "ticket": {**dec["ticket"], "promovible": promovible}}
    assert vistas._ticket_v3_como_parlay(con, dec)["es_apuesta_del_dia"] is esperado


def test_un_ticket_con_modo_experimental_jamas_es_apuesta_del_dia(con):
    dia = _d(2026, 11, 9)
    bm.persistir(con, _decision(con, dia, _cfg(), con_cal="sinteticos"))
    dec = {**vistas.decision_v3(con, dia), "estado": "APROBADO"}                                         # aunque alguien forzara el estado
    assert vistas._ticket_v3_como_parlay(con, dec)["es_apuesta_del_dia"] is False


# ------------------------------------------------------------------ UI: banner rojo completo
pytest.importorskip("streamlit")
from tests.test_ui_smoke import APP, AppTest, _ir_a_panel, _ir_a_parlay, _textos, entorno  # noqa: E402,F401
from golmodel import db  # noqa: E402


def test_la_ui_muestra_el_banner_beta_completo_en_rojo_y_el_sello_en_las_dos_pantallas(entorno, bd):  # noqa: F811
    with db.conectar(bd) as c:
        bm.persistir(c, _decision(c, _dia(c), _cfg_beta()))
    for ir in (_ir_a_panel, _ir_a_parlay):
        at = ir(AppTest.from_file(APP, default_timeout=60))
        assert not at.exception, [e.value for e in at.exception]
        rojos = [m.value for m in at.markdown if "#b00020" in m.value]
        assert len(rojos) == 1 and BANNER.replace("&", "&amp;") in rojos[0] and "1.25rem" in rojos[0]       # banner grande, texto íntegro
        assert not any("PROVISIONAL" in w.value for w in at.warning)                                        # el aviso genérico lo sustituye
        assert any("BETA" in m.value for m in at.markdown if "Ticket V3" in m.value)                        # sello en el título/tarjeta


def test_la_ui_sin_beta_no_muestra_banner_rojo_ni_sello(entorno, bd):  # noqa: F811
    with db.conectar(bd) as c:
        bm.persistir(c, _decision(c, _dia(c), _cfg(), con_cal="sinteticos"))
    at = _ir_a_parlay(AppTest.from_file(APP, default_timeout=60))
    assert not at.exception, [e.value for e in at.exception]
    assert not any("#b00020" in m.value for m in at.markdown) and any("PROVISIONAL" in w.value for w in at.warning)
    assert "BETA" not in _textos(at)
