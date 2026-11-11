"""Pruebas de motor/tickets.py (el consumidor final).

Mapa de condiciones de la autorización:
  1 fallback genuino ............ test_fallback_*           5 contrato → cerebro2 .. test_contrato_*
  2 modo_experimental ........... test_modo_experimental_*  6 reproducibilidad ..... test_reproducibilidad_*
  3 no-relajación silenciosa .... test_no_relajacion_*      7 caché (dec. 12) ...... test_cache_*
  4 trazabilidad ................ test_trazabilidad_*
Los fixtures usan datos SINTÉTICOS y el config REAL (config/v3_umbrales.yaml): verifican el mecanismo,
no que los umbrales sean correctos (validación OOS real pendiente).
"""
import copy
import json
import pathlib
import tracemalloc
from datetime import datetime, timedelta

import numpy as np
import pytest
import yaml

from golmodel.backtest.calibracion_tickets import VersionCalibradorTickets
from golmodel.modelos import incertidumbre as inc
from golmodel.modelos.calibracion_mercado import SeleccionCalibrada
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import probabilidad
from golmodel.motor import dependencia as dep
from golmodel.motor import tickets as tk

AHORA = datetime(2026, 11, 9, 12, 0, 0)
RUTA_CFG = pathlib.Path(__file__).resolve().parents[1] / "config" / "v3_umbrales.yaml"
DATOS_POOL = [(1, "EPL", 1.9, 1.4), (2, "EPL", 2.0, 1.3), (3, "SA", 1.8, 1.5),
              (4, "SA", 2.1, 1.2), (5, "MX1", 1.7, 1.5), (6, "MX1", 1.9, 1.3)]


def _cfg(**secciones):
    cfg = yaml.safe_load(RUTA_CFG.read_text(encoding="utf-8"))
    cfg["dependencia"]["n_draws"] = 2000
    cfg["tickets"]["cache_max_entradas"] = 64
    for sec, vals in secciones.items():
        cfg[sec].update(vals)
    return cfg


def _cand(pid, liga, lh, la, *, edge=1.12, sel=("OU:1.5:over",), crs=10, veto=False, peso=40.0,
          edad_h=1.0, ver="cm-v1", calibrado=True):
    p = probabilidad(matriz_marcadores(lh, la, 0.0), *sel)
    return tk.Candidata(pid, liga, tuple(sel), lh, la, 0.0, peso, peso, edge / p,
                        SeleccionCalibrada(p, ver, calibrado, (liga, "OU:1.5", "medio")),
                        crs, veto, AHORA - timedelta(hours=edad_h))


def _pool(**kw):
    return [_cand(*d, **kw) for d in DATOS_POOL]


def _version(*, c=0.5, sigma_liga=0.1, sigma_g=0.0, con="sinteticos", semilla=7):
    return VersionCalibradorTickets(
        datos_hasta=datetime(2026, 1, 1), config_hash="cfgh", n_draws=2000, semilla=semilla, metodo="global",
        segmentos_calibrados=[["EPL", "OU:1.5", "medio"]], segmentos_fallback=[["MX1", "OU:1.5", "medio"]],
        c_global=c, sigma_liga=sigma_liga, sigma_g=sigma_g, calibrado_con=con)


def _run(pool=None, version=None, cfg=None, *, flag=True, semilla=11, cache=None):
    return tk.generar_tickets(pool if pool is not None else _pool(), version or _version(),
                              cfg or _cfg(), modo_experimental=flag, semilla=semilla, ahora=AHORA, cache=cache)


def _dump(salida):
    return json.dumps(salida.a_json(), sort_keys=True)


# ============ bandera: obligatoria, sin default, y con candado ============

def test_modo_experimental_es_obligatorio_sin_default():
    with pytest.raises(TypeError):                                   # omitido → no hay valor por defecto
        tk.generar_tickets(_pool(), _version(), _cfg(), semilla=1, ahora=AHORA)
    with pytest.raises(TypeError):                                   # ni truthiness: debe ser bool
        tk.generar_tickets(_pool(), _version(), _cfg(), modo_experimental="si", semilla=1, ahora=AHORA)


