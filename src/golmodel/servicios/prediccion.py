"""Recálculo del Módulo 1 y el Módulo 2: predicción inmutable -> picks -> parlays -> exposición.

Política de paper trading (para que ROI y CLV no se inflen):
* Un pick por partido y selección; como máximo `max_simples_con_stake_por_partido` con stake por partido.
* Un parlay registrado por día (el primero que se genera con cuotas vigentes). Los recálculos
  posteriores muestran alternativas en vivo, pero no se registran como apuestas nuevas.
* Un pick ya registrado nunca se borra: si pierde valor recibe el evento 'valor_perdido'.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

from golmodel.config import cargar_v3, hash_config
from golmodel.modelos import crs as crs_mod
from golmodel.modelos import devig
from golmodel.modelos.combinacion import matriz_mercado, p_ajustada, p_ajustada_push
from golmodel.modelos.contexto import ajuste_equipo
from golmodel.modelos.dixon_coles import ModeloDC
from golmodel.modelos.matriz import Matriz
from golmodel.modelos.mercados import CONDICIONES, GRUPO_DE, GRUPOS, PUSH, probabilidad, probabilidad_push
from golmodel.motor.bankroll import (
    Apuesta,
    asignar_exposicion,
    kelly,
    kelly_push,
    log_crecimiento,
    log_crecimiento_push,
    stake_u,
)
from golmodel.motor.valor import ev, ev_con_push, umbral_efectivo
from golmodel.servicios.modelo import alpha_para
from golmodel import repositorio as repo

AGREGADOS = {"promedio", "maximo"}      # no son casas reales: no entran al consenso
EVENTOS_CIERRAN = ("valor_perdido", "anulado", "descartado_usuario")


def _j(o):
    return Jsonb(o, dumps=lambda x: json.dumps(x, default=str, ensure_ascii=False))


def vigente(capturada: datetime, inicio: datetime, ahora: datetime, cfg: dict) -> bool:
    f = cfg["frescura"]
    limite = f["cuotas_max_min_cerca"] if (inicio - ahora) < timedelta(hours=f["horas_cerca_inicio"]) \
        else f["cuotas_max_min"]
    return (ahora - capturada) <= timedelta(minutes=limite)


def grupo_alpha(s: str) -> str:
    g = GRUPO_DE[s]
    return "1X2" if g in ("1X2", "DC") else g


def probs_mercado(cuotas: pd.DataFrame, cfg: dict) -> tuple[dict[str, float], set[str], dict[str, float]]:
    """(p sin margen por selección, selecciones con referencia sustituta, q condicional de AH ±1.0)."""
    mk = cfg["mercado"]
    ref, metodo = mk["casa_referencia"].lower(), mk["metodo_devig"]
    p: dict[str, float] = {}
    sust: set[str] = set()
    if cuotas.empty:
        return p, sust, {}
    for grupo, sels in GRUPOS.items():
        sub = cuotas[cuotas["seleccion"].isin(sels)]
        r = sub[sub["casa"] == ref].set_index("seleccion")["cuota"]
        if set(sels) <= set(r.index):
            p.update(dict(zip(sels, devig.quitar_margen([r[s] for s in sels], metodo))))
            continue
        por_casa = {}
        for casa, d in sub[~sub["casa"].isin(AGREGADOS | {"manual"})].groupby("casa"):
            dd = d.set_index("seleccion")["cuota"]
            if set(sels) <= set(dd.index):
                por_casa[casa] = [dd[s] for s in sels]
        c = devig.consenso(por_casa, metodo=metodo, min_casas=mk["min_casas_consenso"])
        if c is not None:
            p.update(dict(zip(sels, c)))
            sust |= set(sels)
    if all(k in p for k in ("1X2:1", "1X2:X", "1X2:2")):
        p["DC:1X"], p["DC:X2"], p["DC:12"] = p["1X2:1"] + p["1X2:X"], p["1X2:X"] + p["1X2:2"], p["1X2:1"] + p["1X2:2"]
        if "1X2:1" in sust:
            sust |= {"DC:1X", "DC:X2", "DC:12"}
    q_push = {s: p.pop(s) for s in list(p) if s in PUSH}     # en ±1.0 es P(gana | no push)
    return p, sust, q_push


@dataclass
class Contexto:
    partido: dict
    prediccion_id: int
    matriz: Matriz
    matriz_decision: Matriz
    crs: crs_mod.ResultadoCRS
    sel: dict[str, dict]
    cuotas_ejec: dict[str, dict]
    p_mercado: dict[str, float]
    sustitutas: set[str]
    mm: Matriz | None
    avisos: list[str] = field(default_factory=list)
    sugerencia_ah: dict | None = None


def _calendario_crs(conn, ids: list[int], inicio: datetime) -> tuple[pd.DataFrame, bool]:
    filas = conn.execute("""SELECT equipo_id::text AS equipo, inicio, competicion, es_local, es_liga
                            FROM calendario_equipos WHERE equipo_id = ANY(%s) AND inicio < %s
                              AND inicio > %s - interval '10 days'""", (ids, inicio, inicio)).fetchall()
    dias = [(inicio - timedelta(days=k)).date() for k in range(0, 5)]
    cub = {r["dia"] for r in conn.execute("SELECT dia FROM calendario_cobertura WHERE dia = ANY(%s)", (dias,))}
    df = pd.DataFrame(filas, columns=["equipo", "inicio", "competicion", "es_local", "es_liga"])
    if not df.empty:
        df["inicio"] = pd.to_datetime(df["inicio"], utc=True)
    return df, set(dias) <= cub


def _ajuste_bajas(conn, partido: dict, equipo_id: int, cfg: dict) -> tuple[float, dict]:
    filas = conn.execute("""
        SELECT DISTINCT ON (jugador_nombre) jugador_nombre, tipo, participacion
        FROM contexto_lesiones WHERE partido_id = %s AND equipo_id = %s AND vigente
        ORDER BY jugador_nombre, capturado_en DESC
    """, (partido["id"], equipo_id)).fetchall()
    if not filas:
        return 0.0, {}
    if partido["liga"] not in cfg["contexto"]["ligas_con_ajuste"]:
        return 0.0, {"aviso": "bajas informadas; sin ajuste de λ en esta liga (sin xG+xA por jugador)",
                     "bajas": [f["jugador_nombre"] for f in filas]}
    prod = pd.DataFrame(conn.execute("""
        SELECT j.nombre AS jugador, jp.minutos, jp.xg::float8 AS xg, jp.xa::float8 AS xa
        FROM jugador_produccion jp JOIN jugadores j ON j.id = jp.jugador_id
        WHERE jp.equipo_id = %s AND jp.temporada = %s AND jp.fuente = 'understat'
    """, (equipo_id, partido["temporada"])).fetchall(), columns=["jugador", "minutos", "xg", "xa"])
    a = ajuste_equipo([{"jugador": f["jugador_nombre"], "tipo": f["tipo"]} for f in filas], prod, cfg)
    return a.fraccion, {"fraccion": a.fraccion, "detalle": list(a.detalle), "no_resueltos": list(a.no_resueltos),
                        "topado": a.topado}


def construir_mercado(conn, cfg: dict, modelo: ModeloDC, version_id: int, partido: dict, ahora: datetime) -> int | None:
    """Predicción de una competición SIN modelo propio (Champions, selecciones…): todo sale del mercado.

    p_mercado = cuotas sin margen (Pinnacle, o consenso de ≥3 casas); los mercados derivados (BTTS, O/U, hándicap)
    salen de la matriz implícita del mercado (λ ajustadas a 1X2 + O/U 2.5). α = 0: la decisión ES el mercado.
    No se generan picks ni CRS: sin modelo no hay una ventaja propia que medir."""
    gmax = cfg["modelo"]["goles_max"]
    cuotas = repo.cuotas_vigentes(conn, partido["id"])
    p_mk, sust, q_push = probs_mercado(cuotas, cfg)
    mmk = matriz_mercado(p_mk, modelo.rho, gmax=gmax) if p_mk else None
    if mmk is None or not mmk.usable:
        return None
    m = mmk.matriz
    sel = {}
    for s in CONDICIONES:
        pw, pp = probabilidad(m, s), probabilidad_push(m, s)
        if s in PUSH and s in q_push:
            pm, pad = q_push[s] * (1 - pp), q_push[s] * (1 - pp)
        elif s in p_mk:
            pm = pad = p_mk[s]
        else:
            pm, pad = None, None         # derivado de la matriz implícita: el panel lo calcula de lambda_mercado
        sel[s] = (pw, pp, pm, pad)
    pid = conn.execute("""
        INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
            rho, ajuste_local, ajuste_visitante, matriz, masa_truncada, lambda_mercado_local, lambda_mercado_visitante,
            cr_scores, crs_completo, crs_alerta, crs_version, crs_modo_ajuste)
        VALUES (%s,%s,'produccion',%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s,%s,false,'INSUFICIENTE',%s,%s)
        RETURNING id
    """, (partido["id"], version_id, ahora, ahora, m.lambda_local, m.lambda_visitante, m.rho, m.aplanada(),
          m.masa_truncada, m.lambda_local, m.lambda_visitante, _j({"_solo_mercado": True}),
          cfg["crs"]["version"], cfg["crs"]["ajuste_favorito"]["modo"])).fetchone()["id"]
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO predicciones_seleccion (prediccion_id, seleccion, origen, p_modelo, p_modelo_sin_ajuste, p_push,
                p_mercado, metodo_devig, p_mercado_consenso, alpha, alpha_calibrado, p_adj)
            VALUES (%s, %s, 'produccion', %s, %s, %s, %s, %s, %s, 0.0, false, %s)
        """, [(pid, s, pw, pw, pp, pm, cfg["mercado"]["metodo_devig"] if pm is not None else None, s in sust, pad)
              for s, (pw, pp, pm, pad) in sel.items()])
    return pid


