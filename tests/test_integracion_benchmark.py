"""integracion/benchmark.py — V3 en la ruta en vivo (candidatas -> tickets.py + dependencia.py -> cerebro2).

Verificado con datos SINTÉTICOS. Los tests de paridad comparan contra llamadas DIRECTAS a tickets.py y a dependencia.py;
los negativos prueban que ante un fallo de dependencia.py el resultado es ABSTENCIÓN y jamás un ticket por independencia.
"""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pytest

from golmodel import db
from golmodel.integracion import benchmark as bm
from golmodel.migraciones import aplicar
from golmodel.modelos import incertidumbre as inc
from golmodel.motor import dependencia as dep
from golmodel.motor import tickets as tk
# mundo real: pipeline V2 sobre PostgreSQL (mismos fixtures de test_integracion_candidatas)
from tests.test_integracion import AHORA as AHORA_BD, cargado, cfg, dsn, mundo  # noqa: F401
from tests.test_integracion_candidatas import _dia, poblada, v3  # noqa: F401
from tests.test_tickets import AHORA, _cand, _cfg, _pool, _version


def _json(x):
    return json.dumps(x.a_json(), sort_keys=True)


# ------------------------------------------------------------------ núcleo puro: paridad y fail-closed

def test_decidir_produce_el_mismo_ticket_que_tickets_py_sin_divergencias():
    cfg_v3, pool, ver = _cfg(), _pool(), _version()
    salida, veredicto, error = bm.decidir(pool, {}, ver, cfg_v3, semilla=11, ahora=AHORA)
    directo = tk.generar_tickets(pool, ver, cfg_v3, modo_experimental=cfg_v3["modo_experimental"], semilla=11, ahora=AHORA)
    assert error is None and salida.ticket is not None
    assert _json(salida) == _json(directo)                                       # byte a byte el mismo resultado
    assert salida.ticket.p_conservadora == directo.ticket.p_conservadora


def _piernas_del_ticket(t, pool, ver):
    por_clave = {(c.partido_id, tuple(c.selecciones)): c for c in pool}
    out = []
    for pt in t.piernas:
        c = por_clave[(pt.partido_id, tuple(pt.selecciones))]
        out.append(dep.Pierna(c.partido_id, c.liga, c.lambda_local, c.lambda_visitante, c.rho, tuple(c.selecciones),
                              inc.tau_idiosincratico(ver.c_global, c.peso_efectivo_local),
                              inc.tau_idiosincratico(ver.c_global, c.peso_efectivo_visitante)))
    return out


def test_la_conjunta_del_ticket_coincide_con_dependencia_py_recalculada_aparte_dentro_del_error_montecarlo():
    """HALLAZGO: `probabilidad_conjunta` (la que usa calibracion_tickets) y la ruta de tickets (factores_compartidos +
    p_pierna_sorteos, con caché) usan flujos aleatorios DISTINTOS: mismo estimador, otra realización de Monte Carlo. No
    son idénticos bit a bit; deben coincidir dentro del error de MC. Aquí se acota esa diferencia (un sesgo real, p. ej.
    otra τ o otra λ, la rompería)."""
    cfg_v3, pool, ver = _cfg(), _pool(), _version()
    salida, _, _ = bm.decidir(pool, {}, ver, cfg_v3, semilla=11, ahora=AHORA)
    t = salida.ticket
    sampler = inc.FactorSamplerJerarquico(sigma_g=ver.sigma_g, sigma_liga=ver.sigma_liga)
    r = dep.probabilidad_conjunta(_piernas_del_ticket(t, pool, ver), sampler, cfg_v3, 11)
    assert abs(r.p_media - t.p_media) <= 4 * np.hypot(r.error_mc, t.error_mc)            # media: dentro de 4 σ de MC
    assert r.p_conservadora == pytest.approx(t.p_conservadora, rel=0.05)
    assert r.p_independencia == pytest.approx(t.p_independencia, rel=1e-12)              # la determinista SÍ es idéntica


