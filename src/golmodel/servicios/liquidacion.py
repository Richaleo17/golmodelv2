"""Liquidación de picks y parlays, y cálculo de CLV.

Reglas de precisión:
* Solo se liquida con resultado CONFIRMADO y sin conflictos abiertos entre fuentes.
* Mercados a 90 minutos (el marcador guardado ya es el reglamentario).
* CLV principal = p_cierre_justa(casa de referencia, sin margen) × cuota_tomada − 1.
  CLV secundario = log(cuota_tomada / cuota_cierre de la MISMA casa; si es captura manual,
  la de la casa de referencia).
* Si no hay cierre, se espera `dias_espera_cierre_csv` al CSV de football-data antes de "sin CLV".
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

from golmodel.modelos import devig
from golmodel.modelos.mercados import CONDICIONES, GRUPOS, GRUPO_DE, PUSH
from golmodel import repositorio as repo

FUENTE_CIERRE = {"odds_api": "snapshot", "api_football": "api_football", "football_data_csv": "football_data_csv"}


def resultado_seleccion(s: str, gl: int, gv: int) -> str:
    i, j = np.array(gl), np.array(gv)
    if s in PUSH and bool(PUSH[s](i, j)):
        return "nulo"
    return "gana" if bool(CONDICIONES[s](i, j)) else "pierde"


def _p_justa_cierre(cierre, seleccion: str, cfg: dict) -> float | None:
    ref = cfg["mercado"]["casa_referencia"].lower()
    r = cierre[cierre["casa"] == ref].set_index("seleccion")["cuota"] if not cierre.empty else {}
    g = GRUPO_DE[seleccion]
    if g == "DC":
        partes = {"DC:1X": ("1X2:1", "1X2:X"), "DC:X2": ("1X2:X", "1X2:2"), "DC:12": ("1X2:1", "1X2:2")}[seleccion]
        sels = GRUPOS["1X2"]
        if not set(sels) <= set(getattr(r, "index", [])):
            return None
        p = dict(zip(sels, devig.quitar_margen([r[s] for s in sels], cfg["mercado"]["metodo_devig"])))
        return p[partes[0]] + p[partes[1]]
    sels = GRUPOS[g]
    if not set(sels) <= set(getattr(r, "index", [])):
        return None
    p = dict(zip(sels, devig.quitar_margen([r[s] for s in sels], cfg["mercado"]["metodo_devig"])))
    return p[seleccion]


def liquidar(conn, cfg: dict, ahora: datetime) -> dict:
    espera = timedelta(days=cfg["liquidacion"]["dias_espera_cierre_csv"])
    ref = cfg["mercado"]["casa_referencia"].lower()
    res = {"picks": 0, "sin_clv": 0, "esperando_cierre": 0, "parlays": 0, "bloqueados_por_conflicto": 0}
    pend = conn.execute("""
        SELECT p.*, pa.estado, pa.goles_local, pa.goles_visitante, pa.resultado_confirmado, pa.inicio, c.clave AS casa,
               EXISTS (SELECT 1 FROM conflictos_datos k WHERE k.partido_id = pa.id AND NOT k.resuelto) AS conflicto
        FROM picks p JOIN partidos pa ON pa.id = p.partido_id JOIN casas_apuestas c ON c.id = p.casa_id
        WHERE p.origen IN ('paper','real')
          AND NOT EXISTS (SELECT 1 FROM pick_liquidaciones l WHERE l.pick_id = p.id)
          AND (pa.estado IN ('cancelado','abandonado') OR (pa.estado = 'finalizado'))
    """).fetchall()
    for pk in pend:
        stake = float(pk["stake_u"])
        if pk["estado"] in ("cancelado", "abandonado"):
            conn.execute("""INSERT INTO pick_liquidaciones (pick_id, resultado, ganancia_u, sin_clv)
                            VALUES (%s, 'nulo', 0, true)""", (pk["id"],))
            res["picks"] += 1
            continue
        if not pk["resultado_confirmado"] or pk["conflicto"]:
            res["bloqueados_por_conflicto"] += 1
            continue
        r = resultado_seleccion(pk["seleccion"], pk["goles_local"], pk["goles_visitante"])
        cuota = float(pk["cuota_sugerida"])
        ganancia = stake * (cuota - 1) if r == "gana" else (-stake if r == "pierde" else 0.0)
        cierre = repo.cuotas_cierre(conn, pk["partido_id"])
        casa_cmp = pk["casa"] if pk["casa"] != "manual" else ref
        fila = cierre[(cierre["casa"] == casa_cmp) & (cierre["seleccion"] == pk["seleccion"])] if not cierre.empty else cierre
        p_justa = _p_justa_cierre(cierre, pk["seleccion"], cfg) if pk["seleccion"] not in PUSH else None
        if fila.empty:
            if ahora - pk["inicio"] < espera:
                res["esperando_cierre"] += 1
                continue
            conn.execute("""INSERT INTO pick_liquidaciones (pick_id, resultado, ganancia_u, sin_clv)
                            VALUES (%s, %s, %s, true)""", (pk["id"], r, round(ganancia, 3)))
            res["sin_clv"] += 1
        else:
            f = fila.iloc[0]
            conn.execute("""
                INSERT INTO pick_liquidaciones (pick_id, resultado, ganancia_u, cuota_cierre_id, cuota_cierre,
                    fuente_cierre, p_cierre_justa, clv_log, clv_ev, sin_clv)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)
            """, (pk["id"], r, round(ganancia, 3), int(f["id"]), float(f["cuota"]), FUENTE_CIERRE[f["fuente"]],
                  p_justa, math.log(cuota / float(f["cuota"])),
                  None if p_justa is None else p_justa * cuota - 1))
        res["picks"] += 1

    # Parlays: se liquidan cuando todos sus partidos están resueltos
    for par in conn.execute("""
        SELECT ps.* FROM parlays_sugeridos ps
        WHERE ps.origen IN ('paper','real') AND NOT EXISTS (SELECT 1 FROM parlay_liquidaciones l WHERE l.parlay_id = ps.id)
    """).fetchall():
        bloques = conn.execute("""
            SELECT b.*, pa.estado, pa.goles_local, pa.goles_visitante, pa.resultado_confirmado,
                   EXISTS (SELECT 1 FROM conflictos_datos k WHERE k.partido_id = pa.id AND NOT k.resuelto) AS conflicto
            FROM parlay_bloques b JOIN partidos pa ON pa.id = b.partido_id WHERE b.parlay_id = %s
        """, (par["id"],)).fetchall()
        if any(b["estado"] not in ("finalizado", "cancelado", "abandonado") for b in bloques):
            continue
        if any(b["estado"] == "finalizado" and (not b["resultado_confirmado"] or b["conflicto"]) for b in bloques):
            res["bloqueados_por_conflicto"] += 1
            continue
        stake = float(par["stake_u"])
        cuota_final, perdio, vivos = 1.0, False, 0
        g_simples = 0.0
        stake_pierna = stake / len(bloques)
        for b in bloques:
            if b["estado"] != "finalizado":
                continue                                   # pierna anulada: sale del parlay
            sels = [b["seleccion_a"]] + ([b["seleccion_b"]] if b["seleccion_b"] else [])
            gano = all(resultado_seleccion(s, b["goles_local"], b["goles_visitante"]) == "gana" for s in sels)
            vivos += 1
            cuota_final *= float(b["cuota"])
            perdio |= not gano
            g_simples += stake_pierna * (float(b["cuota"]) - 1) if gano else -stake_pierna
        if vivos == 0:
            r, gan, cf = "nulo", 0.0, None
        elif perdio:
            r, gan, cf = "pierde", -stake, cuota_final
        else:
            r = "gana" if vivos == len(bloques) else "reducido"
            gan, cf = stake * (cuota_final - 1), cuota_final
        conn.execute("""INSERT INTO parlay_liquidaciones (parlay_id, resultado, cuota_final, ganancia_u,
                        ganancia_simples_u) VALUES (%s, %s, %s, %s, %s)""",
                     (par["id"], r, cf, round(gan, 3), round(g_simples, 3)))
        res["parlays"] += 1
    return res
