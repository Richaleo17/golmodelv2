"""Prior de ascendidos: se estima solo con temporadas completas, empuja al equipo nuevo hacia lo típico de un
ascendido y, en un mundo donde los ascendidos son más débiles, mejora la log-loss (sin look-ahead)."""
import numpy as np
import pandas as pd
import pytest

from golmodel.backtest.validar_prior import validar
from golmodel.modelos.dixon_coles import ajustar
from golmodel.modelos.prior_ascendidos import estimar_prior, priors_para


def mundo(seed=3, temporadas=range(2019, 2026), n_eq=18, efecto=-0.45):
    """Una liga con ascensos: cada año salen 3 equipos y entran 3 ascendidos más débiles (ataque y defensa)."""
    rng = np.random.default_rng(seed)
    fuerza = {f"E{i}": (rng.normal(0, 0.25), rng.normal(0, 0.25)) for i in range(n_eq)}
    plantel = list(fuerza)
    filas, asc, nuevo = [], set(), n_eq
    for y in temporadas:
        if y != temporadas[0]:
            for e in plantel[-3:]:
                plantel.remove(e)
            for _ in range(3):
                e = f"E{nuevo}"
                nuevo += 1
                fuerza[e] = (rng.normal(efecto, 0.15), rng.normal(efecto, 0.15))
                plantel.append(e)
                asc.add((e, f"{y}"))
        pares = [(a, b) for a in plantel for b in plantel if a != b]
        rng.shuffle(pares)
        dias = np.linspace(0, 270, len(pares))
        for (a, b), dd in zip(pares, dias):
            la = np.exp(0.2 + 0.2 + fuerza[a][0] - fuerza[b][1])
            lb = np.exp(0.2 + fuerza[b][0] - fuerza[a][1])
            filas.append({"inicio": pd.Timestamp(f"{y}-08-15", tz="UTC") + pd.Timedelta(days=float(dd)), "liga": "L",
                          "temporada": f"{y}", "local": a, "visitante": b,
                          "goles_local": rng.poisson(la), "goles_visitante": rng.poisson(lb)})
    return pd.DataFrame(filas).sort_values("inicio").reset_index(drop=True), asc


def test_prior_estimado_es_negativo_y_no_usa_la_temporada_en_curso():
    df, asc = mundo()
    a0, d0, n = estimar_prior(df, asc)
    assert n >= 15 and a0 < -0.1 and d0 < -0.1                       # los ascendidos rinden menos que el promedio
    # Con un solo año completo (el resto es la temporada en curso) no hay prior que estimar
    solo = df[df["temporada"] == "2019"]
    assert estimar_prior(solo, asc)[2] == 0
    sin_nuevos, _ = priors_para(df, set(), asc)
    assert sin_nuevos == {}


def test_el_prior_empuja_al_ascendido_con_pocos_partidos():
    df, asc = mundo()
    T = pd.Timestamp("2025-10-01", tz="UTC")
    pasado = df[df["inicio"] < T]
    vig = {e for (e, t) in asc if t == "2025"}
    priors, d = priors_para(pasado, vig, asc)
    assert set(priors) == vig and d["n_equipos_temporada"] > 0
    kw = dict(fecha_corte=T.to_pydatetime(), xi=0.002, l2_equipos=1.0, l2_ligas=1.0)
    sin, con = ajustar(pasado, **kw), ajustar(pasado, priors=priors, **kw)
    e = sorted(vig)[0]
    k = sin.equipos.index(e)
    assert con.ataque[k] < sin.ataque[k]                              # el prior lo baja hacia lo típico de un ascendido


def test_validacion_pareada_mejora_donde_los_ascendidos_son_mas_debiles():
    df, asc = mundo()
    cortes = [pd.Timestamp(f"{y}-{m}-01", tz="UTC") for y in (2023, 2024, 2025) for m in ("10-01", "12-01")]
    res = validar(df, asc, {"L"}, xi=0.002, l2=1.0, l2_priors=[1.0, 8.0, 30.0], cortes=cortes)
    assert all(r["n"] > 100 for r in res.values())
    mejor = min(res.values(), key=lambda r: r["dif_media"])
    assert mejor["dif_media"] < -0.002 and mejor["veredicto"] in ("AYUDA", "NO CONCLUYENTE")
    # Con la penalización suave original el prior casi no hace nada; con una propia sí
    assert abs(res[1.0]["dif_media"]) < abs(mejor["dif_media"]) + 1e-9


def test_gradiente_con_l2_por_equipo():
    from golmodel.modelos.dixon_coles import nll_y_gradiente, preparar
    from scipy.optimize import check_grad
    df, _ = mundo(temporadas=range(2024, 2026))
    eqs = sorted(set(df["local"]) | set(df["visitante"]))
    D = preparar(df, pd.Timestamp("2026-01-01", tz="UTC"), 0.002, 0.0, eqs, ["L"])
    n = len(eqs)
    l2 = np.where(np.arange(n) % 3 == 0, 8.0, 1.0)
    z = np.zeros(n)
    x = np.random.default_rng(0).normal(0, 0.1, 2 * n + 2 + 3)
    x[-1] = -0.05
    f = lambda v: nll_y_gradiente(v, D, l2, 0.3, z + 0.2, z - 0.1)[0]
    g = lambda v: nll_y_gradiente(v, D, l2, 0.3, z + 0.2, z - 0.1)[1]
    assert check_grad(f, g, x) / max(1.0, np.linalg.norm(g(x))) < 1e-5
