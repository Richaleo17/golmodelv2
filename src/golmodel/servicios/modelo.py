"""Ajuste diario y calibración semanal del modelo, con versiones inmutables."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from golmodel import __version__
from golmodel.backtest.walkforward import walkforward
from golmodel.config import hash_config
from golmodel.modelos import devig
from golmodel.modelos.calibracion import buscar
from golmodel.modelos.dixon_coles import ModeloDC, ajustar
from golmodel.repositorio import historial

GRUPOS_ALPHA = {"1X2": ("1X2:1", "1X2:X", "1X2:2"), "OU:2.5": ("OU:2.5:over", "OU:2.5:under")}
COL_PRED = {"1X2:1": "1X2:1", "1X2:X": "1X2:X", "1X2:2": "1X2:2", "OU:2.5:over": "OU:2.5:over"}
MIN_PARTIDOS_CALIBRAR = 300


def _rho_prior(cfg: dict) -> tuple[float, float] | None:
    rp = cfg["modelo"].get("rho_prior")
    return None if not rp else (rp["media"], rp["desviacion"])


def _json(o):
    return Jsonb(o, dumps=lambda x: json.dumps(x, default=float))


def ultima_version(conn: psycopg.Connection, tipo: str | None = None) -> dict | None:
    return conn.execute("""SELECT * FROM versiones_modelo WHERE origen = 'produccion'
                           AND (%s::text IS NULL OR tipo_ajuste = %s) ORDER BY creado_en DESC LIMIT 1""",
                        (tipo, tipo)).fetchone()


def hiperparametros_vigentes(conn, cfg: dict) -> dict:
    v = ultima_version(conn, "semanal")
    if v and v["hiperparametros"].get("calibrado"):
        hp = dict(v["hiperparametros"])
        hp["calibrado_en"] = v["creado_en"].isoformat()
        return hp
    m = cfg["modelo"]
    return {"xi": m["decaimiento_xi_inicial"], "w_xg": m["w_xg_inicial"], "l2": m["l2_lambda_inicial"],
            "alpha": {}, "calibrado": False}


def calibrar(df: pd.DataFrame, cfg: dict, ahora: datetime, cuotas_pre: pd.DataFrame) -> dict:
    """Walk-forward sobre el último año: ξ y w_xG por log-loss; luego α por liga y grupo.

    cuotas_pre: cuotas PRE-PARTIDO de la casa de referencia (las disponibles al decidir; nunca
    las de cierre), columnas partido_id, seleccion, cuota.
    """
    m = cfg["modelo"]
    desde = pd.Timestamp(ahora) - timedelta(days=365)
    fin = df[(df["inicio"] >= desde) & df["goles_local"].notna()]
    base = {"xi": m["decaimiento_xi_inicial"], "w_xg": m["w_xg_inicial"], "l2": m["l2_lambda_inicial"],
            "alpha": {}, "calibrado": False}
    if len(fin) < MIN_PARTIDOS_CALIBRAR:
        base["motivo"] = f"solo {len(fin)} partidos en el último año (< {MIN_PARTIDOS_CALIBRAR})"
        return base
    hay_xg = df["xg_local"].notna().any()
    rg = m["rejilla_calibracion"]
    rejilla = {"xi": list(rg["xi"]), "w_xg": list(rg["w_xg"]) if hay_xg else [0.0]}
    fijos = {"l2_equipos": m["l2_lambda_inicial"], "l2_ligas": m["l2_lambda_inicial"],
             "rho_limites": tuple(m["rho_limites"]), "rho_prior": _rho_prior(cfg)}
    tabla = buscar(df, desde=desde.to_pydatetime(), hasta=ahora, rejilla=rejilla, fijos=fijos, paso_dias=14)
    mejor = tabla.iloc[0]
    hp = {"xi": float(mejor["xi"]), "vida_media_dias": float(np.log(2) / mejor["xi"]),
          "w_xg": float(mejor["w_xg"]), "l2": m["l2_lambda_inicial"], "calibrado": True,
          "log_loss_1x2": float(mejor["log_loss_1x2"]), "n_eval": int(mejor["n"]),
          "rejilla": tabla[["xi", "w_xg", "log_loss_1x2"]].to_dict("records"), "alpha": {}}

    pred = walkforward(df, desde=desde.to_pydatetime(), hasta=ahora, paso_dias=14, xi=hp["xi"],
                       w_xg=hp["w_xg"], **fijos)
    pred = pred[pred["omitido"].isna()].merge(df[["partido_id", "inicio", "local", "visitante"]],
                                              on=["inicio", "local", "visitante"])
    hp["alpha"] = calibrar_alpha(pred, cuotas_pre, cfg["mercado"]["metodo_devig"])
    return hp


def calibrar_alpha(pred: pd.DataFrame, cuotas_pre: pd.DataFrame, metodo: str) -> dict:
    """α por liga y grupo minimizando el log-loss de p_adj = α·p_modelo + (1−α)·p_mercado."""
    salida: dict = {}
    if cuotas_pre.empty:
        return salida
    piv = cuotas_pre.pivot_table(index="partido_id", columns="seleccion", values="cuota", aggfunc="last")
    rejilla = np.round(np.arange(0, 1.0001, 0.05), 2)
    for grupo, sels in GRUPOS_ALPHA.items():
        if not set(sels) <= set(piv.columns):
            continue
        comp = piv[list(sels)].dropna()
        pm = pd.DataFrame([devig.quitar_margen(r, metodo) for r in comp.to_numpy()], index=comp.index,
                          columns=[f"{s}_mk" for s in sels])
        datos = pred.merge(pm, left_on="partido_id", right_index=True)

        def calcular(d: pd.DataFrame) -> dict:
            if grupo == "1X2":
                p_mod = d[["1X2:1", "1X2:X", "1X2:2"]].to_numpy()
                p_mk = d[["1X2:1_mk", "1X2:X_mk", "1X2:2_mk"]].to_numpy()
                y = d["y_1x2"].to_numpy(int)
            else:
                po = d["OU:2.5:over"].to_numpy()
                p_mod = np.column_stack([po, 1 - po])
                p_mk = d[["OU:2.5:over_mk", "OU:2.5:under_mk"]].to_numpy()
                y = 1 - d["y_over25"].to_numpy(int)
            idx = np.arange(len(y))
            ll = [float(-np.mean(np.log(np.clip(a * p_mod[idx, y] + (1 - a) * p_mk[idx, y], 1e-15, 1))))
                  for a in rejilla]
            k = int(np.argmin(ll))
            return {"valor": float(rejilla[k]), "n": int(len(y)), "log_loss_adj": ll[k],
                    "log_loss_modelo": ll[-1], "log_loss_mercado": ll[0]}

        for liga, d in datos.groupby("liga"):
            if len(d) >= MIN_PARTIDOS_CALIBRAR:
                salida.setdefault(liga, {})[grupo] = calcular(d)
        # α AGRUPADO de todas las ligas: respaldo para las ligas con pocos partidos (p. ej. Liga MX, con dos
        # torneos cortos al año), mejor que un valor fijo que le daría demasiado peso al modelo.
        if len(datos) >= MIN_PARTIDOS_CALIBRAR:
            salida.setdefault("_global", {})[grupo] = calcular(datos)
    return salida


def alpha_para(hp: dict, cfg: dict, liga: str, grupo: str) -> tuple[float, bool]:
    """(α, calibrado). Orden: α propio de la liga y mercado > factor × α(1X2 de la liga) > α agrupado de todas
    las ligas (el mercado correspondiente, o su 1X2 × factor) > α por defecto (conservador)."""
    a = hp.get("alpha", {}).get(liga, {})
    if grupo in a:
        return a[grupo]["valor"], True
    mk = cfg["mercado"]
    if "1X2" in a:
        return round(mk["factor_alpha_no_calibrado"] * a["1X2"]["valor"], 4), False
    g = hp.get("alpha", {}).get("_global", {})
    if grupo in g:
        return g[grupo]["valor"], False
    if "1X2" in g:
        return round(mk["factor_alpha_no_calibrado"] * g["1X2"]["valor"], 4), False
    return mk["alpha_por_defecto"], False


def reajustar(conn, cfg: dict, ahora: datetime, *, semanal: bool,
              cuotas_pre: pd.DataFrame | None = None) -> tuple[int, ModeloDC, dict]:
    df = historial(conn, hasta=ahora)
    hp = calibrar(df, cfg, ahora, cuotas_pre if cuotas_pre is not None else pd.DataFrame()) if semanal \
        else hiperparametros_vigentes(conn, cfg)
    m = cfg["modelo"]
    modelo = ajustar(df, fecha_corte=ahora, xi=hp["xi"], w_xg=hp["w_xg"], l2_equipos=hp["l2"], l2_ligas=hp["l2"],
                     rho_limites=tuple(m["rho_limites"]), rho_prior=_rho_prior(cfg))
    hp_guardar = {**hp, "globales": {"rho": modelo.rho, "mu_global": modelo.mu_global,
                                     "ventaja_global": modelo.ventaja_global}}
    vid = conn.execute("""
        INSERT INTO versiones_modelo (origen, tipo_ajuste, datos_hasta, n_partidos, hiperparametros, config,
                                      config_hash, codigo_version, convergencia)
        VALUES ('produccion', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, ("semanal" if semanal else "diario", modelo.datos_hasta, modelo.n_partidos, _json(hp_guardar),
          _json(cfg), hash_config(cfg), __version__, _json(modelo.convergencia))).fetchone()["id"]
    ligas_id = {r["codigo"]: r["id"] for r in conn.execute("SELECT id, codigo FROM ligas")}
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO parametros_liga VALUES (%s, %s, %s, %s)",
                        [(vid, ligas_id[l], float(modelo.mu[k]), float(modelo.ventaja_local[k]))
                         for k, l in enumerate(modelo.ligas)])
        cur.executemany("""INSERT INTO parametros_equipo (version_id, equipo_id, liga_id, ataque, defensa,
                           peso_efectivo) VALUES (%s, %s, %s, %s, %s, %s)""",
                        [(vid, int(e), ligas_id[modelo.liga_de_equipo[e]], float(modelo.ataque[k]),
                          float(modelo.defensa[k]), modelo.peso_efectivo[e]) for k, e in enumerate(modelo.equipos)])
    return vid, modelo, hp_guardar