def test_sin_sesgo_sistematico_entre_la_ruta_de_calibracion_y_la_de_tickets():
    """Promedio sobre semillas de la p_conservadora por ambas rutas: la diferencia media es ruido, no sesgo."""
    cfg_v3, pool, ver = _cfg(), _pool(), _version()
    salida, _, _ = bm.decidir(pool, {}, ver, cfg_v3, semilla=11, ahora=AHORA)
    piernas = _piernas_del_ticket(salida.ticket, pool, ver)
    sampler = inc.FactorSamplerJerarquico(sigma_g=ver.sigma_g, sigma_liga=ver.sigma_liga)
    ligas = sorted({p.liga for p in piernas})
    A, B = [], []
    for s in range(1, 25):
        A.append(dep.probabilidad_conjunta(piernas, sampler, cfg_v3, s).p_conservadora)
        f = dep.factores_compartidos(sampler, ligas, int(cfg_v3["dependencia"]["n_draws"]), s)
        pares = [dep.p_pierna_sorteos(p, f, s) for p in piernas]
        B.append(dep.resumir_conjunta([a for a, _ in pares], [pi for _, pi in pares], cfg_v3, s).p_conservadora)
    d = np.array(A) - np.array(B)
    assert abs(d.mean()) <= 3 * d.std(ddof=1) / np.sqrt(len(d))                         # media de diferencias ≈ 0 (z < 3)


def test_la_p_conservadora_difiere_del_producto_de_independencia():
    """El defecto que se corrige: con dependencia, la conjunta NO es el producto. (Si fueran iguales no habría nada que integrar.)"""
    salida, _, _ = bm.decidir(_pool(), {}, _version(), _cfg(), semilla=11, ahora=AHORA)
    t = salida.ticket
    prod = float(np.prod([p.p_calibrada for p in t.piernas]))
    assert t.p_conservadora != pytest.approx(prod, rel=1e-6) and t.lift != pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("punto", ["p_pierna_sorteos", "resumir_conjunta", "factores_compartidos"])
def test_si_dependencia_falla_el_resultado_es_abstencion_no_independencia(monkeypatch, punto):
    def roto(*a, **k):
        raise RuntimeError("dependencia rota")
    monkeypatch.setattr(dep, punto, roto)
    salida, veredicto, error = bm.decidir(_pool(), {}, _version(), _cfg(), semilla=11, ahora=AHORA)
    assert salida is None and veredicto is None and "RuntimeError" in error and "dependencia rota" in error
    d = bm.DecisionV3(AHORA.date(), AHORA, None, salida, veredicto, error)
    assert d.estado == "ABSTENCION" and d.ticket is None                       # y NO hay ticket por independencia


