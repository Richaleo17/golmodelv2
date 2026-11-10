"""V2 → V3 en la ruta en vivo: sustituye la probabilidad de parlay por producto (`math.prod`, independencia) por la
probabilidad conjunta REAL de `motor/dependencia.py`, vía `motor/tickets.py` y el auditor `cerebro2/`.

Este módulo ORQUESTA, no calcula: lee candidatas (`integracion.candidatas`), llama a `tickets.generar_tickets` (que usa
`dependencia.py`) y a `cerebro2.riesgo.evaluar`, y devuelve la decisión. NO hay ninguna ruta de respaldo a independencia:
si `dependencia.py` o `tickets.py` fallan, el resultado es ABSTENCIÓN con el error registrado (nunca un ticket calculado
con el producto de probabilidades).

`modo_experimental` sale de la config (hoy true: q, σ_g y c_seg sin validar con OOS real) y nunca se apaga aquí: el
candado `calibrado_con == 'oos_real'` vive en `tickets.py`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from golmodel.backtest.calibracion_tickets import VersionCalibradorTickets
from golmodel.cerebro2 import persistencia, riesgo
from golmodel.cerebro2.benchmark_mercado import MercadoPierna
from golmodel.cerebro2.decision import Veredicto
from golmodel.config import hash_config
from golmodel.integracion import almacen_calibradores as almacen
from golmodel.integracion.candidatas import ResultadoCandidatas, construir_candidatas
from golmodel.modelos.mercados import GRUPO_DE, GRUPOS
from golmodel.motor import tickets as tk
from golmodel.servicios.prediccion import vigente

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecisionV3:
    dia: date
    ahora: datetime
    candidatas: ResultadoCandidatas
    salida: tk.SalidaTickets | None      # None solo si tickets/dependencia fallaron (ver `error`)
    veredicto: Veredicto | None          # None si hubo ABSTENCIÓN (no hay ticket que auditar)
    error: str | None                    # "Tipo: mensaje" si algo falló: la decisión es ABSTENCIÓN, nunca independencia

    @property
    def estado(self) -> str:
        """Estado FINAL para el usuario: el del auditor si hay ticket; si no, ABSTENCION."""
        if self.veredicto is not None:
            return self.veredicto.estado
        return "ABSTENCION"

    @property
    def ticket(self):
        return None if self.salida is None else self.salida.ticket


def version_provisional(cfg_v3: dict, ahora: datetime) -> VersionCalibradorTickets:
    """Versión SIN calibrar (config `calibracion_tickets.version_provisional`). Nunca 'sinteticos' ni 'oos_real'."""
    ct, vp = cfg_v3["calibracion_tickets"], cfg_v3["calibracion_tickets"]["version_provisional"]
    return VersionCalibradorTickets(
        datos_hasta=ahora, config_hash=hash_config(ct), n_draws=int(cfg_v3["dependencia"]["n_draws"]), semilla=0,
        metodo="provisional_sin_calibrar", segmentos_calibrados=[], segmentos_fallback=[],
        c_global=float(vp["c_global"]), sigma_liga=vp.get("sigma_liga", 0.0), sigma_g=float(vp.get("sigma_g", 0.0)),
        calibrado_con="no_calibrado")


def resolver_version(conn, ahora: datetime, cfg_v3: dict) -> VersionCalibradorTickets:
    """La última versión VÁLIDA del backtest OOS (oos_real + gates_ok, creada <= ahora) o, si no hay, la provisional (no_calibrado)."""
    guardada = almacen.cargar_ultima_valida(conn, ahora)
    return guardada[1] if guardada else version_provisional(cfg_v3, ahora)


def mercado_de_candidatas(conn, candidatas, ahora: datetime, cfg_v2: dict, cfg_v3: dict) -> dict:
    """{(partido_id, selecciones): MercadoPierna} con cuotas CRUDAS de las casas elegibles, as-of `ahora`, vigentes.
    Solo piernas simples de grupos exhaustivos: las demás quedan fuera y Cerebro 2 las marca `no_auditable` (no se inventa)."""
    casas = list(cfg_v3["cerebro2"]["consenso_mercado"]["casas_elegibles"])
    ids = sorted({c.partido_id for c in candidatas})
    if not ids:
        return {}
    filas = conn.execute("""
        SELECT DISTINCT ON (q.partido_id, q.casa_id, q.seleccion) q.partido_id, c.clave AS casa, q.seleccion, q.cuota,
               q.capturada_en, p.inicio
        FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id JOIN partidos p ON p.id = q.partido_id
        WHERE q.partido_id = ANY(%s) AND q.tipo IN ('apertura','actual') AND q.capturada_en <= %s AND c.clave = ANY(%s)
        ORDER BY q.partido_id, q.casa_id, q.seleccion, q.capturada_en DESC, q.id DESC""", (ids, ahora, casas)).fetchall()
    por_partido: dict[int, dict[str, dict[str, float]]] = {}
    for f in filas:
        if vigente(f["capturada_en"], f["inicio"], ahora, cfg_v2):
            por_partido.setdefault(f["partido_id"], {}).setdefault(f["casa"], {})[f["seleccion"]] = float(f["cuota"])
    out = {}
    for c in candidatas:
        if len(c.selecciones) != 1 or GRUPO_DE.get(c.selecciones[0]) not in GRUPOS:
            continue
        grupo = set(GRUPOS[GRUPO_DE[c.selecciones[0]]])
        cuotas = {casa: {s: q for s, q in d.items() if s in grupo} for casa, d in por_partido.get(c.partido_id, {}).items()}
        out[(c.partido_id, tuple(c.selecciones))] = MercadoPierna(cuotas_por_casa=cuotas, casas_esperadas=tuple(casas))
    return out


def decidir(candidatas, mercado: dict, version_cal: VersionCalibradorTickets, cfg_v3: dict, *, semilla: int,
            ahora: datetime, cache=None) -> tuple[tk.SalidaTickets | None, Veredicto | None, str | None]:
    """Núcleo puro (sin BD): tickets.py (con dependencia.py) → Cerebro 2. Cualquier fallo ⇒ (None, None, error): ABSTENCIÓN."""
    try:
        salida = tk.generar_tickets(list(candidatas), version_cal, cfg_v3,
                                    modo_experimental=bool(cfg_v3["modo_experimental"]), semilla=semilla,
                                    ahora=ahora, cache=cache)
    except Exception as e:                       # noqa: BLE001 — fail-closed: NO se cae a independencia
        log.error("tickets/dependencia fallaron; ABSTENCIÓN (sin respaldo a independencia): %s: %s", type(e).__name__, e)
        return None, None, f"{type(e).__name__}: {e}"
    if salida.ticket is None:
        return salida, None, None
    return salida, riesgo.evaluar(salida, mercado, cfg_v3), None


def generar_decision_v3(conn, dia: date, ahora: datetime, cfg_v2: dict, cfg_v3: dict, *, version_cal=None,
                        semilla: int | None = None, registro_calibradores: dict | None = None, cache=None) -> DecisionV3:
    """Lee la BD as-of `ahora`, genera el ticket del día con probabilidad conjunta real y lo audita (Cerebro 2).
    Determinista: mismo estado de BD + mismos argumentos ⇒ misma decisión."""
    cands = construir_candidatas(conn, dia, ahora, cfg_v2, cfg_v3, registro_calibradores)
    version_cal = version_cal or resolver_version(conn, ahora, cfg_v3)
    semilla = int(dia.strftime("%Y%m%d")) if semilla is None else int(semilla)
    mercado = mercado_de_candidatas(conn, cands.candidatas, ahora, cfg_v2, cfg_v3)
    salida, veredicto, error = decidir(cands.candidatas, mercado, version_cal, cfg_v3, semilla=semilla, ahora=ahora, cache=cache)
    return DecisionV3(dia, ahora, cands, salida, veredicto, error)


def persistir(conn, d: DecisionV3) -> dict:
    """Guarda en auditoria_decisiones / evaluaciones_riesgo (migración 004). Falla en voz alta si no está aplicada."""
    ids = {}
    if d.salida is not None:
        ids["tickets"] = persistencia.guardar_decision_tickets(conn, d.salida, payload_extra={"dia": d.dia.isoformat()})
        if d.veredicto is not None:
            ids["cerebro2"] = persistencia.guardar_veredicto(conn, d.veredicto)
    return ids