def test_sin_version_de_calibracion_falla_cerrado():
    with pytest.raises(ValueError):
        tk.generar_tickets(_pool(), None, _cfg(), modo_experimental=True, semilla=1, ahora=AHORA)


def test_bandera_off_exige_calibracion_oos_real():
    # sinteticos NO puede desactivar la bandera (candado).
    with pytest.raises(ValueError):
        _run(version=_version(con="sinteticos"), flag=False)


# ============ condición 2: modo_experimental ============

def test_modo_experimental_techo_con_reservas():
    # Caso IDEAL (p alta, EV alto, CRS bajo, lift aceptable): sin la bandera no habría tope.
    libre = _run(version=_version(con="oos_real"), flag=False)        # fixture: supuesto de validación real
    exper = _run(version=_version(con="oos_real"), flag=True)
    assert libre.estado == exper.estado == "PROPUESTO"
    assert libre.ticket.techo_estado == "SIN_TOPE" and libre.ticket.promovible is True
    assert exper.ticket.techo_estado == "CON_RESERVAS" and exper.ticket.promovible is False
    assert libre.ticket.partidos == exper.ticket.partidos            # la bandera no altera la selección
    assert "APROBADO" not in _dump(libre) and "APROBADO" not in _dump(exper)   # tickets nunca aprueba


# ============ condición 1: fallback genuino ============

def test_fallback_abstencion_registra_constraint_por_cesta():
    cfg = _cfg(umbrales_provisionales={"umbral_ev_cons_ticket": 50.0})   # imposible de cumplir
    s = _run(cfg=cfg)
    a = s.auditoria
    assert s.estado == "ABSTENCION" and s.ticket is None and s.por_tamano == {}
    assert a.n_cestas_evaluadas > 0 and a.n_cestas_factibles == 0
    assert a.motivo_abstencion == "ninguna_cesta_factible" and a.categoria_abstencion == "tickets_sin_valor"
    for ce in a.cestas:                                              # CADA cesta dice qué la bloqueó
        r = {x.constraint: x for x in ce.rechazos}
        assert "ev_cons_ticket" in r and r["ev_cons_ticket"].umbral == 50.0
        assert r["ev_cons_ticket"].valor is not None and r["ev_cons_ticket"].valor <= 50.0
    assert a.bloqueos_cestas["ev_cons_ticket"] == a.n_cestas_evaluadas


def test_fallback_abstencion_sin_piernas_elegibles():
    s = _run(cfg=_cfg(tickets={"p_min_pierna": 0.99}))
    a = s.auditoria
    assert s.estado == "ABSTENCION" and a.motivo_abstencion == "sin_piernas_elegibles"
    assert a.n_cestas_evaluadas == 0
    assert len(a.rechazos_piernas) == a.n_candidatas == 6            # una explicación por pierna
    assert all(any(r.constraint == "p_calibrada_minima" for r in pr.rechazos) for pr in a.rechazos_piernas)


def test_fallback_piernas_insuficientes():
    pool = [_cand(*DATOS_POOL[0])] + [_cand(*d, crs=55) for d in DATOS_POOL[1:]]   # solo 1 elegible
    s = _run(pool=pool)
    assert s.estado == "ABSTENCION" and s.auditoria.motivo_abstencion == "piernas_insuficientes"
    assert s.auditoria.bloqueos_piernas["crs_alto"] == 5


# ============ condición 3: no-relajación silenciosa ============

def test_no_relajacion_silenciosa():
    cfg = _cfg(umbrales_provisionales={"lift_max": 0.5})             # ningún ticket puede cumplir
    antes = copy.deepcopy(cfg)
    s = _run(cfg=cfg)
    a = s.auditoria
    assert s.estado == "ABSTENCION" and a.n_cestas_factibles == 0 and a.n_cestas_evaluadas > 0
    assert cfg == antes                                              # el config no fue mutado
    assert a.constraints_aplicados["lift_max"] == 0.5                # el umbral aplicado es el pedido
    assert a.constraints_aplicados == tk._constraints(antes)         # y TODOS coinciden con el config
    assert all(not ce.factible for ce in a.cestas)                   # nada se coló por debajo del umbral

    # Relajar solo es posible entregando OTRA versión de config, y queda visible en la auditoría:
    s2 = _run(cfg=_cfg(umbrales_provisionales={"lift_max": 1.15}))
    assert s2.estado == "PROPUESTO"
    assert s2.auditoria.config_hash_restricciones != a.config_hash_restricciones


