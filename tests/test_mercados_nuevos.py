"""Mercados nuevos con datos SINTÉTICOS: córners (NB), props de jugador, SGP correlacionado (dependencia_mercados) y Soñadoras analíticas.
Ninguno toca Dixon-Coles: se comprueba que los mercados de goles salen igual que la matriz exacta."""
from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from golmodel.modelos import corners as mc
from golmodel.modelos import props as mp
from golmodel.modelos.incertidumbre import FactorSamplerJerarquico
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import probabilidad
from golmodel.motor import dependencia_mercados as dm
from golmodel.motor import sonadoras_analiticas as sa

AHORA = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
CFG = {"mercados_extendidos": {"sorteos_parametros": 300, "sorteos_partido": 300, "spearman_goles_corners": 0.0, "rho_tiros_goles": 0.3},
       "dependencia": {"percentil_conservador": 0.25, "ic": [0.025, 0.975]},
       "props": {"minutos_titular": 85, "minutos_suplente": 15, "tasa_al_arco": 0.33, "factor_rival_limites": [0.6, 1.6]}}
SAMPLER = FactorSamplerJerarquico()


# ------------------------------------------------------------------ córners
def _liga_corners(n_equipos=20, jornadas=4, semilla=3, k=9.0):
    rng = np.random.default_rng(semilla)
    eq = [f"E{i:02d}" for i in range(n_equipos)]
    at = dict(zip(eq, rng.normal(0, 0.18, n_equipos)))
    co = dict(zip(eq, rng.normal(0, 0.12, n_equipos)))
    arbs = [f"R{i}" for i in range(8)]
    ar = dict(zip(arbs, rng.normal(0, 0.06, len(arbs))))
    c, h = math.log(4.8), 0.10
    filas, t = [], AHORA - timedelta(days=700)
    for _ in range(jornadas):
        for a in eq:
            for b in eq:
                if a == b:
                    continue
                r = arbs[rng.integers(len(arbs))]
                ml = math.exp(c + h + at[a] + co[b] + ar[r])
                mv = math.exp(c + at[b] + co[a] + ar[r])
                cl = rng.negative_binomial(k, k / (k + ml))
                cv = rng.negative_binomial(k, k / (k + mv))
                gl, gv = rng.poisson(1.4 + 0.02 * cl), rng.poisson(1.1 + 0.02 * cv)
                filas.append({"local": a, "visitante": b, "corners_local": cl, "corners_visitante": cv, "arbitro": r,
                              "goles_local": gl, "goles_visitante": gv, "inicio": t})
                t += timedelta(hours=8)
    return pd.DataFrame(filas), at, co, h, k


def test_corners_recupera_estilo_de_equipo_ventaja_y_dispersion():
    df, at, co, h, k = _liga_corners()
    m = mc.ajustar(df, AHORA, xi=0.0, l2=0.5)
    est = np.array([m.ataque[e] for e in at])
    real = np.array(list(at.values()))
    assert np.corrcoef(est, real)[0, 1] > 0.85                       # el «estilo» (córners que genera) se recupera
    assert np.corrcoef([m.concede[e] for e in co], list(co.values()))[0, 1] > 0.7
    assert abs(m.ventaja - h) < 0.05
    assert 4.5 < m.k < 20                                             # sobredispersión detectada (Poisson sería k → ∞)
    assert 8.5 < m.media_liga < 11.5


def test_corners_probabilidades_coherentes():
    m = mc.ModeloCorners(c=math.log(5.0), ventaja=0.1, ataque={"A": 0.2, "B": -0.1}, concede={"A": 0.0, "B": 0.1}, k=8.0)
    d = mc.distribucion(m, "A", "B")
    assert abs(d["total"].sum() - 1) < 1e-9 and abs(d["local"].sum() - 1) < 1e-9
    ps = [mc.prob_mas_de(d["total"], x) for x in (7.5, 8.5, 9.5, 10.5)]
    assert all(a > b for a, b in zip(ps, ps[1:]))                      # más línea = menos probable
    esperado = (d["total"] * np.arange(len(d["total"]))).sum()
    assert abs(esperado - (d["mu_local"] + d["mu_visitante"])) < 0.05
    assert d["mu_local"] > d["mu_visitante"]                          # A ataca más, es local y B concede más


def test_corners_pocos_datos_falla_explicito():
    df, *_ = _liga_corners(n_equipos=4, jornadas=1)
    with pytest.raises(ValueError, match="pocos partidos"):
        mc.ajustar(df.head(10), AHORA)


