"""Cerebro 2: cada regla del diseño (docs/v3_cerebro2_diseno.md v0.4) tiene aquí su prueba falsable."""
import dataclasses
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest
import yaml

from golmodel.cerebro2 import Estado, auditar_seleccion, auditar_ticket, cargar_politica
from golmodel.cerebro2 import auditoria, metacalibracion
from golmodel.cerebro2.benchmark_mercado import Benchmark, consenso
from golmodel.cerebro2.estadistica import jensen_shannon_bits, wilson
from golmodel.cerebro2.politica import RUTA_UMBRALES, desde_dicts
from golmodel.cerebro2.riesgo import EntradaSeleccion, EntradaTicket, EstadisticaSegmento
from golmodel.config import cargar
from golmodel.modelos import devig

RAIZ = Path(__file__).resolve().parents[1]
AHORA = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
CFG = cargar()
POL = cargar_politica(CFG)
POL_VIGENTE = dataclasses.replace(POL, modo_experimental=False)


def _bench(p=(0.52, 0.26, 0.22), auditable=True):
    return Benchmark("1X2", dict(zip(("1X2:1", "1X2:X", "1X2:2"), p)) if auditable else None,
                     ("bet365", "marathon", "pinnacle"), AHORA - timedelta(minutes=20), auditable, "test", "potencia")


def _sel(**kw):
    base = dict(partido_id=1, liga="MLS", seleccion="1X2:1", p_modelo_calibrada=0.55, p_conservadora=0.50,
                cuota_ejecutable=2.2, info_hasta=AHORA - timedelta(hours=1),
                cuota_capturada_en=AHORA - timedelta(minutes=10), crs_alerta="BAJA", benchmark=_bench(),
                calibracion=EstadisticaSegmento("MLS|1X2|0.55", 400, 230, 0.55), alineacion_confirmada=True)
    base.update(kw)
    return EntradaSeleccion(**base)


# ------------------------------------------------------------------ política

def test_politica_sale_del_config_versionado():
    assert POL.modo_experimental is True
    assert POL.metodo_devig == "potencia" and POL.n_min_casas == 3
    assert POL.d_mm_edge_max == 0.10 and POL.d_mm_rechazo == 0.20
    assert POL.hash == cargar_politica(CFG).hash                 # reproducible
    assert POL.hash != POL_VIGENTE.hash                          # cualquier cambio de política cambia el hash


def test_politica_rechaza_umbrales_incoherentes_y_devig_igual_a_c1():
    v3 = yaml.safe_load(RUTA_UMBRALES.read_text(encoding="utf-8"))
    malo = json.loads(json.dumps(v3))
    malo["umbrales_provisionales"]["d_mm_edge_max_pp"] = 0.30
    with pytest.raises(ValueError):
        desde_dicts(malo, CFG)
    circular = json.loads(json.dumps(v3))
    circular["cerebro2"]["consenso_mercado"]["metodo_devig"] = "power"
    cfg_c1_power = {**CFG, "mercado": {**CFG["mercado"], "metodo_devig": "potencia"}}
    with pytest.raises(ValueError, match="anti-circularidad"):
        desde_dicts(circular, cfg_c1_power)


# ------------------------------------------------------------------ benchmark de mercado

def _cuotas(casas, ts, cuotas=(2.0, 3.5, 4.0), rol="ambos"):
    filas = []
    for i, casa in enumerate(casas):
        for s, c in zip(("1X2:1", "1X2:X", "1X2:2"), cuotas):
            filas.append({"casa": casa, "seleccion": s, "cuota": c + 0.05 * i, "capturada_en": ts, "rol": rol})
    return pd.DataFrame(filas)


def test_consenso_tres_casas_mediana_power_renormalizada():
    ts = AHORA - timedelta(minutes=30)
    b = consenso(_cuotas(["pinnacle", "bet365", "marathon"], ts), "1X2:1", POL, AHORA)
    assert b.auditable and b.casas == ("bet365", "marathon", "pinnacle")
    esperado = np.median(np.vstack([devig.potencia([2.0 + 0.05 * i, 3.5 + 0.05 * i, 4.0 + 0.05 * i])
                                    for i in range(3)]), axis=0)
    esperado /= esperado.sum()
    assert b.p_de("1X2:1") == pytest.approx(esperado[0])
    assert sum(b.p.values()) == pytest.approx(1.0)
    assert b.p_de("DC:1X") == pytest.approx(esperado[0] + esperado[1])