def test_no_relajacion_registro_consistente_con_umbrales():
    # Propiedad sobre un escenario MIXTO (algunas cestas pasan, otras no): factible ⇔ cumple TODOS los
    # umbrales aplicados, y cada rechazo registrado viola de verdad el umbral que cita.
    s = _run(cfg=_cfg(umbrales_provisionales={"ic_max_rel": 0.6}))
    a = s.auditoria
    c = a.constraints_aplicados
    assert 0 < a.n_cestas_factibles < a.n_cestas_evaluadas           # el escenario es realmente mixto
    viola = {"ev_cons_ticket": lambda v, u: not v > u, "ic_ancho": lambda v, u: v > u,
             "lift": lambda v, u: v > u, "k_piernas_por_liga": lambda v, u: v > u}
    for ce in a.cestas:
        if ce.factible:
            assert ce.ev_cons > c["umbral_ev_cons_ticket"] and ce.ic_rel <= c["ic_max_rel"] and ce.lift <= c["lift_max"]
            assert ce.rechazos == ()
        else:
            assert ce.rechazos
            assert all(viola[r.constraint](r.valor, r.umbral) for r in ce.rechazos)
    assert s.ticket is not None and s.ticket.ic_rel <= 0.6           # y el ticket elegido respeta el umbral


def test_umbrales_sin_defaults_falta_clave_falla():
    cfg = _cfg()
    del cfg["tickets"]["p_min_pierna"]
    with pytest.raises(KeyError):                                    # nunca se inventa un umbral
        _run(cfg=cfg)


def test_no_se_trunca_en_silencio():
    with pytest.raises(ValueError):
        _run(cfg=_cfg(tickets={"max_cestas_evaluadas": 5}))


# ============ condición 4: trazabilidad ============

def test_trazabilidad_auditoria_completa():
    s = _run(version=_version(c=0.5, sigma_liga=0.1, sigma_g=0.0))
    a = s.auditoria
    assert a.version_calibracion_mercado == ("cm-v1",)
    assert a.version_incertidumbre == "incertidumbre-v1" and a.version_dependencia == "dependencia-v1"
    assert a.version_calibracion_tickets.startswith("ct:cfgh:") and a.calibrado_con == "sinteticos"
    assert a.version_tickets == "tickets-v1"
    assert a.c_seg["c_global"] == 0.5 and a.c_seg["segmentos_fallback"] == [["MX1", "OU:1.5", "medio"]]
    assert a.sigma_g == 0.0 and a.sigma_liga == 0.1
    assert a.modo_experimental is True and a.techo_estado == "CON_RESERVAS"
    assert a.constraints_aplicados == tk._constraints(_cfg()) and len(a.config_hash_restricciones) == 16
    assert a.n_cestas_evaluadas == len(a.cestas) and a.n_cestas_factibles == sum(c.factible for c in a.cestas)
    assert a.cuota_combinada_tipo.startswith("teorica_producto_de_cuotas_por_pierna_bajo_independencia_de_precios")
    assert "NO ejecutable" in a.cuota_combinada_tipo and s.ticket.cuota_combinada_tipo == a.cuota_combinada_tipo
    assert a.criterio_ranking == "log_crecimiento_kelly_fraccional_SIN_tope_de_stake"
    json.dumps(s.a_json())                                           # payload serializable (auditoria_decisiones)


def test_trazabilidad_motivo_por_constraint_en_piernas():
    pool = _pool()
    pool[0] = _cand(*DATOS_POOL[0], veto=True)
    pool[1] = _cand(*DATOS_POOL[1], crs=None)
    pool[2] = _cand(*DATOS_POOL[2], edad_h=48)
    pool[3] = _cand(*DATOS_POOL[3], sel=("AH:local:+1.0",))          # selección con push
    a = _run(pool=pool).auditoria
    motivos = {pr.partido_id: {r.constraint for r in pr.rechazos} for pr in a.rechazos_piernas}
    assert "crs_veto" in motivos[1] and "crs_no_disponible" in motivos[2]
    assert "dato_obsoleto" in motivos[3] and "seleccion_con_push" in motivos[4]