def test_correlacion_goles_corners_medida():
    df, *_ = _liga_corners()
    assert mc.correlacion_goles_corners(df) > 0.03                   # el sintético liga goles con córners
    assert mc.correlacion_goles_corners(df.head(20)) == 0.0           # sin muestra: 0 (no se inventa)


# ------------------------------------------------------------------ props
def test_props_anota_nunca_supera_que_su_equipo_anote():
    M = matriz_marcadores(1.8, 1.0, -0.05)
    pmf_eq = mp.pmf_marginal(M.p, "local")
    j = mp.Jugador(1, "Delantero", 10, minutos=2400, partidos=28, xg=14.0, tiros=95)
    pf = mp.perfil(j, 1.8, 1.0, CFG)
    p = mp.p_anota(pf, pmf_eq)
    assert 0.25 < p < probabilidad(M, "ANOTA:local:si")
    assert 0.9 < pf.titularidad <= 1.0
    suplente = mp.perfil(mp.Jugador(2, "Suplente", 10, minutos=600, partidos=25, xg=3.0, tiros=20), 1.8, 1.0, CFG)
    assert suplente.titularidad < 0.3 and mp.p_anota(suplente, pmf_eq) < p


def test_props_rival_y_tiros():
    j = mp.Jugador(1, "X", 10, 2000, 24, 10.0, tiros=80)
    debil = mp.perfil(j, 1.5, mp.factor_rival(2.0, 1.3, CFG), CFG)
    fuerte = mp.perfil(j, 1.5, mp.factor_rival(0.8, 1.3, CFG), CFG)
    assert debil.tasa_gol_partido > fuerte.tasa_gol_partido
    assert mp.p_tiros_arco(debil, 1) > mp.p_tiros_arco(debil, 2) > mp.p_tiros_arco(debil, 3)
    assert mp.factor_rival(9.0, 1.0, CFG) == 1.6 and mp.factor_rival(None, 1.0, CFG) == 1.0
    sin_tiros = mp.perfil(mp.Jugador(3, "Y", 10, 2000, 24, 5.0, tiros=None), 1.5, 1.0, CFG)
    assert mp.p_tiros_arco(sin_tiros, 1) is None                        # sin dato no se inventa


# ------------------------------------------------------------------ SGP con mercados nuevos
def _partido(pid=1, spearman=None):
    return dm.PartidoExt(pid, "LL", 1.9, 0.9, -0.05, corners=(6.0, 4.0, 10.0),
                         jugadores={7: dm.JugadorPartido(7, "local", 0.30, 1.4), 8: dm.JugadorPartido(8, "local", 0.20, 0.9),
                                    9: dm.JugadorPartido(9, "visitante", 0.35, None)})


def _cfg(spearman=0.0):
    return {**CFG, "mercados_extendidos": {**CFG["mercados_extendidos"], "spearman_goles_corners": spearman}}


def test_goles_salen_igual_que_la_matriz_exacta():
    r, _ = dm.probabilidad_conjunta([_partido()], [dm.PiernaExt(1, "OU:2.5:over")], SAMPLER, _cfg(), 5)
    assert abs(r.p_media - probabilidad(matriz_marcadores(1.9, 0.9, -0.05), "OU:2.5:over")) < 0.015
    r, _ = dm.probabilidad_conjunta([_partido()], [dm.PiernaExt(1, "1X2:1"), dm.PiernaExt(1, "BTTS:si")], SAMPLER, _cfg(), 5)
    assert abs(r.p_media - probabilidad(matriz_marcadores(1.9, 0.9, -0.05), "1X2:1", "BTTS:si")) < 0.015


def test_corners_salen_igual_que_la_binomial_negativa():
    r, _ = dm.probabilidad_conjunta([_partido()], [dm.PiernaExt(1, "CORNERS:total:over:9.5")], SAMPLER, _cfg(), 5)
    exacta = mc.prob_mas_de(np.convolve(mc.pmf(6.0, 10.0), mc.pmf(4.0, 10.0)), 9.5)
    assert abs(r.p_media - exacta) < 0.015
    r, _ = dm.probabilidad_conjunta([_partido()], [dm.PiernaExt(1, "CORNERS:local:over:5.5")], SAMPLER, _cfg(), 5)
    assert abs(r.p_media - mc.prob_mas_de(mc.pmf(6.0, 10.0), 5.5)) < 0.02


