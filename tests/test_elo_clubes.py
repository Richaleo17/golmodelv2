"""Elo de clubes en ensamble con Dixon-Coles (datos SINTÉTICOS)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from golmodel.config import cargar
from golmodel.modelos import elo_clubes as ec

CFG = cargar()


def _liga(n_temp=4, semilla=5):
    rng = np.random.default_rng(semilla)
    eq = [str(i) for i in range(16)]
    fuerza = dict(zip(eq, rng.normal(0, 0.35, 16)))
    filas, pid = [], 0
    t0 = datetime(2021, 8, 1, tzinfo=timezone.utc)
    for t in range(n_temp):
        for j in range(30):
            orden = rng.permutation(eq)
            for a, b in zip(orden[::2], orden[1::2]):
                ll = np.exp(0.25 + fuerza[a] - fuerza[b] * 0.8)
                lv = np.exp(0.05 + fuerza[b] - fuerza[a] * 0.8)
                filas.append({"partido_id": pid, "inicio": pd.Timestamp(t0 + timedelta(days=365 * t + 7 * j)), "liga": "L1",
                              "temporada": str(2021 + t), "local": a, "visitante": b,
                              "goles_local": float(rng.poisson(ll)), "goles_visitante": float(rng.poisson(lv))})
                pid += 1
    return pd.DataFrame(filas), fuerza


def test_elo_ordena_fuerzas_y_aprende_ventaja_local():
    df, fuerza = _liga()
    elo, hfa, dprev = ec.recorrer(df, CFG)
    assert np.corrcoef([elo[e] for e in fuerza], list(fuerza.values()))[0, 1] > 0.8
    assert hfa["L1"] > 0                                         # el local gana más seguido
    assert len(dprev) == len(df)


def test_logit_ordenado_coherente():
    df, _ = _liga()
    _, _, dprev = ec.recorrer(df, CFG)
    y = np.where(df.goles_local > df.goles_visitante, 0, np.where(df.goles_local == df.goles_visitante, 1, 2))
    par = ec.ajustar_ordenado(dprev.loc[df.partido_id].to_numpy(), y)
    assert par["b"] > 0 and par["c1"] < par["c2"]
    p = ec.probs([-300, 0, 300], par)
    assert np.allclose(p.sum(axis=1), 1) and p[2, 0] > p[1, 0] > p[0, 0] and p[0, 2] > p[2, 2]


def _pred_dc(df, ruido, semilla=1):
    """Walk-forward «falso» de Dixon-Coles: probabilidades de la última temporada con un ruido controlado."""
    rng = np.random.default_rng(semilla)
    ult = df[df.temporada == df.temporada.max()].copy()
    _, _, dprev = ec.recorrer(df, CFG)
    par = {"b": 2.5, "c1": -0.2, "c2": 1.0}
    p = ec.probs(dprev.loc[ult.partido_id].to_numpy() + rng.normal(0, ruido, len(ult)), par)
    ult[["1X2:1", "1X2:X", "1X2:2"]] = p
    ult["y_1x2"] = np.where(ult.goles_local > ult.goles_visitante, 0, np.where(ult.goles_local == ult.goles_visitante, 1, 2))
    return ult


def test_ensamble_se_activa_si_mejora_y_se_aplica():
    df, _ = _liga()
    pred = _pred_dc(df, ruido=400)                               # un «DC» muy ruidoso: el Elo debe ayudar
    ens = ec.calibrar_ensamble(df, pred, {**CFG, "elo_clubes": {**CFG["elo_clubes"], "min_partidos": 50, "min_partidos_liga": 20}})
    assert ens["activo"] and ens["w"] < 1 and ens["log_loss_ensamble"] < ens["log_loss_dc"] and ens["ligas_activas"] == ["L1"]
    nuevo = ec.aplicar_a_pred(pred, df, ens, CFG)
    assert not np.allclose(nuevo[["1X2:1"]].to_numpy(), pred[["1X2:1"]].to_numpy())
    assert np.allclose(nuevo[["1X2:1", "1X2:X", "1X2:2"]].sum(axis=1), 1)
    hist = df.copy()
    p = ec.p_partido(hist, CFG, ens, "L1", "0", "1")
    assert p is not None and abs(sum(p.values()) - 1) < 1e-9
    assert ec.p_partido(hist, CFG, ens, "OTRA", "0", "1") is None
    mz = ec.mezclar({"1X2:1": 0.5, "1X2:X": 0.3, "1X2:2": 0.2}, p, ens["w"])
    assert mz["DC:1X"] == pytest.approx(mz["1X2:1"] + mz["1X2:X"])


def test_ensamble_no_se_activa_si_no_mejora():
    df, _ = _liga()
    pred = _pred_dc(df, ruido=0)                                 # el «DC» ya es el Elo exacto: no hay nada que ganar
    ens = ec.calibrar_ensamble(df, pred, {**CFG, "elo_clubes": {**CFG["elo_clubes"], "min_partidos": 50, "min_partidos_liga": 20}})
    assert not ens["activo"] and ens["ligas_activas"] == [] and "motivo" in ens
    assert ec.aplicar_a_pred(pred, df, ens, CFG) is pred or ec.aplicar_a_pred(pred, df, ens, CFG).equals(pred)