def cuotas_ejecucion(cuotas: pd.DataFrame, partido: dict, ahora: datetime, cfg: dict) -> dict[str, dict]:
    """Cuota de ejecución por selección: casa fija; la captura manual VIGENTE la reemplaza.
    Fuente única: la usan `construir` (V2) y `integracion/candidatas.py` (V3) para no divergir."""
    ejec: dict[str, dict] = {}
    if not cuotas.empty:
        for casa in (cfg["mercado"]["casa_ejecucion"], "manual"):
            for r in cuotas[cuotas["casa"] == casa].itertuples():
                vig = vigente(r.capturada_en, partido["inicio"], ahora, cfg)
                if casa == "manual" and not vig:
                    continue
                ejec[r.seleccion] = {"cuota": float(r.cuota), "cuota_id": int(r.id), "casa_id": int(r.casa_id),
                                     "capturada_en": r.capturada_en, "vigente": vig, "casa": casa}
    return ejec


def construir(conn, cfg: dict, modelo: ModeloDC, version_id: int, hp: dict, partido: dict,
              hist: pd.DataFrame, ahora: datetime) -> Contexto | None:
    kl, kv = str(partido["local_id"]), str(partido["visitante_id"])
    if kl not in modelo.peso_efectivo or kv not in modelo.peso_efectivo:
        return None
    gmax = cfg["modelo"]["goles_max"]
    aj_l, det_l = _ajuste_bajas(conn, partido, partido["local_id"], cfg)
    aj_v, det_v = _ajuste_bajas(conn, partido, partido["visitante_id"], cfg)
    base = modelo.matriz(kl, kv, gmax=gmax)
    m = modelo.matriz(kl, kv, gmax=gmax, ajuste_local=aj_l, ajuste_visitante=aj_v)

    cuotas = repo.cuotas_vigentes(conn, partido["id"])
    p_mk, sust, q_push = probs_mercado(cuotas, cfg)
    mmk = matriz_mercado(p_mk, modelo.rho, gmax=gmax) if p_mk else None
    mm = mmk.matriz if (mmk and mmk.usable) else None

    # --- CRS antes de las cuotas justas finales
    cal, cal_ok = _calendario_crs(conn, [partido["local_id"], partido["visitante_id"]], partido["inicio"])
    h_liga = hist[hist["liga"] == partido["liga"]]
    entrada = crs_mod.EntradaCRS(
        inicio=partido["inicio"], local=kl, visitante=kv,
        p_modelo_local=probabilidad(m, "1X2:1"), p_modelo_visitante=probabilidad(m, "1X2:2"),
        p_mercado_local=p_mk.get("1X2:1"), p_mercado_visitante=p_mk.get("1X2:2"),
        historial=h_liga, calendario=cal, calendario_completo=cal_ok,
        tabla=crs_mod.tabla(h_liga, partido["liga"], partido["temporada"], partido["inicio"]),
        fronteras=cfg["crs"]["objetivos"].get(partido["liga"], []))
    res = crs_mod.calcular(entrada, cfg)
    m_crs = crs_mod.matriz_ajustada(m, res, cfg, gmax) if res.veto else None
    modo = cfg["crs"]["ajuste_favorito"]["modo"]
    m_dec = m_crs if (m_crs is not None and modo == "activo") else m

    # --- probabilidades por selección
    sel: dict[str, dict] = {}
    for s in CONDICIONES:
        alpha, cal_a = alpha_para(hp, cfg, partido["liga"], grupo_alpha(s))
        pw, pp = probabilidad(m_dec, s), probabilidad_push(m_dec, s)
        fila = {"p_modelo": pw, "p_sin_ajuste": probabilidad(base, s), "p_push": pp,
                "p_crs": probabilidad(m_crs, s) if m_crs is not None else None,
                "alpha": alpha, "alpha_calibrado": cal_a, "p_mercado": None, "p_adj": None, "p_adj_pierde": None}
        if s in PUSH and s in q_push:
            w, _, l = p_ajustada_push(pw, pp, 1 - pw - pp, q_push[s], alpha)
            fila.update(p_mercado=q_push[s] * (1 - pp), p_adj=w, p_adj_pierde=l)
        elif s in p_mk:
            fila.update(p_mercado=p_mk[s], p_adj=p_ajustada(pw, p_mk[s], alpha))
        sel[s] = fila

    fav = res.favorito
    sfav = None if fav is None else ("1X2:1" if fav == "local" else "1X2:2")
    pid = conn.execute("""
        INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local,
            lambda_visitante, rho, ajuste_local, ajuste_visitante, matriz, masa_truncada,
            lambda_mercado_local, lambda_mercado_visitante, lote_cuotas,
            cr_scores, crs_total, crs_puntos_brutos, crs_maximo_disponible, crs_completo, crs_alerta, crs_favorito,
            crs_p_fav_modelo, crs_p_fav_mercado, crs_veto, crs_veto_simples, crs_selecciones_vetadas,
            crs_version, crs_modo_ajuste, matriz_crs)
        VALUES (%s,%s,'produccion',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (partido["id"], version_id, ahora, ahora, m.lambda_local, m.lambda_visitante, m.rho, aj_l, aj_v,
          m.aplanada(), m.masa_truncada,
          mm.lambda_local if mm else None, mm.lambda_visitante if mm else None, None,
          _j({k: v for k, v in res.a_json()["componentes"].items()} | {"_bajas": {"local": det_l, "visitante": det_v}}),
          res.total, res.puntos_brutos, res.maximo_disponible, res.completo, res.alerta, fav,
          probabilidad(m, sfav) if sfav else None, p_mk.get(sfav) if sfav else None,
          res.veto, res.veto and cfg["crs"]["veto"]["aplica_simples"], list(res.selecciones_vetadas),
          res.version, modo, m_crs.aplanada() if m_crs is not None else None)).fetchone()["id"]
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO predicciones_seleccion (prediccion_id, seleccion, origen, p_modelo, p_modelo_sin_ajuste,
                p_push, p_modelo_crs, p_mercado, metodo_devig, p_mercado_consenso, alpha, alpha_calibrado, p_adj)
            VALUES (%s, %s, 'produccion', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, [(pid, s, f["p_modelo"], f["p_sin_ajuste"], f["p_push"], f["p_crs"], f["p_mercado"],
               cfg["mercado"]["metodo_devig"] if f["p_mercado"] is not None else None, s in sust, f["alpha"],
               f["alpha_calibrado"], f["p_adj"]) for s, f in sel.items()])

    # --- cuotas de ejecución: casa fija; la captura manual vigente la reemplaza
    ejec = cuotas_ejecucion(cuotas, partido, ahora, cfg)
    ctx = Contexto(partido, pid, m, m_dec, res, sel, ejec, p_mk, sust, mm)
    if res.veto:
        ctx.sugerencia_ah = {"base": crs_mod.sugerencia_handicap(m, fav),
                             "con_ajuste_crs": crs_mod.sugerencia_handicap(m_crs, fav)}
    if not ejec:
        ctx.avisos.append("sin cuotas de ejecución")
    return ctx


def simples(ctx: Contexto, cfg: dict) -> list[dict]:
    """Selecciones del Módulo 1 con valor (EV >= umbral efectivo) y cuota vigente."""
    b = cfg["bankroll"]
    vetadas = set(ctx.crs.selecciones_vetadas) if (ctx.crs.veto and cfg["crs"]["veto"]["aplica_simples"]) else set()
    out = []
    for s, q in ctx.cuotas_ejec.items():
        f = ctx.sel.get(s)
        if not q["vigente"] or f is None or f["p_adj"] is None or s in vetadas:
            continue
        derivado = s.startswith("DC:")
        u = umbral_efectivo(cfg, alpha_calibrado=f["alpha_calibrado"] and not derivado,
                            referencia_sustituta=s in ctx.sustitutas)
        if s in PUSH:
            v = ev_con_push(f["p_adj"], f["p_adj_pierde"], q["cuota"])
            k = kelly_push(f["p_adj"], f["p_adj_pierde"], q["cuota"])
        else:
            v, k = ev(f["p_adj"], q["cuota"]), kelly(f["p_adj"], q["cuota"])
        if v < u.valor:
            continue
        st = stake_u(k, fraccion=b["kelly_fraccion_simple"], max_u=b["stake_max_simple_u"],
                     unidad_pct=b["unidad_pct_banca"])
        fr = st * b["unidad_pct_banca"]
        g = (log_crecimiento_push(f["p_adj"], f["p_adj_pierde"], q["cuota"], fr) if s in PUSH
             else log_crecimiento(f["p_adj"], q["cuota"], fr))
        out.append({"seleccion": s, "ev": v, "umbral": u.valor, "kelly": k, "stake_u": st, "log_crec": g, **q})
    return out


# ------------------------------------------------------------------ orquestación

def _picks_abiertos(conn, dia: date) -> list[dict]:
    return conn.execute("""
        SELECT p.*, pa.inicio, dia_mx(pa.inicio) AS dia,
               (SELECT tipo FROM pick_eventos e WHERE e.pick_id = p.id ORDER BY creado_en DESC LIMIT 1) AS ultimo
        FROM picks p JOIN partidos pa ON pa.id = p.partido_id
        WHERE p.origen IN ('paper','real') AND dia_mx(pa.inicio) = %s
          AND NOT EXISTS (SELECT 1 FROM pick_liquidaciones l WHERE l.pick_id = p.id)
    """, (dia,)).fetchall()


def recalcular(conn, cfg: dict, modelo: ModeloDC, version_id: int, hp: dict, dias: list[date],
               ahora: datetime, cfg_v3: dict | None = None) -> dict:
    if cfg_v3 is None:                           # por defecto se usa la política V3 del repo; si no existe, sin ticket (con aviso)
        try:
            cfg_v3 = cargar_v3()
        except FileNotFoundError:
            cfg_v3 = None
    hist = repo.historial(conn, hasta=ahora)
    resumen = {"predicciones": 0, "omitidos": [], "picks_nuevos": 0, "valor_perdido": 0,
               "cuota_no_vigente": 0, "parlays_registrados": 0, "crs_alta": 0, "avisos": []}
    ch = hash_config(cfg)
    b = cfg["bankroll"]
    for dia in dias:
        ctxs: list[Contexto] = []
        for p in repo.partidos_de_dias(conn, [dia]):
            if p["inicio"] <= ahora:
                continue
            if p.get("solo_mercado"):                    # competición sin modelo propio: solo probabilidades del mercado
                if construir_mercado(conn, cfg, modelo, version_id, p, ahora) is not None:
                    resumen["predicciones_mercado"] = resumen.get("predicciones_mercado", 0) + 1
                continue
            ctx = construir(conn, cfg, modelo, version_id, hp, p, hist, ahora)
            if ctx is None:
                resumen["omitidos"].append({"partido_id": p["id"], "motivo": "equipo sin historial en el modelo"})
                continue
            ctxs.append(ctx)
            resumen["predicciones"] += 1
            resumen["crs_alta"] += ctx.crs.alerta == "ALTA"
        por_partido = {c.partido["id"]: c for c in ctxs}

        # 1) Picks existentes: ¿siguen valiendo?
        abiertos = [pk for pk in _picks_abiertos(conn, dia) if pk["ultimo"] not in EVENTOS_CIERRAN]
        for pk in abiertos:
            c = por_partido.get(pk["partido_id"])
            if c is None:
                continue
            q, f = c.cuotas_ejec.get(pk["seleccion"]), c.sel[pk["seleccion"]]
            if q is None or not q["vigente"]:
                if pk["ultimo"] != "cuota_no_vigente":
                    conn.execute("INSERT INTO pick_eventos (pick_id, tipo) VALUES (%s, 'cuota_no_vigente')", (pk["id"],))
                    resumen["cuota_no_vigente"] += 1
                continue
            vetado = c.crs.veto and pk["seleccion"] in c.crs.selecciones_vetadas
            v = None if f["p_adj"] is None else (ev_con_push(f["p_adj"], f["p_adj_pierde"], q["cuota"])
                                                 if pk["seleccion"] in PUSH else ev(f["p_adj"], q["cuota"]))
            if vetado or v is None or v < float(pk["umbral_edge"]):
                conn.execute("INSERT INTO pick_eventos (pick_id, tipo, detalle) VALUES (%s, 'valor_perdido', %s)",
                             (pk["id"], _j({"ev_nuevo": v, "cuota_actual": q["cuota"], "p_adj_nueva": f["p_adj"],
                                            "prediccion_id": c.prediccion_id, "crs_veto": vetado})))
                resumen["valor_perdido"] += 1
            elif pk["ultimo"] == "cuota_no_vigente":
                conn.execute("INSERT INTO pick_eventos (pick_id, tipo) VALUES (%s, 'cuota_vigente')", (pk["id"],))

        # 2) Exposición ya comprometida en el día
        abiertos = [pk for pk in _picks_abiertos(conn, dia) if pk["ultimo"] not in EVENTOS_CIERRAN]
        usado = sum(float(pk["stake_u"]) for pk in abiertos)
        parlay_dia = conn.execute("""SELECT stake_u FROM parlays_sugeridos WHERE dia = %s AND origen = 'paper'
                                     AND ranking = 1 ORDER BY creado_en LIMIT 1""", (dia,)).fetchone()
        usado += float(parlay_dia["stake_u"]) if parlay_dia else 0.0
        con_simple = {pk["partido_id"] for pk in abiertos}
        ya = {(pk["partido_id"], pk["seleccion"]) for pk in abiertos}

        # 3) Candidatos: simples (Módulo 1) y parlay (Módulo 2)
        apuestas, cand = [], {}
        for c in ctxs:
            for s in simples(c, cfg):
                pid = c.partido["id"]
                if pid in con_simple or (pid, s["seleccion"]) in ya:
                    continue
                key = f"s:{pid}:{s['seleccion']}"
                cand[key] = (c, s)
                apuestas.append(Apuesta(key, "simple", frozenset({pid}), s["stake_u"], s["log_crec"]))
        # Los parlays YA NO se arman con modulo2.generar (producto de probabilidades = independencia): el ticket del día lo
        # propone V3 (probabilidad conjunta real) más abajo y, bajo modo_experimental, NO es promovible a paper/real, así
        # que no entra a la asignación de exposición.
        asign = asignar_exposicion(apuestas, max_diaria_u=max(b["exposicion_diaria_max_u"] - usado, 0.0),
                                   max_simples_por_partido=b["max_simples_con_stake_por_partido"])
        cierres = set()
        for a in asign.apuestas:
            if a.tipo == "simple":
                c, s = cand[a.id]
                f = c.sel[s["seleccion"]]
                conn.execute("""
                    INSERT INTO picks (prediccion_id, seleccion, partido_id, origen, cuota_id, casa_id, cuota_sugerida,
                        p_modelo, p_mercado, p_adj, ev, umbral_edge, kelly_completo, stake_u, config_hash,
                        crs_total, crs_alerta)
                    VALUES (%s,%s,%s,'paper',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (c.prediccion_id, s["seleccion"], c.partido["id"], s["cuota_id"], s["casa_id"], s["cuota"],
                      f["p_modelo"], f["p_mercado"], f["p_adj"], s["ev"], s["umbral"], s["kelly"], a.stake_final_u,
                      ch, c.crs.total, c.crs.alerta))
                resumen["picks_nuevos"] += 1
                cierres.add(c.partido["id"])
        _ticket_v3(conn, dia, ahora, cfg, resumen, cfg_v3, cierres)
        for pid in cierres:
            p = por_partido[pid].partido
            conn.execute("""INSERT INTO cierres_programados (partido_id, programado_para) VALUES (%s, %s)
                            ON CONFLICT (partido_id) DO UPDATE SET programado_para = EXCLUDED.programado_para
                            WHERE cierres_programados.estado = 'pendiente'""",
                         (pid, p["inicio"] - timedelta(minutes=cfg["cierre"]["minutos_antes"])))
        resumen["avisos"] += [f"{e.id}: {e.nota}" for e in asign.excluidas if e.nota]
    return resumen


def _ticket_v3(conn, dia: date, ahora: datetime, cfg: dict, resumen: dict, cfg_v3: dict | None, cierres: set) -> None:
    """Ejecuta V3 (candidatas -> tickets.py + dependencia.py -> cerebro2) y guarda la decisión en `auditoria_decisiones`.
    Corre en un SAVEPOINT: si algo falla (p. ej. la migración 004 sin aplicar) se registra un aviso y NO se cae a
    independencia ni se pierden los picks simples. Sin `cfg_v3` (config V3 ausente) no se genera ticket, con aviso."""
    from golmodel.integracion import benchmark
    if cfg_v3 is None:
        resumen["avisos"].append("V3: config/v3_umbrales.yaml no cargada; sin ticket V3 (no se usa independencia).")
        return
    try:
        with conn.transaction():
            d = benchmark.generar_decision_v3(conn, dia, ahora, cfg, cfg_v3)
            if d.error is not None:
                resumen["avisos"].append(f"V3: ABSTENCIÓN por error en tickets/dependencia ({d.error}); sin ticket.")
            ids = benchmark.persistir(conn, d)
    except Exception as e:                       # noqa: BLE001 — fail-closed: sin ticket, nunca independencia
        resumen["avisos"].append(f"V3: no se pudo generar/guardar la decisión ({type(e).__name__}: {e}); sin ticket.")
        resumen["tickets_v3"] = resumen.get("tickets_v3", []) + [{"dia": str(dia), "estado": "ABSTENCION", "error": type(e).__name__}]
        return
    resumen["tickets_v3"] = resumen.get("tickets_v3", []) + [
        {"dia": str(dia), "estado": d.estado, "ticket": d.ticket is not None, "n_candidatas": len(d.candidatas.candidatas),
         "motivo": None if d.salida is None else d.salida.auditoria.motivo_abstencion, "ids": ids}]
    if d.ticket is not None:
        cierres |= {pt.partido_id for pt in d.ticket.piernas}