def test_el_fallo_de_dependencia_no_deja_pasar_un_ticket_calculado_con_el_producto(monkeypatch):
    """Aunque dependencia falle, si alguien hubiera puesto un respaldo con math.prod habría ticket: no debe haberlo."""
    monkeypatch.setattr(dep, "resumir_conjunta", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    salida, veredicto, error = bm.decidir(_pool(), {}, _version(), _cfg(), semilla=11, ahora=AHORA)
    assert (salida, veredicto) == (None, None) and error.startswith("ValueError")


def test_sin_version_de_calibrador_es_abstencion_con_error_explicito():
    salida, veredicto, error = bm.decidir(_pool(), {}, None, _cfg(), semilla=11, ahora=AHORA)
    assert salida is None and veredicto is None and "ValueError" in error


def test_modo_experimental_viene_de_la_config_y_el_ticket_nunca_es_aprobado():
    cfg_v3 = _cfg()
    assert cfg_v3["modo_experimental"] is True
    salida, veredicto, _ = bm.decidir(_pool(), {}, _version(), cfg_v3, semilla=11, ahora=AHORA)
    assert salida.ticket.modo_experimental is True and salida.ticket.techo_estado == "CON_RESERVAS"
    assert salida.estado == "PROPUESTO" and veredicto.estado != "APROBADO"


def test_la_bandera_no_se_apaga_sin_oos_real():
    cfg_v3 = _cfg()
    cfg_v3["modo_experimental"] = False
    salida, veredicto, error = bm.decidir(_pool(), {}, _version(con="sinteticos"), cfg_v3, semilla=11, ahora=AHORA)
    assert salida is None and "oos_real" in error                              # el candado de tickets.py se respeta


def test_sin_candidatas_es_abstencion_no_error():
    salida, veredicto, error = bm.decidir([], {}, _version(), _cfg(), semilla=11, ahora=AHORA)
    assert error is None and salida.estado == "ABSTENCION" and salida.ticket is None and veredicto is None
    assert bm.DecisionV3(AHORA.date(), AHORA, None, salida, veredicto, error).estado == "ABSTENCION"


def test_version_provisional_nunca_se_declara_calibrada():
    v = bm.version_provisional(_cfg(), AHORA)
    assert v.calibrado_con == "no_calibrado" and v.segmentos_calibrados == []


# ------------------------------------------------------------------ con PostgreSQL real

@pytest.fixture(scope="module")
def bd(poblada):  # noqa: F811
    with db.conectar(poblada) as c:
        aplicar(c)                                        # la 004 llega por el runner real
    return poblada


@pytest.fixture
def con(bd):
    c = db.conectar(bd)
    yield c
    c.rollback()
    c.close()


def test_bd_real_decision_end_to_end_ejecuta_tickets_dependencia_y_es_coherente(con, cfg, v3):  # noqa: F811
    d = bm.generar_decision_v3(con, _dia(con), AHORA_BD, cfg, v3)
    assert d.error is None and d.salida is not None and d.candidatas.candidatas
    assert d.salida.auditoria.n_candidatas == len(d.candidatas.candidatas)
    assert d.salida.auditoria.modo_experimental is True and d.salida.auditoria.calibrado_con == "no_calibrado"
    assert d.estado in {"ABSTENCION", "CON_RESERVAS", "EN_OBSERVACION", "RECHAZADO"} and d.estado != "APROBADO"
    if d.ticket is None:
        assert d.salida.auditoria.motivo_abstencion


def test_bd_real_es_reproducible(con, cfg, v3):  # noqa: F811
    a = bm.generar_decision_v3(con, _dia(con), AHORA_BD, cfg, v3)
    b = bm.generar_decision_v3(con, _dia(con), AHORA_BD, cfg, v3)
    assert _json(a.salida) == _json(b.salida) and a.estado == b.estado and a.candidatas == b.candidatas


def test_bd_real_persistir_guarda_la_decision_y_el_veredicto(con, cfg, v3):  # noqa: F811
    d = bm.generar_decision_v3(con, _dia(con), AHORA_BD, cfg, v3)
    ids = bm.persistir(con, d)
    assert "tickets" in ids
    fila = con.execute("SELECT componente, estado, modo_experimental, calibrado_con FROM auditoria_decisiones WHERE id = %s",
                       (ids["tickets"],)).fetchone()
    assert fila["componente"] == "tickets" and fila["modo_experimental"] is True and fila["calibrado_con"] == "no_calibrado"
    assert ("cerebro2" in ids) == (d.ticket is not None)


# ------------------------------------------------------------------ mercado para Cerebro 2 (cuotas crudas, sin devig, as-of)

def _cands(con, cfg, v3, ahora=AHORA_BD):
    from golmodel.integracion.candidatas import construir_candidatas
    return construir_candidatas(con, _dia(con), ahora, cfg, v3).candidatas


def test_mercado_trae_cuotas_crudas_del_grupo_completo_de_las_casas_elegibles(con, cfg, v3):  # noqa: F811
    from golmodel.modelos.mercados import GRUPO_DE, GRUPOS
    cands = _cands(con, cfg, v3)
    m = bm.mercado_de_candidatas(con, cands, AHORA_BD, cfg, v3)
    elegibles = set(v3["cerebro2"]["consenso_mercado"]["casas_elegibles"])
    assert m and set(m) == {(c.partido_id, tuple(c.selecciones)) for c in cands}
    for (pid, sels), mp in m.items():
        grupo = set(GRUPOS[GRUPO_DE[sels[0]]])
        assert mp.cuotas_por_casa and set(mp.cuotas_por_casa) <= elegibles
        assert tuple(mp.casas_esperadas) == tuple(v3["cerebro2"]["consenso_mercado"]["casas_elegibles"])
        for casa, d in mp.cuotas_por_casa.items():
            assert set(d) == grupo                                        # el grupo COMPLETO de cada casa (para el devig)
            for s, q in d.items():                                        # y son las cuotas crudas de la BD
                bd = con.execute("""SELECT q.cuota FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id WHERE q.partido_id = %s
                                    AND c.clave = %s AND q.seleccion = %s AND q.tipo IN ('apertura','actual')
                                    ORDER BY q.capturada_en DESC LIMIT 1""", (pid, casa, s)).fetchone()
                assert q == pytest.approx(float(bd["cuota"]))


def test_mercado_omite_las_piernas_sgp_y_no_inventa_cuotas(con, cfg, v3):  # noqa: F811
    cands = _cands(con, cfg, v3)
    pid = cands[0].partido_id
    casa = con.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (cfg["mercado"]["casa_ejecucion"],)).fetchone()["id"]
    con.execute("INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente, capturada_en) "
                "VALUES (%s, %s, '1X2:1', 'OU:2.5:over', 3.4, 'manual', %s)", (pid, casa, AHORA_BD))
    cands2 = _cands(con, cfg, v3)
    m = bm.mercado_de_candidatas(con, cands2, AHORA_BD, cfg, v3)
    assert any(len(c.selecciones) == 2 for c in cands2)
    assert not any(len(k[1]) == 2 for k in m)                             # el SGP no tiene grupo de devig: sin entrada


