"""BD histórica → `CestaHistorica` para `backtest/calibracion_tickets.py`. Adaptador sin lógica de modelo.

Implementa la decisión 5 de `docs/v3_incertidumbre_diseno.md` (pre-registrada): las cestas de calibración se construyen
**ex-post, SIN filtrar por aprobación** (ni por CRS, EV o p mínimo). Para cada día se toman todas las piernas disponibles
as-of y se MUESTREAN combinaciones de N piernas (máx. 1 por partido, partidos distintos), de modo que entren las que el
motor habría rechazado y las perdedoras. Calibrar sobre cestas aprobadas sería sesgo de supervivencia.

Garantías (cada una con test y mutante):
* **Sin leakage.** El modelo de cada ventana se ajusta SOLO con partidos `inicio < t` (`modelos.dixon_coles.ajustar`,
  con aserción `datos_hasta <= t`); las cuotas usadas son las capturadas `<= t_dia` (inicio del día local de decisión,
  `t_dia >= t`); los partidos de la ventana empiezan `>= t`. Los RESULTADOS solo se usan para `gano`, nunca para λ/ρ/pesos.
* **Reproducible.** La muestra de cada día sale de `default_rng([semilla, día])`: no depende del resto de días.
* **Cobertura.** El muestreo es uniforme sobre partidos y sobre selecciones disponibles ⇒ cubre todo el rango de
  probabilidades, cuotas y mezclas de mercado.
* **Anuladas/pendientes NO se descartan en silencio:** `CestaHistorica` solo representa `gano: bool`, así que una cesta con
  pierna anulada o sin resolver queda en `excluidas` (con su meta) y se cuenta. Desviación documentada de "incluidas anuladas".

Decisiones de traducción (no son reglas de modelo): el modelo histórico usa los hiperparámetros iniciales de `config.yaml`
(`decaimiento_xi_inicial`, `w_xg_inicial`, `l2_lambda_inicial`, sin prior de ascendidos, sin recalibración semanal);
la cuota de la pierna es la de la casa `casa` as-of `t_dia`; solo piernas simples (no hay cuotas SGP históricas); NO se
aplica CRS (`crs_veto`) ni frescura: el histórico no los reproduce y la decisión 5 pide no filtrar por ellos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from golmodel import repositorio as repo
from golmodel.backtest.calibracion_tickets import CestaHistorica, PiernaHistorica
from golmodel.cerebro2.metacalibracion import resolver_ticket
from golmodel.modelos.calibracion_mercado import segmento_de
from golmodel.modelos.dixon_coles import ModeloDC, ajustar
from golmodel.modelos.mercados import APTAS_PARLAY, probabilidad
from golmodel.servicios.modelo import _rho_prior

TIPOS_CUOTA = ("apertura", "prepartido", "actual")
RESULTADOS = ("gano", "perdio", "anulada", "pendiente")


@dataclass(frozen=True)
class PiernaMeta:
    partido_id: int
    selecciones: tuple[str, ...]
    cuota: float
    cuota_capturada_en: datetime
    p_modelo: float                    # solo para auditoría/tests; NO entra en el ajuste


@dataclass(frozen=True)
class MetaCesta:
    ventana_t: datetime                # corte del modelo (todo dato del modelo es < t)
    t_dia: datetime                    # instante de decisión (toda cuota usada fue capturada <= t_dia)
    dia: str
    n: int
    piernas: tuple[PiernaMeta, ...]
    resultado: str                     # uno de RESULTADOS


@dataclass(frozen=True)
class ResultadoCestas:
    cestas: tuple[CestaHistorica, ...]       # solo las resueltas (gano True/False); paralelas a `meta`
    meta: tuple[MetaCesta, ...]
    excluidas: tuple[MetaCesta, ...]         # anuladas o pendientes: contadas, nunca borradas en silencio
    omisiones: dict                          # motivo -> n (partidos o días no usados)


# --------------------------------------------------------------------- lectura (SQL; sin lógica)

def leer_historico(conn, desde: datetime, hasta: datetime, casa: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(df_modelo, pool, cuotas). `df_modelo` = historial completo en el formato del modelo (el ajuste filtra por corte);
    `pool` = TODOS los partidos de [desde, hasta) con cualquier estado (incluye anulados); `cuotas` = de `casa`."""
    df_modelo = repo.historial(conn)
    filas = conn.execute("""
        SELECT p.id AS partido_id, p.inicio, l.codigo AS liga, p.local_id::text AS local, p.visitante_id::text AS visitante,
               p.estado, p.goles_local, p.goles_visitante
        FROM partidos p JOIN ligas l ON l.id = p.liga_id WHERE p.inicio >= %s AND p.inicio < %s ORDER BY p.inicio, p.id""",
                         (desde, hasta)).fetchall()
    pool = pd.DataFrame(filas, columns=["partido_id", "inicio", "liga", "local", "visitante", "estado",
                                        "goles_local", "goles_visitante"])
    if not pool.empty:
        pool["inicio"] = pd.to_datetime(pool["inicio"], utc=True)
    ids = [int(x) for x in pool["partido_id"]] if not pool.empty else []
    q = conn.execute("""
        SELECT q.partido_id, q.seleccion, q.cuota, q.capturada_en, q.id FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id
        WHERE c.clave = %s AND q.tipo = ANY(%s) AND q.partido_id = ANY(%s)""", (casa, list(TIPOS_CUOTA), ids)).fetchall()
    cuotas = pd.DataFrame(q, columns=["partido_id", "seleccion", "cuota", "capturada_en", "id"])
    if not cuotas.empty:
        cuotas["cuota"] = cuotas["cuota"].astype(float)
        cuotas["capturada_en"] = pd.to_datetime(cuotas["capturada_en"], utc=True)
    return df_modelo, pool, cuotas