def test_consenso_no_auditable_con_dos_casas_agregados_o_casas_no_elegibles():
    ts = AHORA - timedelta(minutes=30)
    assert not consenso(_cuotas(["pinnacle", "bet365"], ts), "1X2:1", POL, AHORA).auditable
    d = pd.concat([_cuotas(["pinnacle", "bet365"], ts), _cuotas(["williamhill"], ts, rol="agregado"),
                   _cuotas(["casa_rara"], ts)])
    b = consenso(d, "1X2:1", POL, AHORA)
    assert not b.auditable and "2 casas" in b.motivo


def test_consenso_ignora_cuotas_posteriores_a_info_hasta_y_usa_la_ultima_captura_valida():
    vieja, futura = AHORA - timedelta(hours=2), AHORA + timedelta(minutes=5)
    d = pd.concat([_cuotas(["pinnacle", "bet365", "marathon"], vieja),
                   _cuotas(["pinnacle", "bet365", "marathon", "williamhill"], futura, cuotas=(1.5, 4.0, 6.0))])
    b = consenso(d, "1X2:1", POL, AHORA)
    assert b.auditable and len(b.casas) == 3
    assert pd.Timestamp(b.capturada_en) == pd.Timestamp(vieja)


# ------------------------------------------------------------------ escalera de estados

def test_todo_ok_queda_con_reservas_en_modo_experimental_y_aprobado_fuera_de_el():
    d = auditar_seleccion(_sel(), AHORA, POL)
    assert d.estado is Estado.CON_RESERVAS and d.tope_experimental and d.gate_determinante == "modo_experimental"
    d2 = auditar_seleccion(_sel(), AHORA, POL_VIGENTE)
    assert d2.estado is Estado.APROBADO and not d2.tope_experimental


def test_falta_p_conservadora_es_abstencion_y_no_corre_nada_mas():
    d = auditar_seleccion(_sel(p_conservadora=None), AHORA, POL_VIGENTE)
    assert d.estado is Estado.ABSTENCION and d.gate_determinante == "inputs" and len(d.gates) == 1


@pytest.mark.parametrize("info_hasta", [AHORA - timedelta(days=4), AHORA + timedelta(minutes=1)])
def test_info_vieja_o_look_ahead_es_abstencion(info_hasta):
    d = auditar_seleccion(_sel(info_hasta=info_hasta), AHORA, POL_VIGENTE)
    assert d.estado is Estado.ABSTENCION and d.gate_determinante == "frescura"


def test_ev_conservador_no_positivo_es_rechazo():
    d = auditar_seleccion(_sel(cuota_ejecutable=1.9), AHORA, POL_VIGENTE)      # 0.50·1.9 − 1 < 0
    assert d.estado is Estado.RECHAZADO and d.gate_determinante == "ev_cons_pierna"


def test_crs_veto_rechaza_y_alerta_alta_deja_con_reservas():
    assert auditar_seleccion(_sel(crs_alerta="ALTA", crs_selecciones_vetadas=("1X2:1",)), AHORA,
                             POL_VIGENTE).estado is Estado.RECHAZADO
    d = auditar_seleccion(_sel(crs_alerta="ALTA"), AHORA, POL_VIGENTE)
    assert d.estado is Estado.CON_RESERVAS and d.gate_determinante == "crs"


def test_mercado_no_auditable_topa_en_con_reservas_y_nunca_rechaza():
    d = auditar_seleccion(_sel(benchmark=None, p_modelo_calibrada=0.95), AHORA, POL_VIGENTE)
    assert d.estado is Estado.CON_RESERVAS and d.gate_determinante == "mercado"
    g = next(g for g in d.gates if g.nombre == "mercado")
    assert g.detalle["gate_mercado"] == "no_auditable"


@pytest.mark.parametrize("p_cal,estado", [(0.60, Estado.APROBADO), (0.67, Estado.CON_RESERVAS),
                                          (0.75, Estado.RECHAZADO), (0.30, Estado.RECHAZADO)])
def test_banda_de_discrepancia_modelo_mercado(p_cal, estado):
    cal = EstadisticaSegmento("s", 400, int(400 * p_cal), p_cal)
    d = auditar_seleccion(_sel(p_modelo_calibrada=p_cal, calibracion=cal), AHORA, POL_VIGENTE)
    assert d.estado is estado


def test_gana_la_etapa_mas_severa_ev_falla_mas_alineacion_pendiente():
    d = auditar_seleccion(_sel(cuota_ejecutable=1.9, alineacion_confirmada=False), AHORA, POL_VIGENTE)
    assert d.estado is Estado.RECHAZADO