def test_correlacion_corners_goles_sube_la_conjunta():
    piernas = [dm.PiernaExt(1, "OU:2.5:over"), dm.PiernaExt(1, "CORNERS:total:over:9.5")]
    sin, _ = dm.probabilidad_conjunta([_partido()], piernas, SAMPLER, _cfg(0.0), 5)
    con, _ = dm.probabilidad_conjunta([_partido()], piernas, SAMPLER, _cfg(0.35), 5)
    assert abs(sin.lift - 1) < 0.06                                   # sin correlación medida ≈ independencia
    assert con.lift > 1.08 and con.p_media > sin.p_media              # «Más de 8.5 córners» es más probable junto a «Más 2.5 goles»


def test_jugador_anota_esta_contenido_en_que_su_equipo_anote():
    p = [_partido()]
    solo, marg = dm.probabilidad_conjunta(p, [dm.PiernaExt(1, "JUG:7:anota"), dm.PiernaExt(1, "ANOTA:local:si")], SAMPLER, _cfg(), 5)
    assert abs(solo.p_media - marg["1|JUG:7:anota"]) < 1e-12           # P(Yamal anota y Barça anota) = P(Yamal anota)
    lam = mp.PerfilProp(7, "", 1, 85, 0, None, 0.30 * 1.9, None, 0.30)
    exacta = mp.p_anota(lam, mp.pmf_marginal(matriz_marcadores(1.9, 0.9, -0.05).p, "local"))
    assert abs(marg["1|JUG:7:anota"] - exacta) < 0.02
    dos, _ = dm.probabilidad_conjunta(p, [dm.PiernaExt(1, "JUG:7:anota"), dm.PiernaExt(1, "JUG:8:anota")], SAMPLER, _cfg(), 5)
    assert dos.p_media < marg["1|JUG:7:anota"]


def test_sgp_estilo_barca_over_y_tiros_correlaciona_positivo():
    piernas = [dm.PiernaExt(1, "1X2:1"), dm.PiernaExt(1, "OU:2.5:over"), dm.PiernaExt(1, "JUG:7:tiros_arco:2")]
    r, marg = dm.probabilidad_conjunta([_partido()], piernas, SAMPLER, _cfg(), 5)
    assert r.lift > 1.2 and r.p_media <= min(marg.values())


def test_entre_partidos_distintos_sin_factores_es_independencia():
    p = [_partido(1), _partido(2)]
    r, marg = dm.probabilidad_conjunta(p, [dm.PiernaExt(1, "1X2:1"), dm.PiernaExt(2, "CORNERS:total:over:9.5")], SAMPLER, _cfg(), 5)
    assert abs(r.lift - 1) < 0.05


def test_validaciones_y_determinismo():
    p = [_partido()]
    with pytest.raises(ValueError, match="fuera de catálogo"):
        dm.probabilidad_conjunta(p, [dm.PiernaExt(1, "TARJETAS:over:4.5")], SAMPLER, _cfg(), 1)
    with pytest.raises(ValueError, match="sin dato de tiros"):
        dm.probabilidad_conjunta(p, [dm.PiernaExt(1, "JUG:9:tiros_arco:1")], SAMPLER, _cfg(), 1)
    with pytest.raises(ValueError, match="modelo de córners"):
        dm.probabilidad_conjunta([dm.PartidoExt(1, "LL", 1.2, 1.1, 0.0)], [dm.PiernaExt(1, "CORNERS:total:over:9.5")], SAMPLER, _cfg(), 1)
    a, _ = dm.probabilidad_conjunta(p, [dm.PiernaExt(1, "JUG:7:anota")], SAMPLER, _cfg(), 11)
    b, _ = dm.probabilidad_conjunta(p, [dm.PiernaExt(1, "JUG:7:anota")], SAMPLER, _cfg(), 11)
    assert a.p_media == b.p_media


# ------------------------------------------------------------------ Soñadoras analíticas
CFG_SA = {**_cfg(0.3), "sonadoras_analiticas": {"p_min_pierna": 0.55, "cuota_min_pierna": 1.10, "n_max_piernas": 8, "max_por_partido": 3,
                                                 "ancho_haz": 30, "max_evaluadas": 12, "sorteos_cribado": [80, 100], "alternativas": 2,
                                                 "umbral_ev_valor": 0.0, "cuota_max_permitida": 500}}


