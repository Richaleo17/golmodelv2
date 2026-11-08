"""Fase 3 · Ejemplo de punta a punta para un partido.

    # Con un CSV real de football-data (recomendado):
    python scripts/fase3_ejemplo.py --csv E0.csv --liga EPL --local Arsenal --visitante Chelsea

    # Sin CSV: usa una liga SINTÉTICA (parámetros conocidos) y lo indica en el JSON.
    python scripts/fase3_ejemplo.py

Pasos: limpieza del CSV -> ajuste Dixon-Coles con decaimiento -> matriz ->
mercados -> quitar margen (Shin) -> p_adj -> EV -> SGP (local + Over 2.5).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from golmodel.config import cargar  # noqa: E402
from golmodel.ingesta.limpieza import limpiar  # noqa: E402
from golmodel.modelos import devig, sgp  # noqa: E402
from golmodel.modelos.combinacion import matriz_mercado, p_ajustada  # noqa: E402
from golmodel.modelos.dixon_coles import ajustar  # noqa: E402
from golmodel.modelos.mercados import GRUPOS, probabilidad, todos  # noqa: E402
from golmodel.motor.valor import clasificar, cuota_justa, ev, umbral_efectivo  # noqa: E402

MARGEN_EJEMPLO = 0.05


def cuotas_mercado_eficiente(verdad, local: str, visitante: str) -> dict[str, float]:
    """Cuotas de EJEMPLO: probabilidades VERDADERAS de la liga sintética + 5% de margen.

    Simula un mercado eficiente: lo normal es que el modelo NO encuentre valor
    contra él. Un EV grande contra un mercado eficiente indica error del modelo.
    """
    from golmodel.modelos.matriz import matriz_marcadores
    lh = np.exp(verdad.mu + verdad.ventaja_local + verdad.ataque[local] - verdad.defensa[visitante])
    la = np.exp(verdad.mu + verdad.ataque[visitante] - verdad.defensa[local])
    m = matriz_marcadores(lh, la, verdad.rho)
    cuotas = {}
    for grupo in ("1X2", "OU:2.5", "BTTS"):
        for s in GRUPOS[grupo]:
            cuotas[s] = round(1.0 / (probabilidad(m, s) * (1 + MARGEN_EJEMPLO)), 2)
    return cuotas


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def construir(args) -> dict:
    cfg = cargar()
    mc = cfg["modelo"]
    xi = args.xi if args.xi is not None else mc["decaimiento_xi_inicial"]
    alpha = cfg["mercado"]["alpha_por_defecto"]

    if args.csv:
        partidos, cuotas, rep = limpiar(Path(args.csv).read_text(encoding="utf-8", errors="replace"), args.liga)
        origen = {"tipo": "csv_football_data", "archivo": args.csv, "limpieza": rep.__dict__}
        w_xg = 0.0   # football-data no trae xG
        corte = (pd.Timestamp(args.fecha, tz="UTC") if args.fecha
                 else partidos.loc[partidos.estado == "finalizado", "inicio"].max() + timedelta(seconds=1))
        local, visitante = args.local, args.visitante
        c = cuotas[(cuotas.local == local) & (cuotas.visitante == visitante) & (cuotas.tipo == "prepartido")
                   & (cuotas.casa == "pinnacle")] if not cuotas.empty else cuotas
        cuotas_partido = dict(zip(c.seleccion, c.cuota)) if not c.empty else {}
    else:
        from golmodel.backtest.sintetico import generar_liga
        partidos, verdad = generar_liga(temporadas=3, semilla=2026)
        origen = {"tipo": "SINTETICO", "advertencia": "Liga simulada con parámetros conocidos. Cuotas = "
                  "probabilidades verdaderas + 5% de margen (mercado eficiente). No usar para apostar."}
        w_xg = mc["w_xg_inicial"]
        corte = partidos["inicio"].max() + timedelta(days=1)
        local, visitante = "SINT_03", "SINT_11"
        cuotas_partido = cuotas_mercado_eficiente(verdad, local, visitante)

    modelo = ajustar(partidos, fecha_corte=corte.to_pydatetime(), xi=xi, w_xg=w_xg,
                     l2_equipos=mc["l2_lambda_inicial"], l2_ligas=mc["l2_lambda_inicial"],
                     rho_limites=tuple(mc["rho_limites"]),
                     rho_prior=(mc["rho_prior"]["media"], mc["rho_prior"]["desviacion"]))
    m = modelo.matriz(local, visitante, gmax=mc["goles_max"])
    p_modelo = todos(m)

    # Probabilidad del mercado sin margen, por grupo completo de cuotas.
    p_mercado: dict[str, float] = {}
    for grupo, sels in GRUPOS.items():
        if all(s in cuotas_partido for s in sels):
            p = devig.quitar_margen([cuotas_partido[s] for s in sels], cfg["mercado"]["metodo_devig"])
            p_mercado.update(dict(zip(sels, p)))

    umbral = umbral_efectivo(cfg, alpha_calibrado=False)   # α aún no calibrado en backtest
    mercados = {}
    for s, pm in p_modelo.items():
        pmk = p_mercado.get(s)
        padj = p_ajustada(pm, pmk, alpha)
        cuota = cuotas_partido.get(s)
        e = ev(padj, cuota) if (padj is not None and cuota) else None
        mercados[s] = {"p_modelo": _r(pm), "p_mercado": _r(pmk), "p_adj": _r(padj),
                       "cuota_justa": _r(cuota_justa(padj if padj is not None else pm), 3),
                       "cuota_casa": cuota, "ev": _r(e),
                       "clasificacion": clasificar(e, umbral) if e is not None else "sin_cuota"}

    # SGP de ejemplo: gana local + Over 2.5, sumando celdas de la matriz.
    ev_sgp = sgp.evaluar(m, ("1X2:1", "OU:2.5:over"), ratio_redundancia=cfg["parlays"]["redundancia_ratio"])
    mm = matriz_mercado(p_mercado, modelo.rho) if p_mercado else None
    p_sgp_mercado = probabilidad(mm.matriz, *ev_sgp.selecciones) if (mm and mm.usable) else None
    p_sgp_adj = p_ajustada(ev_sgp.p_conjunta, p_sgp_mercado, alpha)
    i, j = np.indices(m.p.shape)
    suma_manual = float(m.p[(i > j) & (i + j >= 3)].sum())

    top = np.dstack(np.unravel_index(np.argsort(m.p.ravel())[::-1][:6], m.p.shape))[0]
    return {
        "origen_datos": origen,
        "partido": {"local": local, "visitante": visitante},
        "modelo": {"version": "dixon_coles_xg_l2", "datos_hasta": modelo.datos_hasta.isoformat(),
                   "n_partidos": modelo.n_partidos, "xi_por_dia": xi,
                   "vida_media_dias": _r(modelo.vida_media_dias, 1), "w_xg": w_xg,
                   "rho": _r(modelo.rho), "ventaja_local": _r(float(modelo.ventaja_local[0])),
                   "convergencia": modelo.convergencia},
        "lambdas": {"local": _r(m.lambda_local), "visitante": _r(m.lambda_visitante)},
        "matriz": {"goles_max": mc["goles_max"], "suma": _r(m.p.sum(), 12),
                   "masa_truncada": float(m.masa_truncada), "rho_acotado": m.rho_acotado,
                   "marcadores_mas_probables": [{"marcador": f"{a}-{b}", "p": _r(m.p[a, b])} for a, b in top]},
        "combinacion": {"alpha": alpha, "alpha_calibrado": False,
                        "umbral_edge_efectivo": umbral.valor, "razones_umbral": list(umbral.razones)},
        "mercados": mercados,
        "consistencia": {
            "1X2_suma": _r(sum(p_modelo[s] for s in GRUPOS["1X2"]), 12),
            "BTTS_suma": _r(sum(p_modelo[s] for s in GRUPOS["BTTS"]), 12),
            "OU2.5_suma": _r(sum(p_modelo[s] for s in GRUPOS["OU:2.5"]), 12),
        },
        "sgp_ejemplo": {
            "selecciones": list(ev_sgp.selecciones), "descripcion": "Gana local + Más de 2.5 goles",
            "p_modelo_conjunta": _r(ev_sgp.p_conjunta), "verificacion_suma_celdas": _r(suma_manual),
            "p_si_se_multiplicara (INCORRECTO)": _r(ev_sgp.p_si_independientes),
            "correlacion_lift": _r(ev_sgp.correlacion, 3),
            "p_mercado_conjunta": _r(p_sgp_mercado),
            "matriz_mercado_error_max": _r(mm.error_max, 5) if mm else None,
            "p_adj": _r(p_sgp_adj),
            "cuota_justa": _r(cuota_justa(p_sgp_adj if p_sgp_adj else ev_sgp.p_conjunta), 3),
            "cuota_casa": None, "ev": None,
            "estado": "valido, requiere capturar la cuota SGP real de la casa" if ev_sgp.valido else ev_sgp.motivo,
            "umbral_edge_sgp": umbral_efectivo(cfg, sgp=True, sgp_con_mercado=p_sgp_mercado is not None,
                                               alpha_calibrado=False).valor,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--liga", default="EPL")
    ap.add_argument("--local")
    ap.add_argument("--visitante")
    ap.add_argument("--fecha", help="corte (UTC, YYYY-MM-DD); por defecto, tras el último resultado")
    ap.add_argument("--xi", type=float)
    ap.add_argument("--salida", default=str(RAIZ / "docs" / "ejemplo_partido.json"))
    args = ap.parse_args()
    if args.csv and not (args.local and args.visitante):
        ap.error("--csv requiere --local y --visitante")
    out = construir(args)
    Path(args.salida).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