def test_calibracion_del_segmento():
    sin = auditar_seleccion(_sel(calibracion=None), AHORA, POL_VIGENTE)
    assert sin.estado is Estado.ABSTENCION and sin.gate_determinante == "calibracion_segmento"
    fina = auditar_seleccion(_sel(calibracion=EstadisticaSegmento("s", 50, 28, 0.55)), AHORA, POL_VIGENTE)
    assert fina.estado is Estado.EN_OBSERVACION
    # El Cerebro 1 promete 55% y en 300 casos acierta 40%: sobreconfianza significativa → rechazo
    mala = auditar_seleccion(_sel(calibracion=EstadisticaSegmento("s", 300, 120, 0.55)), AHORA, POL_VIGENTE)
    assert mala.estado is Estado.RECHAZADO and mala.gate_determinante == "calibracion_segmento"


def test_pendientes_resolubles_dejan_en_observacion():
    vieja = auditar_seleccion(_sel(cuota_capturada_en=AHORA - timedelta(minutes=90)), AHORA, POL_VIGENTE)
    assert vieja.estado is Estado.EN_OBSERVACION and vieja.gate_determinante == "cuota_vigente"
    for alineacion in (False, None):
        assert auditar_seleccion(_sel(alineacion_confirmada=alineacion), AHORA,
                                 POL_VIGENTE).estado is Estado.EN_OBSERVACION
    assert auditar_seleccion(_sel(crs_alerta="INSUFICIENTE"), AHORA, POL_VIGENTE).estado is Estado.EN_OBSERVACION


def test_divergencia_entre_modelos_jensen_shannon():
    assert jensen_shannon_bits([1, 0], [0, 1]) == pytest.approx(1.0)
    assert jensen_shannon_bits([0.5, 0.3, 0.2], [0.5, 0.3, 0.2]) == pytest.approx(0.0)
    parecidos = {"dixon_coles": [0.50, 0.27, 0.23], "mercado": [0.52, 0.26, 0.22]}
    lejanos = {"dixon_coles": [0.80, 0.15, 0.05], "mercado": [0.20, 0.25, 0.55]}
    assert auditar_seleccion(_sel(distribuciones_modelos=parecidos), AHORA, POL_VIGENTE).estado is Estado.APROBADO
    d = auditar_seleccion(_sel(distribuciones_modelos=lejanos), AHORA, POL_VIGENTE)
    assert d.estado is Estado.CON_RESERVAS and d.gate_determinante == "discrepancia_modelos"


def test_error_de_computo_es_abstencion_fail_closed():
    roto = {"a": [0.5, 0.5], "b": [0.3, 0.3, 0.4]}                 # formas distintas → excepción
    d = auditar_seleccion(_sel(distribuciones_modelos=roto), AHORA, POL_VIGENTE)
    assert d.estado is Estado.ABSTENCION and d.gate_determinante == "error_computo"


def test_determinista():
    a = auditar_seleccion(_sel(crs_alerta="ALTA"), AHORA, POL).a_json()
    b = auditar_seleccion(_sel(crs_alerta="ALTA"), AHORA, POL).a_json()
    assert a == b


# ------------------------------------------------------------------ tickets

def _ticket(piernas=None, **kw):
    piernas = piernas or (_sel(partido_id=1, liga="MLS"), _sel(partido_id=2, liga="EPL"),
                          _sel(partido_id=3, liga="SA"))
    base = dict(piernas=tuple(piernas), p_conservadora=0.13, cuota_combinada=10.0, lift=1.02, ic_ancho_rel=0.6)
    base.update(kw)
    return EntradaTicket(**base)


def test_ticket_limpio_y_restricciones_duras():
    assert auditar_ticket(_ticket(), AHORA, POL_VIGENTE).estado is Estado.APROBADO
    assert auditar_ticket(_ticket(), AHORA, POL).estado is Estado.CON_RESERVAS
    casos = {"ev_cons_ticket": dict(p_conservadora=0.10),          # 0.10·10 − 1 = 0 ≤ 0.05
             "ic": dict(ic_ancho_rel=1.3), "lift": dict(lift=1.2)}
    for gate, kw in casos.items():
        d = auditar_ticket(_ticket(**kw), AHORA, POL_VIGENTE)
        assert d.estado is Estado.RECHAZADO and d.gate_determinante == gate


def test_ticket_concentracion_por_partido_y_por_liga():
    mismo_partido = (_sel(partido_id=1), _sel(partido_id=1, seleccion="1X2:X"), _sel(partido_id=3, liga="SA"))
    assert auditar_ticket(_ticket(mismo_partido), AHORA, POL_VIGENTE).gate_determinante == "concentracion"
    tres_mls = tuple(_sel(partido_id=i, liga="MLS") for i in (1, 2, 3))
    d = auditar_ticket(_ticket(tres_mls), AHORA, POL_VIGENTE)
    assert d.estado is Estado.RECHAZADO and d.gate_determinante == "concentracion"