def _dia(n=6, cuota_casa=None):
    partidos, cands = [], []
    for i in range(1, n + 1):
        lam_l, lam_v = 1.4 + 0.1 * i, 1.0
        p = dm.PartidoExt(i, "LL", lam_l, lam_v, -0.05, corners=(5.5, 4.2, 10.0), jugadores={100 + i: dm.JugadorPartido(100 + i, "local", 0.3, 1.5)})
        partidos.append(p)
        M = matriz_marcadores(lam_l, lam_v, -0.05)
        for s in ("1X2:1", "DC:1X", "OU:1.5:over", "OU:2.5:over", "BTTS:si", "ANOTA:local:si"):
            q = probabilidad(M, s)
            cands.append(sa.CandidataExt(i, s, q, None if cuota_casa is None else cuota_casa(q), s, "goles"))
        cands.append(sa.CandidataExt(i, "CORNERS:total:over:7.5", mc.prob_mas_de(np.convolve(mc.pmf(5.5, 10), mc.pmf(4.2, 10)), 7.5),
                                     None, "córners", "corners"))
    return cands, partidos


def test_soñadora_analitica_cae_en_el_rango_y_es_la_mas_probable():
    cands, partidos = _dia()
    s = sa.construir(cands, partidos, SAMPLER, CFG_SA, cuota_min=8, cuota_max=15, semilla=3)
    assert s.estado == "PROPUESTA"
    t = s.ticket
    assert 8 <= t.cuota_combinada <= 15
    assert all(t.p_media >= alt.p_media for alt in s.alternativas)
    assert t.etiqueta == sa.ENTRETENIMIENTO and "sin cuota de casa" in t.motivo_etiqueta    # sin cuotas reales no se afirma valor
    assert t.uno_de_cada == pytest.approx(1 / t.p_media)
    assert any(c.mercado == "corners" for c in t.piernas) or any(c.mercado == "goles" for c in t.piernas)


def test_soñadora_analitica_de_valor_solo_con_cuotas_reales_generosas():
    cands, partidos = _dia(cuota_casa=lambda q: 1.25 / q)            # la casa paga 25 % más que lo justo en goles
    solo_goles = [c for c in cands if c.mercado == "goles"]
    s = sa.construir(solo_goles, partidos, SAMPLER, CFG_SA, cuota_min=8, cuota_max=15, semilla=3)
    assert s.ticket.etiqueta == sa.VALOR and s.ticket.ev_cons > 0
    caras, _ = _dia(cuota_casa=lambda q: 0.9 / q)
    s2 = sa.construir([c for c in caras if c.mercado == "goles"], partidos, SAMPLER, CFG_SA, cuota_min=8, cuota_max=15, semilla=3)
    assert s2.ticket.etiqueta == sa.ENTRETENIMIENTO and "ventaja" in s2.ticket.motivo_etiqueta


def test_soñadora_analitica_inalcanzable_y_rango_invalido():
    cands, partidos = _dia(n=1)
    s = sa.construir(cands, partidos, SAMPLER, CFG_SA, cuota_min=200, cuota_max=400, semilla=1)
    assert s.estado == "NO_DISPONIBLE" and "máximo alcanzable" in s.motivo
    with pytest.raises(ValueError):
        sa.construir(cands, partidos, SAMPLER, CFG_SA, cuota_min=20, cuota_max=10, semilla=1)
    with pytest.raises(ValueError):
        sa.construir(cands, partidos, SAMPLER, CFG_SA, cuota_min=20, cuota_max=900, semilla=1)