def cargar_modelo(conn, version_id: int | None = None) -> tuple[int, ModeloDC, dict] | None:
    v = conn.execute("SELECT * FROM versiones_modelo WHERE id = %s", (version_id,)).fetchone() if version_id \
        else ultima_version(conn)
    if v is None:
        return None
    hp = v["hiperparametros"]
    ligas = conn.execute("""SELECT l.codigo, pl.mu, pl.ventaja_local FROM parametros_liga pl
                            JOIN ligas l ON l.id = pl.liga_id WHERE version_id = %s ORDER BY l.codigo""",
                         (v["id"],)).fetchall()
    eqs = conn.execute("""SELECT pe.equipo_id::text AS e, l.codigo, pe.ataque, pe.defensa, pe.peso_efectivo
                          FROM parametros_equipo pe JOIN ligas l ON l.id = pe.liga_id
                          WHERE version_id = %s ORDER BY pe.equipo_id::text""", (v["id"],)).fetchall()
    g = hp["globales"]
    modelo = ModeloDC(
        equipos=[r["e"] for r in eqs], ligas=[r["codigo"] for r in ligas],
        ataque=np.array([r["ataque"] for r in eqs]), defensa=np.array([r["defensa"] for r in eqs]),
        mu=np.array([r["mu"] for r in ligas]), ventaja_local=np.array([r["ventaja_local"] for r in ligas]),
        mu_global=g["mu_global"], ventaja_global=g["ventaja_global"], rho=g["rho"],
        liga_de_equipo={r["e"]: r["codigo"] for r in eqs}, xi=hp["xi"], w_xg=hp["w_xg"],
        datos_hasta=v["datos_hasta"], n_partidos=v["n_partidos"],
        peso_efectivo={r["e"]: r["peso_efectivo"] for r in eqs}, convergencia=v["convergencia"] or {})
    return v["id"], modelo, hp