def test_ticket_hereda_la_peor_pierna():
    piernas = (_sel(partido_id=1), _sel(partido_id=2, liga="EPL", p_conservadora=None),
               _sel(partido_id=3, liga="SA"))
    d = auditar_ticket(_ticket(piernas), AHORA, POL_VIGENTE)
    assert d.estado is Estado.ABSTENCION and d.gate_determinante == "pierna[1]:inputs"


def test_ticket_sin_p_conjunta_se_abstiene():
    d = auditar_ticket(_ticket(p_conservadora=None, lift=None), AHORA, POL_VIGENTE)
    assert d.estado is Estado.ABSTENCION and d.gate_determinante == "inputs_ticket"


# ------------------------------------------------------------------ metacalibración del auditor

def test_wilson_contiene_la_proporcion():
    lo, hi = wilson(60, 100, 0.95)
    assert lo < 0.6 < hi and lo == pytest.approx(0.502, abs=1e-3)


def test_metacalibracion_rechazos_correctos_y_alarmas():
    rng = np.random.default_rng(1)
    n = 300
    rech = pd.DataFrame({"estado": "RECHAZADO", "cuota": 2.0, "clv": rng.normal(-0.03, 0.02, n),
                         "resultado": np.where(rng.random(n) < 0.7, "pierde", "gana")})
    apos = pd.DataFrame({"estado": "CON_RESERVAS", "cuota": 2.0, "p_conservadora": 0.5,
                         "clv": rng.normal(0.02, 0.02, n), "resultado": np.where(rng.random(n) < 0.55, "gana", "pierde")})
    absten = pd.DataFrame({"estado": ["ABSTENCION"] * 5, "cuota": 2.0, "resultado": "gana"})
    nulos = pd.DataFrame({"estado": ["RECHAZADO"] * 3, "cuota": 2.0, "resultado": "nulo"})
    inf = metacalibracion.evaluar(pd.concat([rech, apos, absten, nulos], ignore_index=True), POL)
    assert inf.rechazo_por_falta_de_auditoria == 5
    assert inf.tasa_rechazos_correctos["n"] == n                 # los nulos y abstenciones no cuentan
    assert inf.tasa_rechazos_correctos["cumple"] and inf.valor_auditor_clv["positivo"]
    assert inf.cobertura_apostables["cumple"] and inf.alarmas == []

    filtra_de_mas = rech.assign(resultado=np.where(rng.random(n) < 0.8, "gana", "pierde"))
    inf2 = metacalibracion.evaluar(filtra_de_mas, POL)
    assert any(a.startswith("U9") for a in inf2.alarmas) and any(a.startswith("U10") for a in inf2.alarmas)


# ------------------------------------------------------------------ registro inmutable (PostgreSQL)

ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")


@pytest.fixture(scope="module")
def conn():
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_c2_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    from psycopg.rows import dict_row
    c = psycopg.connect(ADMIN.replace("/postgres?", f"/{nombre}?"), autocommit=True, row_factory=dict_row)
    c.execute((RAIZ / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    from golmodel import migraciones
    assert "005_cerebro2.sql" in migraciones.aplicar(c)
    yield c
    c.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def test_auditoria_se_guarda_completa_y_es_inmutable(conn):
    t = _ticket()
    d = auditar_ticket(t, AHORA, POL)
    i = auditoria.guardar(conn, auditoria.registro(d, t, POL, AHORA))
    fila = conn.execute("SELECT * FROM auditoria_decisiones WHERE id = %s", (i,)).fetchone()
    assert fila["estado"] == "CON_RESERVAS" and fila["gate_determinante"] == "modo_experimental"
    assert fila["politica_hash"] == POL.hash and fila["cerebro2_version"].startswith("cerebro2_")
    assert [g["gate"] for g in fila["decision"]["gates"]][:2] == ["inputs_ticket", "ev_cons_ticket"]
    assert len(fila["inputs"]["piernas"]) == 3
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute("UPDATE auditoria_decisiones SET estado = 'APROBADO' WHERE id = %s", (i,))


def test_esquema_prohibe_aprobado_en_modo_experimental(conn):
    t = _ticket()
    reg = auditoria.registro(auditar_ticket(t, AHORA, POL_VIGENTE), t, POL_VIGENTE, AHORA)
    assert reg["estado"] == "APROBADO"
    auditoria.guardar(conn, reg)                                  # fuera de modo experimental sí se permite
    with pytest.raises(psycopg.errors.CheckViolation):
        auditoria.guardar(conn, {**reg, "modo_experimental": True})