def test_dato_posterior_a_ahora_se_rechaza():
    pool = _pool()
    pool[0] = _cand(*DATOS_POOL[0], edad_h=-3)                       # info "del futuro" (look-ahead)
    a = _run(pool=pool).auditoria
    assert any(r.constraint == "info_posterior_a_ahora" for pr in a.rechazos_piernas for r in pr.rechazos)


# ============ condición 5: contrato tickets → cerebro2 ============

def test_contrato_tickets_cerebro2():
    s = _run()
    t = s.ticket
    for campo in ("p_media", "p_conservadora", "p_independencia", "lift", "ic", "error_mc", "ev_cons",
                  "cuota_combinada", "techo_estado", "promovible", "modo_experimental"):
        assert getattr(t, campo) is not None                         # ticket completo
    assert t.p_conservadora <= t.p_media and t.lift == pytest.approx(t.p_media / t.p_independencia)
    for pt in t.piernas:                                             # p_conservadora POR PIERNA
        # el cuantil 25 queda por debajo de la mediana p(θ̂) (legs OU: p crece con λ); con q mal puesto falla
        assert 0.0 < pt.p_conservadora < pt.p_independencia and pt.ev_cons > 0
        assert pt.calibrador_version == "cm-v1" and pt.calibrado is True
    mixto = _run(cfg=_cfg(umbrales_provisionales={"ic_max_rel": 0.6})).auditoria   # con rechazos REALES
    rechazos = [r for ce in mixto.cestas for r in ce.rechazos]
    assert rechazos                                                  # el bucle no puede ser vacío
    for r in rechazos:                                               # rechazos POR constraint con valor y umbral
        assert r.constraint in tk.ORDEN_CONSTRAINTS and r.umbral is not None and r.valor is not None
    assert set(s.por_tamano) == {2, 3, 4, 5, 6}                      # mejor factible por tamaño


def test_objetivo_es_argmax_de_crecimiento_sin_pesos():
    s = _run()
    alta = [c for c in s.auditoria.cestas if c.factible and c.tipo == "alta_confianza"]
    assert s.ticket.tipo == "alta_confianza"
    assert s.ticket.log_crecimiento_sin_tope == pytest.approx(max(c.log_crecimiento_sin_tope for c in alta))


def test_ranking_es_log_crecimiento_no_ev_bruto():
    # 5 piernas FUERTES (+12 % de ventaja) y 3 DÉBILES (+5 %), ≤2 por liga. Añadir una pierna débil SIEMPRE
    # sube el EV bruto, pero el crecimiento log la penaliza por la varianza que añade. Si el ranking degenerara
    # en EV (p. ej. por un tope de stake activo), elegiría 8 piernas: el tamaño sería un sesgo, no un resultado.
    datos = [(1, "A", 1.9, 1.4, 1.12), (2, "A", 2.0, 1.3, 1.12), (3, "B", 1.8, 1.5, 1.12),
             (4, "B", 2.1, 1.2, 1.12), (5, "C", 1.7, 1.5, 1.12), (6, "C", 1.9, 1.3, 1.05),
             (7, "D", 1.8, 1.4, 1.05), (8, "D", 2.0, 1.3, 1.05)]
    s = _run(pool=[_cand(*d[:4], edge=d[4]) for d in datos])
    fa = [c for c in s.auditoria.cestas if c.factible and c.tipo == "alta_confianza"]
    ev_max = max(fa, key=lambda c: c.ev_cons)
    assert ev_max.n == 8                                              # el EV bruto preferiría el máximo
    assert s.ticket.n == 5 and s.ticket.partidos == (1, 2, 3, 4, 5)   # el crecimiento log, no
    assert s.ticket.partidos != ev_max.partidos and s.ticket.ev_cons < ev_max.ev_cons
    assert s.auditoria.criterio_ranking == "log_crecimiento_kelly_fraccional_SIN_tope_de_stake"


