"""BD → `motor.tickets.Candidata`. Adaptador sin lógica de modelo.

Para cada partido programado del día lee, AS-OF `ahora` (nunca información posterior), la última predicción de
producción, las cuotas de ejecución y los pesos efectivos de los equipos, y construye las candidatas (simples y SGP del
mismo partido) que `tickets.generar_tickets` consume. Qué candidata merece un ticket lo decide `tickets.py`; aquí solo
se traduce.

Decisiones de traducción (todas documentadas, ninguna es una regla de modelo):
* `p_cruda` = probabilidad de la matriz de DECISIÓN de V2 (`matriz_crs` si el modo de ajuste CRS es 'activo' y hubo
  veto; si no, `matriz`): la misma que persistió V2 en `predicciones_seleccion.p_modelo`. NO es la mezcla α (p_adj).
* `p_calibrada`: lo entrega `calibracion_mercado.aplicar_por_segmento`. V2 no persiste calibradores de mercado: sin
  `registro_calibradores` el resultado es identidad con `calibrado=False` y versión sentinel (contrato §2.5).
* λ, ρ: los de la predicción (λ ya incluye el ajuste por bajas). τ_i lo deriva `incertidumbre` de `peso_efectivo`.
* `peso_efectivo`: de `parametros_equipo` de la versión del modelo de ESA predicción.
* `crs_veto` de la candidata = el veto del partido ∩ sus selecciones (misma regla que `modulo2`).
* `info_hasta` = el instante más antiguo entre la predicción y la cuota (regla conservadora: la información de la pierna
  es tan fresca como su ingrediente más viejo).
* Cuota de ejecución: `prediccion.cuotas_ejecucion` (fuente única con V2); para SGP, misma semántica as-of.
* Solo selecciones en `APTAS_PARLAY` (el mismo catálogo que `tickets.py` aplica; las que empujan no son evaluables).

Sin dato suficiente el partido NO produce candidatas y queda en `omisiones` con un motivo explícito.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from golmodel import repositorio as repo
from golmodel.modelos.calibracion_mercado import aplicar_por_segmento, segmento_de
from golmodel.modelos.mercados import APTAS_PARLAY, probabilidad
from golmodel.motor.tickets import Candidata
from golmodel.servicios.prediccion import cuotas_ejecucion, vigente

MOTIVOS = ("ya_inicio", "sin_prediccion", "sin_peso_efectivo", "sin_cuotas_vigentes")


@dataclass(frozen=True)
class Omision:
    partido_id: int
    liga: str | None
    motivo: str                       # uno de MOTIVOS
    detalle: str | None = None


@dataclass(frozen=True)
class ResultadoCandidatas:
    dia: date
    ahora: datetime
    candidatas: tuple[Candidata, ...]
    omisiones: tuple[Omision, ...]


def _matriz_decision(fila: dict) -> np.ndarray:
    """La matriz de la que V2 sacó `p_modelo` (ver docstring del módulo)."""
    usar_crs = fila["crs_modo_ajuste"] == "activo" and fila["matriz_crs"] is not None
    m = np.asarray(fila["matriz_crs"] if usar_crs else fila["matriz"], dtype=float)
    n = int(round(len(m) ** 0.5))
    return m.reshape(n, n)


def _prediccion(conn, partido_id: int, ahora: datetime) -> dict | None:
    return conn.execute("""
        SELECT * FROM predicciones WHERE partido_id = %s AND origen = 'produccion' AND creada_en <= %s
        ORDER BY creada_en DESC, id DESC LIMIT 1""", (partido_id, ahora)).fetchone()


def _cuotas_asof(conn, partido_id: int, ahora: datetime, casas: list[str]) -> pd.DataFrame:
    filas = conn.execute("""
        SELECT DISTINCT ON (q.casa_id, q.seleccion) q.id, q.casa_id, c.clave AS casa, q.seleccion, q.cuota, q.capturada_en
        FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id
        WHERE q.partido_id = %s AND q.tipo IN ('apertura','actual') AND q.capturada_en <= %s AND c.clave = ANY(%s)
        ORDER BY q.casa_id, q.seleccion, q.capturada_en DESC, q.id DESC""", (partido_id, ahora, casas)).fetchall()
    df = pd.DataFrame(filas, columns=["id", "casa_id", "casa", "seleccion", "cuota", "capturada_en"])
    df["cuota"] = df["cuota"].astype(float)
    return df


def _sgp_asof(conn, partido: dict, ahora: datetime, cfg_v2: dict, casas: list[str]) -> dict[tuple[str, str], tuple[float, datetime]]:
    filas = conn.execute("""
        SELECT DISTINCT ON (s.casa_id, s.seleccion_a, s.seleccion_b) c.clave AS casa, s.seleccion_a, s.seleccion_b,
               s.cuota, s.capturada_en
        FROM cuotas_sgp s JOIN casas_apuestas c ON c.id = s.casa_id
        WHERE s.partido_id = %s AND s.capturada_en <= %s AND c.clave = ANY(%s)
        ORDER BY s.casa_id, s.seleccion_a, s.seleccion_b, s.capturada_en DESC, s.id DESC""",
                         (partido["id"], ahora, casas)).fetchall()
    out: dict[tuple[str, str], tuple[float, datetime]] = {}
    for casa in casas:                                   # la casa manual (última) reemplaza a la fija, como en V2
        for r in (f for f in filas if f["casa"] == casa):
            if vigente(r["capturada_en"], partido["inicio"], ahora, cfg_v2):
                out[(r["seleccion_a"], r["seleccion_b"])] = (float(r["cuota"]), r["capturada_en"])
    return out


def _pesos(conn, version_id: int, equipos: list[int]) -> dict[int, float]:
    return {r["equipo_id"]: float(r["peso_efectivo"]) for r in conn.execute(
        "SELECT equipo_id, peso_efectivo FROM parametros_equipo WHERE version_id = %s AND equipo_id = ANY(%s)",
        (version_id, equipos))}


def construir_candidatas(conn, dia: date, ahora: datetime, cfg_v2: dict, cfg_v3: dict,
                         registro_calibradores: dict | None = None) -> ResultadoCandidatas:
    """Candidatas del `dia` con la información disponible en `ahora`. Determinista: mismo estado de BD y mismos
    argumentos ⇒ mismo resultado (orden incluido)."""
    registro = registro_calibradores or {}
    casas = [cfg_v2["mercado"]["casa_ejecucion"], "manual"]
    omisiones: list[Omision] = []
    crudas: list[dict] = []                              # una por candidata, antes de calibrar

    for partido in repo.partidos_de_dias(conn, [dia]):
        pid, liga = partido["id"], partido["liga"]
        if partido["inicio"] <= ahora:
            omisiones.append(Omision(pid, liga, "ya_inicio", partido["inicio"].isoformat()))
            continue
        pred = _prediccion(conn, pid, ahora)
        if pred is None:
            omisiones.append(Omision(pid, liga, "sin_prediccion"))
            continue
        pesos = _pesos(conn, pred["version_id"], [partido["local_id"], partido["visitante_id"]])
        pl, pv = pesos.get(partido["local_id"]), pesos.get(partido["visitante_id"])
        if pl is None or pv is None or not (pl > 0 and pv > 0):
            omisiones.append(Omision(pid, liga, "sin_peso_efectivo",
                                     f"local={pl} visitante={pv} version_id={pred['version_id']}"))
            continue

        ejec = cuotas_ejecucion(_cuotas_asof(conn, pid, ahora, casas), partido, ahora, cfg_v2)
        piernas: dict[tuple[str, ...], tuple[float, datetime]] = {
            (s,): (q["cuota"], q["capturada_en"]) for s, q in ejec.items() if q["vigente"] and s in APTAS_PARLAY}
        for (a, b), v in _sgp_asof(conn, partido, ahora, cfg_v2, casas).items():
            if a in APTAS_PARLAY and b in APTAS_PARLAY:
                piernas[(a, b)] = v
        if not piernas:
            omisiones.append(Omision(pid, liga, "sin_cuotas_vigentes"))
            continue

        m = _matriz_decision(pred)
        vetadas = set(pred["crs_selecciones_vetadas"] or []) if pred["crs_veto"] else set()
        for sels in sorted(piernas):
            cuota, capt = piernas[sels]
            crudas.append({
                "partido_id": pid, "liga": liga, "selecciones": sels, "cuota": cuota,
                "p_cruda": probabilidad(m, *sels),
                "segmento": segmento_de(liga, sels, cuota, cfg_v3),
                "lambda_local": float(pred["lambda_local"]), "lambda_visitante": float(pred["lambda_visitante"]),
                "rho": float(pred["rho"]), "peso_local": pl, "peso_visitante": pv,
                "crs_total": None if pred["crs_total"] is None else int(pred["crs_total"]),
                "crs_veto": bool(vetadas & set(sels)),
                "info_hasta": min(pred["info_hasta"], capt)})

    cal = aplicar_por_segmento(np.array([c["p_cruda"] for c in crudas]), [c["segmento"] for c in crudas], registro) \
        if crudas else []
    candidatas = tuple(
        Candidata(partido_id=c["partido_id"], liga=c["liga"], selecciones=c["selecciones"],
                  lambda_local=c["lambda_local"], lambda_visitante=c["lambda_visitante"], rho=c["rho"],
                  peso_efectivo_local=c["peso_local"], peso_efectivo_visitante=c["peso_visitante"], cuota=c["cuota"],
                  calibrada=k, crs_total=c["crs_total"], crs_veto=c["crs_veto"], info_hasta=c["info_hasta"])
        for c, k in zip(crudas, cal))
    return ResultadoCandidatas(dia, ahora, candidatas, tuple(omisiones))