def test_mercado_con_cuotas_vencidas_queda_vacio_no_se_usan_cuotas_viejas(con, cfg, v3):  # noqa: F811
    cands = _cands(con, cfg, v3)
    tarde = AHORA_BD + timedelta(hours=5, minutes=30)                     # sigue antes del inicio, pero las cuotas ya vencieron
    m = bm.mercado_de_candidatas(con, cands, tarde, cfg, v3)
    assert all(not mp.cuotas_por_casa for mp in m.values())


def test_mercado_no_usa_cuotas_posteriores_a_ahora(con, cfg, v3):  # noqa: F811
    cands = _cands(con, cfg, v3)
    c0 = next(c for c in cands if len(c.selecciones) == 1)
    base = bm.mercado_de_candidatas(con, cands, AHORA_BD, cfg, v3)
    casa = con.execute("SELECT id FROM casas_apuestas WHERE clave = 'pinnacle'").fetchone()["id"]
    con.execute("INSERT INTO cuotas (partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote) "
                "VALUES (%s, %s, %s, 55.0, 'actual', 'manual', %s, gen_random_uuid())",
                (c0.partido_id, casa, c0.selecciones[0], AHORA_BD + timedelta(minutes=30)))
    assert bm.mercado_de_candidatas(con, cands, AHORA_BD, cfg, v3) == base


def test_la_semilla_por_defecto_es_la_fecha_y_la_decision_es_reproducible_con_ella(con, cfg, v3):  # noqa: F811
    dia = _dia(con)
    a = bm.generar_decision_v3(con, dia, AHORA_BD, cfg, v3)
    b = bm.generar_decision_v3(con, dia, AHORA_BD, cfg, v3, semilla=int(dia.strftime("%Y%m%d")))
    assert _json(a.salida) == _json(b.salida)
    assert a.salida.auditoria.semilla == int(dia.strftime("%Y%m%d"))


# ------------------------------------------------------------------ cobertura de las ramas con ticket (la BD sintética casi siempre se abstiene)

def test_el_estado_final_es_el_del_auditor_cuando_hay_ticket():
    cfg_v3, pool, ver = _cfg(), _pool(), _version()
    salida, veredicto, error = bm.decidir(pool, {}, ver, cfg_v3, semilla=11, ahora=AHORA)
    d = bm.DecisionV3(AHORA.date(), AHORA, None, salida, veredicto, error)
    assert veredicto is not None and d.ticket is not None
    assert d.estado == veredicto.estado and d.estado != "ABSTENCION" and d.estado != "APROBADO"


def test_persistir_con_ticket_guarda_decision_de_tickets_y_veredicto_de_cerebro2(con):
    cfg_v3, pool, ver = _cfg(), _pool(), _version()
    salida, veredicto, error = bm.decidir(pool, {}, ver, cfg_v3, semilla=11, ahora=AHORA)
    d = bm.DecisionV3(AHORA.date(), AHORA, None, salida, veredicto, error)
    ids = bm.persistir(con, d)
    assert set(ids) == {"tickets", "cerebro2"}
    filas = {r["componente"]: r for r in con.execute(
        "SELECT componente, estado, ticket_id FROM auditoria_decisiones WHERE id = ANY(%s)", (list(ids.values()),))}
    assert filas["tickets"]["estado"] == "PROPUESTO" and filas["cerebro2"]["estado"] == veredicto.estado
    assert filas["tickets"]["ticket_id"] == filas["cerebro2"]["ticket_id"]               # los une el ticket_id determinista
    assert con.execute("SELECT count(*) AS n FROM evaluaciones_riesgo WHERE auditoria_id = %s", (ids["cerebro2"],)).fetchone()["n"] > 0


def test_persistir_una_decision_con_error_no_escribe_nada(con):
    d = bm.DecisionV3(AHORA.date(), AHORA, None, None, None, "RuntimeError: x")
    antes = con.execute("SELECT count(*) AS n FROM auditoria_decisiones").fetchone()["n"]
    assert bm.persistir(con, d) == {}
    assert con.execute("SELECT count(*) AS n FROM auditoria_decisiones").fetchone()["n"] == antes


def test_version_provisional_respeta_sigma_g_y_sigma_liga_de_la_config():
    cfg_v3 = _cfg()
    cfg_v3["calibracion_tickets"]["version_provisional"].update({"c_global": 0.7, "sigma_g": 0.3, "sigma_liga": {"EPL": 0.2}})
    v = bm.version_provisional(cfg_v3, AHORA)
    assert (v.c_global, v.sigma_g, v.sigma_liga) == (0.7, 0.3, {"EPL": 0.2}) and v.calibrado_con == "no_calibrado"