def test_auditoria_registra_por_cesta_ev_y_crecimiento_con_y_sin_tope():
    # Sin estos tres valores por cesta, Cerebro 2 no puede auditar por qué se eligió una cesta sobre otra.
    cap = _cfg()["bankroll"]["stake_max_parlay_u"]
    a = _run(cfg=_cfg(umbrales_provisionales={"ic_max_rel": 0.6})).auditoria       # escenario mixto
    assert a.n_cestas_evaluadas > 0 and 0 < a.n_cestas_factibles < a.n_cestas_evaluadas
    for ce in a.cestas:                       # ninguna cesta de este pool cae por estructura: siempre hay cifras
        assert ce.ev_cons is not None and ce.stake_u is not None
        assert ce.log_crecimiento_sin_tope is not None and ce.log_crecimiento_con_tope is not None
        assert 0 <= ce.stake_u <= cap
        assert ce.log_crecimiento_con_tope <= ce.log_crecimiento_sin_tope + 1e-12   # con tope nunca supera al fraccional
    # con las cifras de la auditoría se reconstruye la elección con la MISMA regla que el código: máximo sin tope
    # entre las factibles de 5-8 y, si no hay ninguna, entre todas las factibles (reducidas)
    s = _run(cfg=_cfg(umbrales_provisionales={"ic_max_rel": 0.6}))
    alta = [c for c in s.auditoria.cestas if c.factible and c.tipo == "alta_confianza"]
    candidatas = alta or [c for c in s.auditoria.cestas if c.factible]
    assert candidatas
    assert max(candidatas, key=lambda c: c.log_crecimiento_sin_tope).partidos == s.ticket.partidos


def test_cestas_rechazadas_por_estructura_no_llevan_metricas():
    pool = [_cand(i, "EPL", 1.8 + 0.05 * i, 1.3) for i in range(1, 7)]              # las 6 de la misma liga
    por_k = [c for c in _run(pool=pool).auditoria.cestas if c.rechazos and c.rechazos[0].constraint == "k_piernas_por_liga"]
    assert por_k                                                                    # el caso existe (no vacío)
    assert all(c.ev_cons is None and c.log_crecimiento_sin_tope is None and c.log_crecimiento_con_tope is None
               and c.stake_u is None and len(c.rechazos) == 1 for c in por_k)       # no se calculó MC: None, no 0


def test_el_tope_acota_el_stake_pero_no_el_ranking():
    s = _run()
    cap = _cfg()["bankroll"]["stake_max_parlay_u"]
    assert 0 < s.ticket.stake_u <= cap                                # el stake propuesto sí lleva tope
    assert s.ticket.log_crecimiento_con_tope <= s.ticket.log_crecimiento_sin_tope + 1e-12


def test_una_pierna_por_partido():
    pool = _pool() + [_cand(1, "EPL", 1.9, 1.4, edge=1.25)]          # 2ª pierna (más cuota) del partido 1
    s = _run(pool=pool)
    assert len(set(s.ticket.partidos)) == len(s.ticket.partidos)     # nunca 2 piernas del mismo partido
    assert any(r.constraint == "otra_pierna_mejor_del_partido"
               for pr in s.auditoria.rechazos_piernas for r in pr.rechazos)


def test_k_por_liga_bloquea_y_cae_a_reducido():
    pool = [_cand(i, "EPL", 1.8 + 0.05 * i, 1.3) for i in range(1, 7)]   # las 6 de la misma liga
    s = _run(pool=pool)
    assert s.estado == "PROPUESTO" and s.ticket.tipo == "reducido" and s.ticket.n == 2   # K=2 ⇒ solo 2
    assert s.auditoria.bloqueos_cestas["k_piernas_por_liga"] > 0
    assert all(c.n <= 2 or not c.factible for c in s.auditoria.cestas)


# ============ condición 6: reproducibilidad ============

def test_reproducibilidad_dos_corridas_identicas():
    assert _dump(_run()) == _dump(_run())


