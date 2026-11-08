"""Walk-forward estricto: reentrenar con todo lo anterior a t y predecir [t, t + paso).

Ningún partido con inicio >= t entra al ajuste usado para predecir desde t.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from golmodel.backtest.metricas import brier, log_loss
from golmodel.modelos.dixon_coles import ModeloDC, ajustar
from golmodel.modelos.mercados import probabilidad

CLASES_1X2 = ("1X2:1", "1X2:X", "1X2:2")


def walkforward(df: pd.DataFrame, *, desde: datetime, hasta: datetime, paso_dias: int = 7,
                min_peso_equipo: float = 0.0, **kw_ajuste) -> pd.DataFrame:
    """Devuelve una fila por partido predicho, con p del modelo y resultado.

    min_peso_equipo: si un equipo tiene menos peso efectivo (Σφ de sus partidos),
    el partido se omite y se cuenta (se resolverá con el prior de ascendidos).
    """
    filas = []
    previo: ModeloDC | None = None
    t = pd.Timestamp(desde)
    fin = pd.Timestamp(hasta)
    while t < fin:
        t_sig = min(t + timedelta(days=paso_dias), fin)
        ventana = df[(df["inicio"] >= t) & (df["inicio"] < t_sig) & df["goles_local"].notna()]
        if not ventana.empty:
            modelo = ajustar(df, fecha_corte=t.to_pydatetime(), inicial=previo, **kw_ajuste)
            assert modelo.datos_hasta <= t.to_pydatetime()
            previo = modelo
            for r in ventana.itertuples(index=False):
                omitido = None
                if r.local not in modelo.peso_efectivo or r.visitante not in modelo.peso_efectivo:
                    omitido = "equipo_sin_historial"
                elif min(modelo.peso_efectivo[r.local], modelo.peso_efectivo[r.visitante]) < min_peso_equipo:
                    omitido = "historial_insuficiente"
                if omitido:
                    filas.append({"inicio": r.inicio, "liga": r.liga, "local": r.local,
                                  "visitante": r.visitante, "omitido": omitido})
                    continue
                m = modelo.matriz(r.local, r.visitante)
                gl, gv = int(r.goles_local), int(r.goles_visitante)
                filas.append({
                    "inicio": r.inicio, "liga": r.liga, "local": r.local, "visitante": r.visitante,
                    "omitido": None, "corte": t, "lambda_local": m.lambda_local,
                    "lambda_visitante": m.lambda_visitante,
                    **{s: probabilidad(m, s) for s in (*CLASES_1X2, "OU:2.5:over", "BTTS:si")},
                    "y_1x2": 0 if gl > gv else (1 if gl == gv else 2),
                    "y_over25": int(gl + gv > 2.5), "y_btts": int(gl > 0 and gv > 0),
                })
        t = t_sig
    return pd.DataFrame(filas)


def resumen(pred: pd.DataFrame) -> dict:
    ok = pred[pred["omitido"].isna()]
    p = ok[list(CLASES_1X2)].to_numpy()
    y = ok["y_1x2"].to_numpy(int)
    po = ok["OU:2.5:over"].to_numpy()
    yo = ok["y_over25"].to_numpy(int)
    return {
        "n": int(len(ok)), "omitidos": int(pred["omitido"].notna().sum()),
        "log_loss_1x2": log_loss(p, y), "brier_1x2": brier(p, y),
        "log_loss_ou25": log_loss(np.column_stack([po, 1 - po]), 1 - yo),
    }
