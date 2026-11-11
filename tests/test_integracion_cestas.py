"""integracion/cestas_historicas.py — cestas ex-post walk-forward (decisión 5 del diseño de incertidumbre).

Verificado con datos SINTÉTICOS (liga simulada con parámetros conocidos). Los tests de leakage comparan la salida contra
una copia del mundo con el FUTURO (o el pasado) alterado: si el futuro no cambia λ/ρ/pesos/cuotas, no hay leakage; si el
pasado sí los cambia, el test es sensible (no es vacuo).
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from golmodel.backtest.sintetico import generar_liga
from golmodel.config import cargar
from golmodel.integracion import cestas_historicas as ch
from golmodel.modelos.calibracion_mercado import segmento_de
from golmodel.modelos.mercados import APTAS_PARLAY
from tests.test_integracion import cuotas_ref
from tests.test_tickets import _cfg

INICIO = datetime(2024, 8, 3, 15, 0, tzinfo=timezone.utc)
DF, _ = generar_liga(liga="EPL", n_equipos=16, temporadas=2, semilla=5, inicio=INICIO, deriva_semanal=0.01)
FECHAS = sorted(DF["inicio"].unique())
T0 = pd.Timestamp(FECHAS[len(FECHAS) - 8]).to_pydatetime()          # ~8 jornadas antes del final
T1 = T0 + timedelta(days=28)                                        # cuatro ventanas semanales


def _mundo():
    """(df_modelo, pool, cuotas) sintéticos: todos finalizados; cuota Pinnacle capturada 2 días antes del inicio."""
    d = DF.copy()
    antiguos = d[pd.to_datetime(d["inicio"], utc=True) < T0 - timedelta(days=40)]       # solo el pasado lejano: los días de la
    d = d.drop(antiguos.sample(frac=0.25, random_state=1).index).reset_index(drop=True)  # ventana conservan sus 8 partidos, y los
    #                                                                                     pesos efectivos quedan DISTINTOS por equipo
    d["partido_id"] = np.arange(1, len(d) + 1)
    d["estado"] = "finalizado"
    d["inicio"] = pd.to_datetime(d["inicio"], utc=True)
    filas = []
    for r in d.itertuples():
        for sel, c in cuotas_ref(r).items():
            filas.append({"partido_id": r.partido_id, "seleccion": sel, "cuota": float(c),
                          "capturada_en": r.inicio - pd.Timedelta(days=2), "id": len(filas) + 1})
    cu = pd.DataFrame(filas)
    cu["capturada_en"] = pd.to_datetime(cu["capturada_en"], utc=True)
    pool = d[(d["inicio"] >= T0) & (d["inicio"] < T1)].copy()
    return d, pool, cu


@pytest.fixture(scope="module")
def cfgs():
    v2 = cargar()
    v3 = _cfg()
    v3["calibracion_tickets"]["cestas_historicas"].update({"paso_dias": 7, "n_cestas_por_dia": 40})
    return v2, v3


@pytest.fixture(scope="module")
def mundo():
    return _mundo()


@pytest.fixture(scope="module")
def base(mundo, cfgs):
    d, pool, cu = mundo
    return ch.construir_cestas(d, pool, cu, *cfgs, desde=T0, hasta=T1, semilla=11)


def _run(mundo, cfgs, *, d=None, pool=None, cu=None, desde=T0, hasta=T1, semilla=11):
    d0, p0, c0 = mundo
    return ch.construir_cestas(d0 if d is None else d, p0 if pool is None else pool, c0 if cu is None else cu, *cfgs,
                               desde=desde, hasta=hasta, semilla=semilla)


def _piernas(res):
    return [[(p.liga, p.segmento, p.lambda_local, p.lambda_visitante, p.rho, p.selecciones, p.peso_efectivo_local,
              p.peso_efectivo_visitante) for p in c.piernas] for c in res.cestas]


def _piernas_de(c):
    return [(p.liga, p.segmento, p.lambda_local, p.lambda_visitante, p.rho, p.selecciones, p.peso_efectivo_local,
             p.peso_efectivo_visitante) for p in c.piernas]


def _acierta(sel: str, gl: int, gv: int) -> bool:
    """Evaluador manual e INDEPENDIENTE del de producción (solo 1X2 y OU 2.5, que son las del mundo)."""
    if sel == "1X2:1": return gl > gv
    if sel == "1X2:X": return gl == gv
    if sel == "1X2:2": return gl < gv
    if sel == "OU:2.5:over": return gl + gv > 2.5
    if sel == "OU:2.5:under": return gl + gv < 2.5
    raise AssertionError(sel)


# ------------------------------------------------------------------ contrato

def test_hay_cestas_y_cada_una_cumple_el_contrato(base, cfgs, mundo):
    v3 = cfgs[1]
    assert len(base.cestas) == len(base.meta) >= 120
    tam = set(range(v3["tickets"]["tamano_alta_min"], v3["tickets"]["tamano_alta_max"] + 1))
    assert tam == {5, 6, 7, 8} and {m.n for m in base.meta} == tam                  # solo los tamaños de producción (5-8); nunca 2-4
    for c, m in zip(base.cestas, base.meta):
        assert len(c.piernas) == m.n == len(m.piernas) and m.n in tam
        assert len({pm.partido_id for pm in m.piernas}) == m.n                      # partidos distintos: máx. 1 pierna/partido
        for ph, pm in zip(c.piernas, m.piernas):
            assert ph.lambda_local > 0 and ph.lambda_visitante > 0 and ph.peso_efectivo_local > 0 and ph.peso_efectivo_visitante > 0
            assert set(ph.selecciones) <= APTAS_PARLAY and ph.selecciones == pm.selecciones
            assert ph.segmento == segmento_de(ph.liga, ph.selecciones, pm.cuota, cfgs[1])
            assert 0.0 < pm.p_modelo < 1.0 and pm.cuota > 1.0
        assert isinstance(c.gano, bool)


def test_gano_coincide_con_un_calculo_manual_independiente(base, mundo):
    _, pool, _ = mundo
    res = {int(r.partido_id): (int(r.goles_local), int(r.goles_visitante)) for r in pool.itertuples()}
    for c, m in zip(base.cestas, base.meta):
        esperado = all(_acierta(s, *res[pm.partido_id]) for pm in m.piernas for s in pm.selecciones)
        assert c.gano is esperado and m.resultado == ("gano" if esperado else "perdio")


# ------------------------------------------------------------------ leakage: solo datos < t

def _primera_ventana(res):
    idx = [i for i, m in enumerate(res.meta) if m.ventana_t == T0]
    return idx, [res.cestas[i] for i in idx]


def test_cambiar_los_resultados_del_futuro_en_el_modelo_no_cambia_nada(mundo, base, cfgs):
    """Para la ventana que corta en T0, TODO lo posterior a T0 es futuro. (Las ventanas siguientes sí ven, legítimamente,
    los resultados de la primera: por eso se compara solo la primera.)"""
    d, _, _ = mundo
    d2 = d.copy()
    fut = d2["inicio"] >= T0
    d2.loc[fut, "goles_local"] = (d2.loc[fut, "goles_local"] * 3 + 7).astype(int)       # resultados absurdos a partir de T0
    d2.loc[fut, "goles_visitante"] = 0
    d2.loc[fut, ["xg_local", "xg_visitante"]] = 9.0
    r2 = _run(mundo, cfgs, d=d2)
    i1, c1 = _primera_ventana(base)
    i2, c2 = _primera_ventana(r2)
    assert i1 and i1 == i2 and len(i1) >= 40
    assert [_piernas_de(c) for c in c2] == [_piernas_de(c) for c in c1]                  # λ, ρ, pesos, segmentos: idénticos
    assert [c.gano for c in c2] == [c.gano for c in c1]                                  # `gano` sale del pool, no del modelo
    assert _piernas(r2) != _piernas(base)                                                # y las ventanas posteriores SÍ cambian


def test_los_resultados_solo_afectan_a_gano_no_a_las_piernas(mundo, base, cfgs):
    _, pool, _ = mundo
    p2 = pool.copy()
    p2["goles_local"] = 0
    p2["goles_visitante"] = 0                                                           # 0-0 en todos los partidos del pool
    r2 = _run(mundo, cfgs, pool=p2)
    assert _piernas(r2) == _piernas(base)
    assert [c.gano for c in r2.cestas] != [c.gano for c in base.cestas]                 # sensibilidad: gano SÍ cambia


def test_piernas_dia_una_por_partido_resuelto_con_la_mejor_pierna_y_el_resultado_correcto(base, mundo):
    """Insumo de la prueba B (v0.14): una pierna por partido, la de mayor p_modelo, con su resultado calculado a mano."""
    _, pool, _ = mundo
    assert len(base.piernas_dia) >= 20
    ids = [x.partido_id for x in base.piernas_dia]
    assert len(ids) == len(set(ids))                                                          # una por partido
    res = {int(r.partido_id): r for r in pool.itertuples()}
    for x in base.piernas_dia:
        r = res[x.partido_id]
        assert len(x.selecciones) == 1 and x.gano == _acierta(x.selecciones[0], int(r.goles_local), int(r.goles_visitante))
        assert 0.0 < x.p_modelo < 1.0 and x.liga == r.liga and len(x.dia) == 10
    # es la de MAYOR p_modelo del partido entre las que se ofrecieron a las cestas
    maximo = {}
    for m in base.meta + base.excluidas:
        for pm in m.piernas:
            maximo[pm.partido_id] = max(maximo.get(pm.partido_id, 0.0), pm.p_modelo)
    assert all(x.p_modelo >= maximo[x.partido_id] - 1e-12 for x in base.piernas_dia if x.partido_id in maximo)


def test_piernas_dia_cambiar_los_resultados_cambia_gano_pero_no_la_pierna_ni_su_p(mundo, base, cfgs):
    _, pool, _ = mundo
    p2 = pool.copy()
    p2["goles_local"] = 0
    p2["goles_visitante"] = 0
    r2 = _run(mundo, cfgs, pool=p2)
    clave = lambda r: [(x.partido_id, x.selecciones, x.p_modelo) for x in r.piernas_dia]      # noqa: E731
    assert clave(r2) == clave(base)                                                           # la elección de la pierna y su p NO miran resultados (sin fuga)
    assert [x.gano for x in r2.piernas_dia] != [x.gano for x in base.piernas_dia]             # sensibilidad: gano SÍ cambia


def test_piernas_dia_no_incluye_partidos_sin_resolver(mundo, cfgs):
    _, pool, _ = mundo
    p2 = pool.copy()
    ids = set(sorted(p2["partido_id"])[:5])
    p2.loc[p2["partido_id"].isin(ids), ["estado", "goles_local", "goles_visitante"]] = ["programado", np.nan, np.nan]
    r = _run(mundo, cfgs, pool=p2)
    assert not ids & {x.partido_id for x in r.piernas_dia}


def test_cambiar_el_pasado_si_cambia_las_piernas_el_test_no_es_vacuo(mundo, base, cfgs):
    d, _, _ = mundo
    d2 = d.copy()
    pas = d2["inicio"] < T0 - timedelta(days=30)
    d2.loc[pas, "goles_local"] = d2.loc[pas, "goles_local"] + 3
    assert _piernas(_run(mundo, cfgs, d=d2)) != _piernas(base)


def test_las_cuotas_posteriores_a_la_decision_no_se_usan(mundo, base, cfgs):
    d, pool, cu = mundo
    t_dia = {m.dia: pd.Timestamp(m.t_dia) for m in base.meta}                     # instante de decisión REAL de cada día
    extra = []
    for r in pool.itertuples():
        dia = r.inicio.tz_convert("America/Mexico_City").strftime("%Y-%m-%d")
        for sel in ("1X2:1", "OU:2.5:over"):                                      # cuotas absurdas 1 minuto DESPUÉS de decidir
            extra.append({"partido_id": r.partido_id, "seleccion": sel, "cuota": 77.0,
                          "capturada_en": t_dia[dia] + pd.Timedelta(minutes=1), "id": 10_000_000 + len(extra)})
    cu2 = pd.concat([cu, pd.DataFrame(extra)], ignore_index=True)
    r2 = _run(mundo, cfgs, cu=cu2)
    assert _piernas(r2) == _piernas(base)
    assert [m.piernas for m in r2.meta] == [m.piernas for m in base.meta]
    assert not any(pm.cuota == 77.0 for m in r2.meta for pm in m.piernas)
    # sensibilidad: la MISMA cuota absurda capturada ANTES de decidir sí se usa
    cu3 = cu2.copy()
    cu3["capturada_en"] = cu3["capturada_en"].where(cu3["cuota"] != 77.0, cu3["capturada_en"] - pd.Timedelta(minutes=2))
    assert any(pm.cuota == 77.0 for m in _run(mundo, cfgs, cu=cu3).meta for pm in m.piernas)


def test_las_cuotas_anteriores_si_se_usan_el_test_no_es_vacuo(mundo, base, cfgs):
    d, pool, cu = mundo
    cu2 = cu.copy()
    cu2.loc[cu2["partido_id"].isin(pool["partido_id"]), "cuota"] *= 1.5                 # misma captura (previa): cambia el valor
    assert _piernas(_run(mundo, cfgs, cu=cu2)) != _piernas(base)


def test_toda_cuota_usada_fue_capturada_antes_del_instante_de_decision_y_el_partido_es_posterior_al_corte(base, mundo):
    _, pool, _ = mundo
    inicio = {int(r.partido_id): r.inicio for r in pool.itertuples()}
    for m in base.meta:
        assert m.ventana_t <= m.t_dia
        for pm in m.piernas:
            assert pm.cuota_capturada_en <= m.t_dia
            assert inicio[pm.partido_id] >= m.ventana_t                                 # se decide ANTES del partido


def test_el_modelo_se_ajusta_con_datos_anteriores_al_corte_y_la_guardia_dispara(mundo, cfgs, monkeypatch):
    """Si `ajustar` devolviera un modelo con datos posteriores al corte, el adaptador debe fallar (no seguir)."""
    real = ch.ajustar

    def tramposo(df, **kw):
        m = real(df, **kw)
        m.datos_hasta = kw["fecha_corte"] + timedelta(days=1)
        return m
    monkeypatch.setattr(ch, "ajustar", tramposo)
    with pytest.raises(AssertionError, match="leakage"):
        _run(mundo, cfgs)


def test_el_corte_pasado_a_ajustar_es_el_de_cada_ventana(mundo, cfgs, monkeypatch):
    real, cortes = ch.ajustar, []

    def espia(df, **kw):
        cortes.append(pd.Timestamp(kw["fecha_corte"]))
        return real(df, **kw)
    monkeypatch.setattr(ch, "ajustar", espia)
    _run(mundo, cfgs)
    assert cortes == [pd.Timestamp(T0 + timedelta(days=7 * k)) for k in range(4)]


# ------------------------------------------------------------------ sin filtrar por aprobación (decisión 5)

def test_incluye_lo_que_el_motor_habria_rechazado_y_a_las_perdedoras(base, cfgs):
    p_min = cfgs[1]["tickets"]["p_min_pierna"]
    piernas = [pm for m in base.meta for pm in m.piernas]
    bajo_p = [pm for pm in piernas if pm.p_modelo < p_min]
    ev_neg = [pm for pm in piernas if pm.p_modelo * pm.cuota < 1.0]
    assert len(bajo_p) > 0.5 * len(piernas)                                         # la mayoría la rechazaría p_min_pierna
    assert ev_neg and any(pm.p_modelo >= p_min for pm in piernas)                   # hay EV<0, y también las de p alta
    todas_pasan = [m for m in base.meta if all(pm.p_modelo >= p_min for pm in m.piernas)]
    assert len(todas_pasan) < 0.2 * len(base.meta)                                  # el grueso NO es "aprobable"
    assert any(not c.gano for c in base.cestas) and any(c.gano for c in base.cestas)


def test_cubre_todo_el_rango_de_cuotas_disponible(base, cfgs, mundo):
    _, pool, cu = mundo
    v3 = cfgs[1]
    q = cu[cu["partido_id"].isin(pool["partido_id"])]
    disponibles = {segmento_de("EPL", (s,), c, v3)[2] for s, c in zip(q["seleccion"], q["cuota"]) if s in APTAS_PARLAY}
    cubiertos = {ph.segmento[2] for c in base.cestas for ph in c.piernas}
    assert len(disponibles) >= 4 and cubiertos == disponibles
    mercados = {ph.segmento[1] for c in base.cestas for ph in c.piernas}
    assert mercados >= {"1X2", "OU2.5"} or len(mercados) >= 2                      # mezcla de mercados


def test_cestas_con_piernas_de_ligas_distintas_no_se_excluyen_por_estructura():
    """K por liga es una restricción de TICKETS (aprobación); aquí no se aplica: el muestreo no mira la liga."""
    import inspect
    src = inspect.getsource(ch.construir_cestas)
    assert "k_piernas_por_liga" not in src and "p_min_pierna" not in src and "ev_cons" not in src and "crs" not in src.lower().replace("crs_veto", "")


# ------------------------------------------------------------------ anuladas / pendientes (no se descartan en silencio)

def test_las_cestas_con_pierna_anulada_o_pendiente_se_excluyen_pero_se_cuentan(mundo, cfgs):
    d, pool, cu = mundo
    p2 = pool.copy()
    ids = sorted(p2["partido_id"])[:6]
    p2.loc[p2["partido_id"].isin(ids[:5]), ["estado", "goles_local", "goles_visitante"]] = ["cancelado", np.nan, np.nan]
    p2.loc[p2["partido_id"].isin(ids[5:]), ["estado", "goles_local", "goles_visitante"]] = ["programado", np.nan, np.nan]   # (pendiente tiene prioridad sobre anulada)
    r = _run(mundo, cfgs, pool=p2)
    assert r.excluidas and {m.resultado for m in r.excluidas} == {"anulada", "pendiente"}
    assert len(r.cestas) == len(r.meta) and all(m.resultado in ("gano", "perdio") for m in r.meta)
    malos = set(ids)
    assert not any(pm.partido_id in malos for m in r.meta for pm in m.piernas)           # ninguna cesta resuelta los contiene
    assert all(any(pm.partido_id in malos for pm in m.piernas) for m in r.excluidas)     # y cada excluida SÍ tiene alguno
    assert len(r.cestas) + len(r.excluidas) > 0


# ------------------------------------------------------------------ reproducibilidad y omisiones explícitas

def test_mismo_input_misma_salida_y_la_semilla_cambia_la_muestra(mundo, base, cfgs):
    assert _run(mundo, cfgs) == base
    assert [m.piernas for m in _run(mundo, cfgs, semilla=12).meta] != [m.piernas for m in base.meta]


def test_la_muestra_de_un_dia_no_depende_de_los_demas_dias(mundo, base, cfgs):
    d, pool, cu = mundo
    dia = base.meta[0].dia
    quedan = pool[pool["inicio"].dt.tz_convert("America/Mexico_City").dt.strftime("%Y-%m-%d") == dia]
    solo = _run(mundo, cfgs, pool=quedan)
    assert [m for m in solo.meta if m.dia == dia] == [m for m in base.meta if m.dia == dia] and solo.meta


def test_sin_historia_previa_no_se_inventa_nada(mundo, cfgs):
    d, pool, cu = mundo
    r = _run(mundo, cfgs, d=d[d["inicio"] >= T0])                                   # nada anterior al primer corte
    assert r.omisiones.get("ventana_sin_historia", 0) > 0                           # la 1.ª ventana se omite con motivo
    assert not any(m.ventana_t == T0 for m in r.meta)                               # ...y no produce cestas
    assert r.meta and all(m.ventana_t > T0 for m in r.meta)                         # las siguientes sí tienen historia (la 1.ª)
    vacio = _run(mundo, cfgs, d=d[d["inicio"] >= T1])                               # sin historia en NINGUNA ventana
    assert vacio.cestas == () and vacio.omisiones["ventana_sin_historia"] == len(pool)


def test_partidos_sin_cuota_asof_se_omiten_con_motivo(mundo, cfgs):
    d, pool, cu = mundo
    r = _run(mundo, cfgs, cu=cu.iloc[0:0])
    assert r.cestas == () and r.omisiones.get("sin_cuotas_asof", 0) == len(pool)


def test_equipo_sin_historial_se_omite_con_motivo(mundo, cfgs):
    d, pool, cu = mundo
    p2 = pool.copy()
    p2.loc[p2.index[0], "local"] = "EQUIPO_NUEVO"
    assert _run(mundo, cfgs, pool=p2).omisiones.get("equipo_sin_historial") == 1


def test_semilla_negativa_falla(mundo, cfgs):
    with pytest.raises(ValueError):
        _run(mundo, cfgs, semilla=-1)


# ------------------------------------------------------------------ reglas de traducción adicionales

def test_las_piernas_traen_lambda_rho_y_pesos_del_modelo_de_su_ventana(base, mundo, cfgs):
    """Cruce independiente para la 1.ª ventana: se reajusta aparte con los mismos hiperparámetros y se compara."""
    d, pool, _ = mundo
    v2 = cfgs[0]
    m = v2["modelo"]
    from golmodel.modelos.dixon_coles import ajustar
    from golmodel.servicios.modelo import _rho_prior
    mod = ajustar(d, fecha_corte=T0, xi=m["decaimiento_xi_inicial"], w_xg=m["w_xg_inicial"], l2_equipos=m["l2_lambda_inicial"],
                  l2_ligas=m["l2_lambda_inicial"], rho_limites=tuple(m["rho_limites"]), rho_prior=_rho_prior(v2))
    fila = {int(r.partido_id): r for r in pool.itertuples()}
    visto = 0
    for c, mt in zip(base.cestas, base.meta):
        if mt.ventana_t != T0:
            continue
        for ph, pm in zip(c.piernas, mt.piernas):
            r = fila[pm.partido_id]
            mx = mod.matriz(r.local, r.visitante, gmax=m["goles_max"])
            assert (ph.lambda_local, ph.lambda_visitante) == pytest.approx((mx.lambda_local, mx.lambda_visitante))
            assert ph.rho == pytest.approx(mx.rho)
            assert ph.peso_efectivo_local == pytest.approx(mod.peso_efectivo[r.local])
            assert ph.peso_efectivo_visitante == pytest.approx(mod.peso_efectivo[r.visitante])
            visto += 1
    assert visto > 100


def test_dentro_de_un_dia_no_hay_cestas_repetidas(base):
    por_dia: dict = {}
    for m in base.meta:
        por_dia.setdefault(m.dia, []).append(tuple((pm.partido_id, pm.selecciones) for pm in m.piernas))
    assert por_dia and all(len(v) == len(set(v)) for v in por_dia.values())


def test_se_usa_la_ultima_cuota_capturada_antes_de_decidir_no_la_primera(mundo, base, cfgs):
    d, pool, cu = mundo
    t_dia = {m.dia: pd.Timestamp(m.t_dia) for m in base.meta}
    nuevas = []
    for k, r in enumerate(pool.itertuples()):                                      # TODOS los partidos: el muestreo no garantiza una pierna concreta
        dia = r.inicio.tz_convert("America/Mexico_City").strftime("%Y-%m-%d")
        if dia in t_dia:
            nuevas.append({"partido_id": r.partido_id, "seleccion": "1X2:1", "cuota": 6.66,
                           "capturada_en": t_dia[dia] - pd.Timedelta(hours=1), "id": 20_000_000 + k})   # más reciente que la previa, aún antes de decidir
    previa = cu[cu["seleccion"] == "1X2:1"].set_index("partido_id")["capturada_en"]
    assert nuevas and all(previa[n["partido_id"]] < n["capturada_en"] for n in nuevas)        # cada nueva es posterior a la previa de SU partido
    cu2 = pd.concat([cu, pd.DataFrame(nuevas)], ignore_index=True)
    r = _run(mundo, cfgs, cu=cu2)
    usadas = [pm.cuota for m in r.meta for pm in m.piernas if pm.selecciones == ("1X2:1",)]
    assert usadas and set(usadas) == {6.66}


def test_selecciones_no_aptas_para_parlay_no_entran_a_las_cestas(mundo, cfgs):
    d, pool, cu = mundo
    extra = [{"partido_id": r.partido_id, "seleccion": s, "cuota": 1.9, "capturada_en": r.inicio - pd.Timedelta(days=2),
              "id": 30_000_000 + k} for k, r in enumerate(pool.itertuples()) for s in ("AH:local:-1.0", "AH:visitante:+1.0")]
    cu2 = pd.concat([cu, pd.DataFrame(extra)], ignore_index=True)
    r = _run(mundo, cfgs, cu=cu2)
    usadas = set().union(*(set(pm.selecciones) for m in r.meta + r.excluidas for pm in m.piernas))
    assert usadas <= APTAS_PARLAY                                                     # ni resueltas ni excluidas las contienen
    assert not r.excluidas                                                            # y no se "anula" nada por culpa de ellas


def test_el_instante_de_decision_es_el_maximo_entre_el_corte_y_el_inicio_del_dia_local(base, mundo):
    """Cálculo independiente de t_dia (no se usa el que reporta el adaptador): max(ventana_t, medianoche local en UTC)."""
    for m in base.meta:
        medianoche = (pd.Timestamp(m.dia).tz_localize("America/Mexico_City")).tz_convert("UTC")
        assert pd.Timestamp(m.t_dia) == max(pd.Timestamp(m.ventana_t), medianoche)


def test_los_dias_no_comparten_el_mismo_patron_de_muestreo(base, mundo):
    """Cada día tiene su propio flujo aleatorio: con el mismo nº de partidos, los patrones NO deben ser idénticos."""
    por_dia: dict = {}
    for m in base.meta:
        por_dia.setdefault(m.dia, []).append(m)
    patrones = []
    for dia, ms in sorted(por_dia.items()):
        pids = sorted({pm.partido_id for m in ms for pm in m.piernas})
        patrones.append(tuple((m.n, tuple(pids.index(pm.partido_id) for pm in m.piernas)) for m in ms[:10]))
    assert len(patrones) >= 3 and len(set(patrones)) == len(patrones)


def test_sin_cestas_repetidas_aunque_el_espacio_de_combinaciones_sea_chico(mundo, cfgs):
    v2, v3 = cfgs
    v3 = copy.deepcopy(v3)
    v3["tickets"]["tamano_alta_min"] = v3["tickets"]["tamano_alta_max"] = 2          # solo pares
    v3["calibracion_tickets"]["cestas_historicas"]["n_cestas_por_dia"] = 140          # ≈ el espacio total de un día chico
    d, pool, cu = mundo
    dia = pool["inicio"].dt.tz_convert("America/Mexico_City").dt.strftime("%Y-%m-%d").iloc[0]
    chico = pool[pool["inicio"].dt.tz_convert("America/Mexico_City").dt.strftime("%Y-%m-%d") == dia].iloc[:4]
    r = ch.construir_cestas(d, chico, cu, v2, v3, desde=T0, hasta=T0 + timedelta(days=7), semilla=3)
    claves = [tuple((pm.partido_id, pm.selecciones) for pm in m.piernas) for m in r.meta + r.excluidas]
    assert len(claves) > 100 and len(claves) == len(set(claves))


# ------------------------------------------------------------------ con PostgreSQL REAL (histórico cargado por el pipeline real)

from tests.test_integracion import AHORA as AHORA_BD, cargado, cfg, dsn  # noqa: E402,F401  (NO se importa `mundo`: chocaría con el de este módulo)
from golmodel import db  # noqa: E402


@pytest.fixture
def conn_bd(dsn, cargado):  # noqa: F811
    c = db.conectar(dsn)
    yield c
    c.rollback()
    c.close()


def _ventana_bd():
    fin = pd.Timestamp(AHORA_BD) - pd.Timedelta(days=1)
    return (fin - pd.Timedelta(days=21)).to_pydatetime(), fin.to_pydatetime()


def test_bd_real_construye_cestas_y_coincide_con_la_funcion_pura(conn_bd, cfg, cfgs):
    desde, hasta = _ventana_bd()
    v3 = cfgs[1]
    r = ch.construir_cestas_historicas(conn_bd, desde, hasta, cfg, v3, casa="pinnacle", semilla=5)
    assert len(r.cestas) >= 100 and all(isinstance(c.gano, bool) for c in r.cestas)
    d, pool, cu = ch.leer_historico(conn_bd, desde, hasta, "pinnacle")
    assert r == ch.construir_cestas(d, pool, cu, cfg, v3, desde=desde, hasta=hasta, semilla=5)      # misma ruta de datos
    assert len(pool) > 0 and set(cu["seleccion"]) <= {"1X2:1", "1X2:X", "1X2:2", "OU:2.5:over", "OU:2.5:under"}
    for m in r.meta:
        assert m.ventana_t >= desde and all(pm.cuota_capturada_en <= m.t_dia for pm in m.piernas)


def test_bd_real_la_casa_es_un_parametro_explicito_sin_fallback(conn_bd, cfg, cfgs):
    desde, hasta = _ventana_bd()
    r = ch.construir_cestas_historicas(conn_bd, desde, hasta, cfg, cfgs[1], casa="bet365", semilla=5)   # sin cuotas históricas de bet365
    assert r.cestas == () and r.omisiones.get("sin_cuotas_asof", 0) > 0                                    # no se cae a otra casa


def test_bd_real_el_resultado_se_toma_de_la_bd_y_coincide_con_un_calculo_manual(conn_bd, cfg, cfgs):
    desde, hasta = _ventana_bd()
    r = ch.construir_cestas_historicas(conn_bd, desde, hasta, cfg, cfgs[1], casa="pinnacle", semilla=5)
    goles = {x["id"]: (x["goles_local"], x["goles_visitante"]) for x in conn_bd.execute(
        "SELECT id, goles_local, goles_visitante FROM partidos WHERE inicio >= %s AND inicio < %s", (desde, hasta))}
    for c, m in zip(r.cestas[:200], r.meta[:200]):
        assert c.gano is all(_acierta(s, *goles[pm.partido_id]) for pm in m.piernas for s in pm.selecciones)


def test_un_partido_finalizado_sin_goles_no_se_resuelve_como_cero_a_cero(mundo, cfgs):
    d, pool, cu = mundo
    p2 = pool.copy()
    mal = int(sorted(p2["partido_id"])[0])
    p2.loc[p2["partido_id"] == mal, ["goles_local", "goles_visitante"]] = [np.nan, np.nan]      # dato corrupto: finalizado sin marcador
    r = _run(mundo, cfgs, pool=p2)
    assert not any(pm.partido_id == mal for m in r.meta for pm in m.piernas)                   # nunca entra como resuelta
    assert any(pm.partido_id == mal for m in r.excluidas for pm in m.piernas)                  # queda contada como excluida


def test_las_piernas_traen_el_partido_id_real_y_la_calibracion_corre_sobre_las_cestas_del_adaptador(base, cfgs):
    """El id real fija el flujo de ruido (misma distribución que tickets.py); sin ids distintos la calibración fallaría."""
    from golmodel.backtest import calibracion_tickets as ct
    for cesta, meta in zip(base.cestas, base.meta):
        assert [ph.partido_id for ph in cesta.piernas] == [pm.partido_id for pm in meta.piernas]
        assert len({ph.partido_id for ph in cesta.piernas}) == len(cesta.piernas)
    cfg_ct = {"calibracion_tickets": {"n_draws": 500, "q": 0.25, "sigma_g": 0.0, "sigma_liga": 0.1}}
    res = ct._resultados(list(base.cestas[:10]), 1.0, cfg_ct, 0)
    assert len(res) == 10 and all(0.0 < r.p_conservadora <= r.p_media <= 1.0 for r in res)