def test_reproducibilidad_cache_fria_vs_caliente():
    cache = tk.CachePierna(64)
    fria = _run(cache=cache)
    caliente = _run(cache=cache)                                     # reutiliza la caché de la 1ª
    assert cache.hits == 6 and _dump(fria) == _dump(caliente)


def test_reproducibilidad_la_semilla_importa():
    assert _dump(_run(semilla=11)) != _dump(_run(semilla=12))


# ============ condición 7: caché (dec. 12) ============

def test_cache_hit_igual_a_calculo_directo():
    cache = tk.CachePierna(64)
    cfg, v = _cfg(), _version()
    _run(cache=cache, cfg=cfg, version=v)
    assert cache.misses == 6 and cache.hits == 0
    # La entrada cacheada de la pierna 1 coincide bit a bit con el cálculo directo.
    c = _pool()[0]
    sampler = inc.FactorSamplerJerarquico(sigma_g=v.sigma_g, sigma_liga=v.sigma_liga)
    f = dep.factores_compartidos(sampler, sorted({d[1] for d in DATOS_POOL}), 2000, 11)
    tau = inc.tau_idiosincratico(v.c_global, c.peso_efectivo_local)
    directo, p_ind = dep.p_pierna_sorteos(
        dep.Pierna(c.partido_id, c.liga, c.lambda_local, c.lambda_visitante, c.rho, c.selecciones, tau, tau), f, 11)
    clave = next(k for k in cache._d if k[0] == c.partido_id)
    arr, p_ind_cache = cache._d[clave]
    assert np.array_equal(arr, directo) and p_ind_cache == p_ind


@pytest.mark.parametrize("cambio", ["tau", "sigma", "semilla", "n_draws"])
def test_cache_se_invalida_al_cambiar_tau_sigma_semilla_n_draws(cambio):
    cache = tk.CachePierna(64)
    base = _run(cache=cache)
    assert (cache.misses, cache.hits) == (6, 0)
    kw = dict(cache=cache)
    if cambio == "tau":
        kw["version"] = _version(c=0.7)
    elif cambio == "sigma":
        kw["version"] = _version(sigma_liga=0.2)
    elif cambio == "semilla":
        kw["semilla"] = 99
    else:
        kw["cfg"] = _cfg(dependencia={"n_draws": 2500})
    otra = _run(**kw)
    assert (cache.misses, cache.hits) == (12, 0)                     # NO reutilizó nada: clave distinta
    assert _dump(otra) != _dump(base)                                # y el resultado cambió de verdad


def test_cache_lru_expulsa_la_menos_reciente():
    c = tk.CachePierna(2)
    mk = lambda v: (lambda: (np.full(4, v), v))
    c.get_or_compute("a", mk(1.0))
    c.get_or_compute("b", mk(2.0))
    c.get_or_compute("a", mk(9.0))                                   # hit: 'a' pasa a ser la más reciente
    c.get_or_compute("c", mk(3.0))                                   # excede el tope → expulsa 'b'
    assert "a" in c and "c" in c and "b" not in c
    assert (c.hits, c.misses, c.evictions, len(c)) == (1, 3, 1, 2)
    assert c.get_or_compute("a", mk(9.0))[1] == 1.0                  # el hit no recalculó
    with pytest.raises(ValueError):
        c._d["a"][0][0] = 5.0                                        # entradas de solo lectura


def test_cache_memoria_acotada_con_cache_activa():
    S, N = 20000, 100
    c = tk.CachePierna(N)
    tracemalloc.start()
    for i in range(150):
        c.get_or_compute(i, lambda: (np.random.default_rng(i).random(S), 0.5))
    _, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    cota = N * (S * 8 + 8)                                           # tope = N entradas × S×8 B (≈16 MB)
    assert len(c) == N and c.evictions == 50
    assert c.nbytes() <= cota and pico <= cota + 2e6


def test_cache_diminuta_no_cambia_el_resultado():
    # Con LRU expulsando constantemente se recalcula, y como es determinista el resultado es idéntico.
    grande = _run(cache=tk.CachePierna(64))
    chica = tk.CachePierna(3)
    pequena = _run(cache=chica)
    assert len(chica) <= 3 and chica.evictions == 3
    assert _dump(grande) == _dump(pequena)
