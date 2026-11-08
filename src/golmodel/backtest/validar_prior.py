"""¿El prior de ascendidos mejora las probabilidades? Corte temporal estricto, sin look-ahead.

Para cada corte T (1-oct y 1-dic de cada año con historia suficiente) se ajusta el modelo con datos < T,
SIN y CON prior (estimado solo con temporadas completas < T), y se evalúa la log-loss 1X2 del MODELO SOLO en los
partidos de los siguientes `ventana_dias` donde juega un ascendido de la temporada en curso.
`l2_prior` = fuerza con la que el ascendido se jala hacia el prior (hiperparámetro a elegir con esta prueba).
Resultado: diferencia pareada por partido (con − sin) e IC95% por bootstrap. Negativo = el prior ayuda.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from golmodel.modelos.dixon_coles import ajustar
from golmodel.modelos.mercados import probabilidad
from golmodel.modelos.prior_ascendidos import priors_para


def _ll(modelo, r) -> float | None:
    if r.local not in modelo.peso_efectivo or r.visitante not in modelo.peso_efectivo:
        return None
    m = modelo.matriz(r.local, r.visitante)
    p = [probabilidad(m, s) for s in ("1X2:1", "1X2:X", "1X2:2")]
    y = 0 if r.goles_local > r.goles_visitante else (1 if r.goles_local == r.goles_visitante else 2)
    return float(-np.log(max(p[y], 1e-15)))


def validar(df: pd.DataFrame, ascendidos: set[tuple[str, str]], ligas_con_descenso: set[str], *, xi: float,
            l2: float, l2_priors: list[float], cortes: list[pd.Timestamp], ventana_dias: int = 60, w_xg: float = 0.0,
            rho_prior: tuple[float, float] | None = None, semilla: int = 7) -> dict[float, dict]:
    """df: columnas del modelo + temporada. ascendidos: {(equipo, temporada)}."""
    filas: dict[float, list[dict]] = {k: [] for k in l2_priors}
    for T in cortes:
        pasado = df[df["inicio"] < T]
        if pasado.empty:
            continue
        en_curso = pasado.sort_values("inicio").groupby("liga").tail(1).set_index("liga")["temporada"].to_dict()
        vigentes = set()
        for liga in ligas_con_descenso:
            if liga not in en_curso:
                continue
            q = pasado[(pasado["liga"] == liga) & (pasado["temporada"] == en_curso[liga])]
            equipos = set(q["local"]) | set(q["visitante"])
            vigentes |= {e for e in equipos if (e, en_curso[liga]) in ascendidos}
        priors, diag = priors_para(pasado, vigentes, ascendidos, min_partidos=15)
        if not priors:
            continue
        ventana = df[(df["inicio"] >= T) & (df["inicio"] < T + pd.Timedelta(days=ventana_dias))
                     & df["goles_local"].notna() & df["liga"].isin(ligas_con_descenso)
                     & (df["local"].isin(vigentes) | df["visitante"].isin(vigentes))]
        if ventana.empty:
            continue
        kw = dict(fecha_corte=T.to_pydatetime(), xi=xi, w_xg=w_xg, l2_equipos=l2, l2_ligas=l2, rho_prior=rho_prior)
        sin = ajustar(pasado, **kw)
        for k in l2_priors:
            con = ajustar(pasado, priors=priors, l2_prior=k, inicial=sin, **kw)
            for r in ventana.itertuples(index=False):
                a, b = _ll(sin, r), _ll(con, r)
                if a is None or b is None:
                    continue
                filas[k].append({"corte": str(T.date()), "ll_sin": a, "ll_con": b, "prior_a0": diag["a0"],
                                 "prior_d0": diag["d0"]})
    return {k: _resumen(f, semilla) for k, f in filas.items()}


def _resumen(filas: list[dict], semilla: int) -> dict:
    if not filas:
        return {"n": 0, "veredicto": "sin datos suficientes"}
    d = pd.DataFrame(filas)
    dif = (d["ll_con"] - d["ll_sin"]).to_numpy()
    rng = np.random.default_rng(semilla)
    boots = [rng.choice(dif, len(dif)).mean() for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    veredicto = "AYUDA" if hi < 0 else ("EMPEORA" if lo > 0 else "NO CONCLUYENTE")
    return {"n": int(len(d)), "ll_sin": float(d["ll_sin"].mean()), "ll_con": float(d["ll_con"].mean()),
            "dif_media": float(dif.mean()), "ic95": [float(lo), float(hi)], "veredicto": veredicto,
            "por_corte": d.groupby("corte").agg(n=("ll_sin", "size"), ll_sin=("ll_sin", "mean"),
                                                ll_con=("ll_con", "mean"), a0=("prior_a0", "first"),
                                                d0=("prior_d0", "first")).reset_index().to_dict("records")}