# --------------------------------------------------------------------- construcción (pura)

def _ajustar(df_modelo: pd.DataFrame, t: datetime, cfg_v2: dict, previo: ModeloDC | None) -> ModeloDC:
    m = cfg_v2["modelo"]
    modelo = ajustar(df_modelo, fecha_corte=t, xi=m["decaimiento_xi_inicial"], w_xg=m["w_xg_inicial"],
                     l2_equipos=m["l2_lambda_inicial"], l2_ligas=m["l2_lambda_inicial"],
                     rho_limites=tuple(m["rho_limites"]), rho_prior=_rho_prior(cfg_v2), inicial=previo)
    if not modelo.datos_hasta <= t:                                    # garantía dura anti-leakage (no es un assert que -O borre)
        raise AssertionError(f"leakage: el modelo usó datos hasta {modelo.datos_hasta} > t={t}")
    return modelo


def _inicio_dia(inicio: pd.Timestamp, tz: ZoneInfo) -> pd.Timestamp:
    return inicio.tz_convert(tz).normalize().tz_convert("UTC")


def _tamanos(cfg_v3: dict) -> list[int]:
    t = cfg_v3["tickets"]
    return list(range(int(t["tamano_reducido_min"]), int(t["tamano_alta_max"]) + 1))


def construir_cestas(df_modelo: pd.DataFrame, pool: pd.DataFrame, cuotas: pd.DataFrame, cfg_v2: dict, cfg_v3: dict, *,
                     desde: datetime, hasta: datetime, semilla: int) -> ResultadoCestas:
    if int(semilla) < 0:
        raise ValueError("semilla debe ser >= 0")
    ch = cfg_v3["calibracion_tickets"]["cestas_historicas"]
    paso, n_dia = int(ch["paso_dias"]), int(ch["n_cestas_por_dia"])
    tamanos = _tamanos(cfg_v3)
    tz = ZoneInfo(cfg_v2["zona_horaria"])
    gmax = cfg_v2["modelo"]["goles_max"]
    resultados = {int(r.partido_id): (r.estado, None if pd.isna(r.goles_local) else int(r.goles_local),
                                      None if pd.isna(r.goles_visitante) else int(r.goles_visitante))
                  for r in pool.itertuples()}
    omisiones: dict[str, int] = {}

    def _om(k: str, n: int = 1):
        omisiones[k] = omisiones.get(k, 0) + n

    cestas: list[CestaHistorica] = []
    metas: list[MetaCesta] = []
    excluidas: list[MetaCesta] = []
    previo: ModeloDC | None = None
    t = pd.Timestamp(desde)
    fin = pd.Timestamp(hasta)
    while t < fin:
        t_sig = min(t + timedelta(days=paso), fin)
        ventana = pool[(pool["inicio"] >= t) & (pool["inicio"] < t_sig)] if not pool.empty else pool
        if ventana.empty:
            t = t_sig
            continue
        previos = df_modelo[(df_modelo["inicio"] < t) & df_modelo["goles_local"].notna() & df_modelo["goles_visitante"].notna()]
        if previos.empty:                                               # sin partidos previos al corte: no se inventa historia
            _om("ventana_sin_historia", len(ventana))
            t = t_sig
            continue
        modelo = _ajustar(df_modelo, t.to_pydatetime(), cfg_v2, previo)
        previo = modelo

        for dia, grupo in ventana.groupby(ventana["inicio"].map(lambda x: _inicio_dia(x, tz))):
            t_dia = max(t, dia)
            info: dict[int, list[tuple]] = {}          # partido_id -> [(PiernaHistorica, PiernaMeta), ...] por selección
            for r in grupo.itertuples():
                pid = int(r.partido_id)
                if r.local not in modelo.peso_efectivo or r.visitante not in modelo.peso_efectivo:
                    _om("equipo_sin_historial")
                    continue
                q = cuotas[(cuotas["partido_id"] == pid) & (cuotas["capturada_en"] <= t_dia)
                           & cuotas["seleccion"].isin(APTAS_PARLAY)] if not cuotas.empty else cuotas
                if q.empty:
                    _om("sin_cuotas_asof")
                    continue
                q = q.sort_values(["seleccion", "capturada_en", "id"]).groupby("seleccion").tail(1)
                m = modelo.matriz(r.local, r.visitante, gmax=gmax)
                legs = []
                for s in q.itertuples():
                    sels = (s.seleccion,)
                    legs.append((PiernaHistorica(
                        liga=r.liga, segmento=segmento_de(r.liga, sels, float(s.cuota), cfg_v3),
                        lambda_local=float(m.lambda_local), lambda_visitante=float(m.lambda_visitante), rho=float(m.rho),
                        selecciones=sels, peso_efectivo_local=float(modelo.peso_efectivo[r.local]),
                        peso_efectivo_visitante=float(modelo.peso_efectivo[r.visitante]), partido_id=pid),
                        PiernaMeta(pid, sels, float(s.cuota), s.capturada_en.to_pydatetime(),
                                   float(probabilidad(m, *sels)))))
                info[pid] = legs
            pids = sorted(info)
            if len(pids) < min(tamanos):
                _om("dia_con_partidos_insuficientes")
                continue
            rng = np.random.default_rng([int(semilla), int(dia.value // 86_400_000_000_000)])
            vistos: set = set()
            intentos = 0
            while len(vistos) < n_dia and intentos < n_dia * 20:
                intentos += 1
                n = int(rng.choice([k for k in tamanos if k <= len(pids)]))
                elegidos = sorted(rng.choice(len(pids), size=n, replace=False).tolist())
                piernas = tuple(info[pids[i]][int(rng.integers(len(info[pids[i]])))] for i in elegidos)
                clave = tuple((pm.partido_id, pm.selecciones) for _, pm in piernas)
                if clave in vistos:
                    continue
                vistos.add(clave)
                resumen = {"piernas": [{"partido_id": pm.partido_id, "selecciones": list(pm.selecciones)} for _, pm in piernas]}
                gano = resolver_ticket(resumen, resultados)
                estado = "pendiente" if gano is None and any(
                    resultados[pm.partido_id][0] in ("programado", "en_juego") for _, pm in piernas) \
                    else ("anulada" if gano is None else ("gano" if gano else "perdio"))
                meta = MetaCesta(t.to_pydatetime(), t_dia.to_pydatetime(), str(dia.date()), n,
                                 tuple(pm for _, pm in piernas), estado)
                if gano is None:
                    excluidas.append(meta)
                else:
                    cestas.append(CestaHistorica([ph for ph, _ in piernas], bool(gano)))
                    metas.append(meta)
        t = t_sig
    return ResultadoCestas(tuple(cestas), tuple(metas), tuple(excluidas), omisiones)


def construir_cestas_historicas(conn, desde: datetime, hasta: datetime, cfg_v2: dict, cfg_v3: dict, *, casa: str,
                                semilla: int) -> ResultadoCestas:
    """Lee la BD y construye las cestas ex-post walk-forward de [desde, hasta)."""
    df_modelo, pool, cuotas = leer_historico(conn, desde, hasta, casa)
    return construir_cestas(df_modelo, pool, cuotas, cfg_v2, cfg_v3, desde=desde, hasta=hasta, semilla=semilla)