# ------------------------------------------------------------------ estadísticas desde el CSV (con PostgreSQL)
CSV = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,Referee,HS,AS,HST,AST,HC,AC
E0,16/08/2025,15:00,Arsenal,Chelsea,2,1,H,M Oliver,15,9,6,3,7,4
E0,17/08/2025,16:30,Liverpool,Everton,1,1,D,A Taylor,18,7,5,2,9,2
E0,18/08/2025,20:00,Fulham,Brentford,0,0,D,,10,11,2,4,,
"""


def test_leer_estadisticas_del_csv():
    from golmodel.ingesta import estadisticas as es
    df = es.leer(CSV, "EPL")
    assert len(df) == 2                                               # la fila sin córners no entra
    r = df.iloc[0]
    assert (r["corners_local"], r["corners_visitante"], r["tiros_arco_local"], r["arbitro"]) == (7, 4, 6, "M Oliver")


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_mx_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    raiz = Path(__file__).resolve().parents[1]
    c = psycopg.connect(admin_dsn.replace("/postgres?", f"/{nombre}?"), row_factory=dict_row)
    c.execute((raiz / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    c.execute((raiz / "db/seed.sql").read_text())
    from golmodel import migraciones
    migraciones.aplicar(c)
    yield c
    c.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def test_estadisticas_se_guardan_y_alimentan_el_modelo_de_corners(bd):
    from golmodel import repositorio as repo
    from golmodel.ingesta import estadisticas as es
    from golmodel.ingesta.pipeline import cargar_historico
    from golmodel.integracion import mercados_extendidos as mx
    df, at, *_ = _liga_corners(n_equipos=10, jornadas=3)
    df["inicio"] = pd.to_datetime(df["inicio"], utc=True)
    partidos = df.rename(columns={})[["inicio", "local", "visitante", "goles_local", "goles_visitante"]].assign(liga="EPL")
    cargar_historico(bd, "EPL", partidos)
    liga = repo.ligas(bd)["EPL"]
    r = es.guardar(bd, liga, df[["local", "visitante", "inicio", "corners_local", "corners_visitante", "arbitro"]]
                   .assign(tiros_local=None, tiros_visitante=None, tiros_arco_local=None, tiros_arco_visitante=None))
    assert r == {"guardadas": len(df), "sin_partido": 0}
    r2 = es.guardar(bd, liga, df.head(3).assign(tiros_local=None, tiros_visitante=None, tiros_arco_local=None, tiros_arco_visitante=None))
    assert r2["guardadas"] == 3                                        # idempotente (upsert)
    assert bd.execute("SELECT count(*) AS n FROM estadisticas_partido").fetchone()["n"] == len(df)
    cfg = {"corners": {"min_partidos_liga": 200, "decaimiento_xi": 0.0, "l2": 0.5, "l2_arbitro": 20, "min_partidos_arbitro": 15}}
    m, spearman = mx.modelo_corners(bd, liga["id"], cfg, AHORA)
    ids = {r_["nombre"]: r_["id"] for r_ in bd.execute("SELECT id, nombre FROM equipos")}
    assert np.corrcoef([m.ataque[ids[e]] for e in at], list(at.values()))[0, 1] > 0.6
    mx._CACHE_CORNERS.clear()
    assert mx.modelo_corners(bd, liga["id"], {**cfg, "corners": {**cfg["corners"], "min_partidos_liga": 10_000}}, AHORA) is None


# ------------------------------------------------------------------ de punta a punta con el pipeline real (mundo sintético)
def test_punta_a_punta_mercados_y_sonadora_analitica(bd):
    """Pipeline real sobre el mundo multiliga: /mercados y la soñadora analítica funcionan con la predicción de producción y sin
    estadísticas de córners (lo dicen en `avisos`, no inventan)."""
    from golmodel import db
    from golmodel.config import cargar_v3
    from golmodel.ingesta.pipeline import Pipeline, cargar_historico
    from golmodel.integracion import mercados_extendidos as mx
    from golmodel.config import cargar
    from tests.mundo_multiliga import LIGAS, Mundo, cadenas, configuracion
    dsn = bd.info.dsn
    mundo, cfg = Mundo(), configuracion(cargar())
    for liga in LIGAS:
        cargar_historico(bd, liga, mundo.hist[liga], mundo.cuotas_prepartido(liga))
    bd.commit()
    Pipeline(cfg, cadenas(mundo), dsn=dsn, reloj=lambda: mundo.ahora).ejecutar("actualizar_todo")
    c = db.conectar(dsn)
    dia = mundo.jornada.tz_convert("America/Mexico_City").date()
    pid = c.execute("""SELECT p.id FROM partidos p JOIN predicciones pr ON pr.partido_id = p.id JOIN ligas l ON l.id = p.liga_id
                       WHERE l.codigo = 'EPL' AND p.estado = 'programado' LIMIT 1""").fetchone()["id"]
    m = mx.mercados_partido(c, pid, cfg, mundo.ahora)
    assert m["partido_ext"] is not None and m["corners"] is None
    assert any("Córners" in a for a in m["avisos"]) and any("Props" in a for a in m["avisos"])
    out = mx.sonadoras_analiticas_dia(c, dia, mundo.ahora, cfg, cargar_v3(), cuota_min=3, cuota_max=8)
    assert out["estado"] in ("PROPUESTA", "NO_DISPONIBLE")
    if out["ticket"]:
        assert 3 <= out["ticket"]["cuota_combinada"] <= 8 and out["ticket"]["etiqueta"] in ("De Valor (+EV)", "Ticket de Entretenimiento")
    c.close()
