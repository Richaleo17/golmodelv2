"""Metacalibración del auditor: ¿Cerebro 2 discrimina bien, o solo acierta por casualidad en el agregado?

JOB EX-POST sobre el histórico (NO corre en runtime): necesita el resultado real de los partidos. Definiciones y
exclusiones: pre-registro §2.9.

  tasa_rechazos_correctos   = rechazados que HABRÍAN PERDIDO / rechazados resueltos
  tasa_rechazos_incorrectos = rechazados que HABRÍAN GANADO  / rechazados resueltos   (coste de oportunidad)
  tasa_aprobados_correctos  = aceptados que GANARON / aceptados resueltos
  yield_rechazados          = media del retorno a stake plano (cuota_combinada-1 si gana, -1 si pierde)

* "Rechazados" = RECHAZADO por un gate SUSTANTIVO. Se excluyen (y se cuentan aparte) las ABSTENCIONES y toda decisión con
  categoría `rechazo_por_falta_de_auditoria`: miden ausencia de datos, no capacidad de discriminar.
* "Aceptados" = estados de `cerebro2.metacalibracion.estados_aprobados` (por defecto APROBADO y CON_RESERVAS): bajo
  `modo_experimental` nada es APROBADO y el auditor decide en sombra para calibrarse.
* Un ticket sin resolver (partido pendiente) o anulado (alguna pierna cancelada) se excluye (simplificación v1).
* La alarma de falsos rechazos es una SEÑAL (revisión versionada), nunca un gatillo que relaje el auditor en runtime.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import numpy as np

from golmodel.modelos.incertidumbre import _wilson
from golmodel.modelos.mercados import CONDICIONES, PUSH

CAT_FALTA_AUDITORIA = "rechazo_por_falta_de_auditoria"
_ANULADOS = ("cancelado", "abandonado", "pospuesto")


@dataclass(frozen=True)
class RegistroMeta:
    estado: str
    categoria: str | None
    gano: bool | None                   # None = pendiente o anulado
    cuota_combinada: float | None


@dataclass(frozen=True)
class Tasa:
    n: int
    k: int
    p: float | None
    lo: float | None
    hi: float | None


@dataclass(frozen=True)
class MetricasAuditor:
    n_rechazados: int                   # rechazos sustantivos RESUELTOS
    n_aceptados: int                    # aceptados RESUELTOS
    n_excluidos_falta_auditoria: int
    n_sin_resolver: int
    tasa_rechazos_correctos: Tasa
    tasa_rechazos_incorrectos: Tasa
    tasa_aprobados_correctos: Tasa
    yield_rechazados: float | None
    yield_rechazados_ic: tuple[float, float] | None
    yield_aceptados: float | None
    valor_auditor: float | None         # yield_aceptados - yield_rechazados (proxy de yield; ver diseño dec. 7c)
    alarma_falsos_rechazos: bool
    muestra_suficiente: bool

    def a_json(self) -> dict:
        return asdict(self)


def _tasa(k: int, n: int, z: float) -> Tasa:
    if n == 0:
        return Tasa(0, 0, None, None, None)
    lo, hi = _wilson(k, n, z)
    return Tasa(n, k, k / n, float(lo), float(hi))


def _retorno(r: RegistroMeta) -> float | None:
    if r.gano is None or r.cuota_combinada is None:
        return None
    return float(r.cuota_combinada - 1.0) if r.gano else -1.0


def _bootstrap_ic(x: np.ndarray, m: dict) -> tuple[float, float]:
    rng = np.random.default_rng(int(m["bootstrap_semilla"]))
    medias = rng.choice(x, size=(int(m["bootstrap_n"]), len(x)), replace=True).mean(axis=1)
    a = (1.0 - float(m["bootstrap_ic"])) / 2.0
    lo, hi = np.quantile(medias, [a, 1.0 - a])
    return float(lo), float(hi)


def metricas(registros: list[RegistroMeta], cfg: dict) -> MetricasAuditor:
    m = cfg["cerebro2"]["metacalibracion"]
    aceptados_est, z, n_min = tuple(m["estados_aprobados"]), float(m["wilson_z"]), int(m["n_min"])

    excl = [r for r in registros if r.estado == "ABSTENCION" or r.categoria == CAT_FALTA_AUDITORIA]
    rech = [r for r in registros if r.estado == "RECHAZADO" and r.categoria != CAT_FALTA_AUDITORIA]
    acep = [r for r in registros if r.estado in aceptados_est and r.categoria != CAT_FALTA_AUDITORIA]
    rech_res = [r for r in rech if r.gano is not None]
    acep_res = [r for r in acep if r.gano is not None]
    sin_resolver = (len(rech) - len(rech_res)) + (len(acep) - len(acep_res))

    k_perdian = sum(1 for r in rech_res if r.gano is False)
    k_ganaban = sum(1 for r in rech_res if r.gano is True)
    k_acep_ganan = sum(1 for r in acep_res if r.gano is True)

    ret_rech = np.array([x for x in (_retorno(r) for r in rech_res) if x is not None], dtype=float)
    ret_acep = np.array([x for x in (_retorno(r) for r in acep_res) if x is not None], dtype=float)
    y_rech = float(ret_rech.mean()) if len(ret_rech) else None
    y_acep = float(ret_acep.mean()) if len(ret_acep) else None
    ic = _bootstrap_ic(ret_rech, m) if len(ret_rech) else None
    suficiente = len(rech_res) >= n_min
    return MetricasAuditor(
        n_rechazados=len(rech_res), n_aceptados=len(acep_res), n_excluidos_falta_auditoria=len(excl),
        n_sin_resolver=sin_resolver,
        tasa_rechazos_correctos=_tasa(k_perdian, len(rech_res), z),
        tasa_rechazos_incorrectos=_tasa(k_ganaban, len(rech_res), z),
        tasa_aprobados_correctos=_tasa(k_acep_ganan, len(acep_res), z),
        yield_rechazados=y_rech, yield_rechazados_ic=ic, yield_aceptados=y_acep,
        valor_auditor=(y_acep - y_rech) if (y_acep is not None and y_rech is not None) else None,
        alarma_falsos_rechazos=bool(suficiente and ic is not None and ic[0] > 0.0),
        muestra_suficiente=suficiente)


# --------------------------------------------------------------------- resolución ex-post de un ticket

def resolver_ticket(resumen: dict | None, partidos: dict) -> bool | None:
    """True/False si el ticket (todas las piernas) ganó/perdió; None si no se puede resolver (pendiente o anulado).
    `partidos`: {partido_id: (estado, goles_local, goles_visitante)} con el resultado a 90 minutos."""
    if not resumen or not resumen.get("piernas"):
        return None
    gano = True
    for p in resumen["piernas"]:
        r = partidos.get(p["partido_id"])
        if r is None:
            return None
        estado, gl, gv = r
        if estado in _ANULADOS or estado != "finalizado" or gl is None or gv is None:
            return None
        for s in p["selecciones"]:
            if s in PUSH or s not in CONDICIONES:
                return None                                  # push/desconocida: no se resuelve (v1)
            if not bool(CONDICIONES[s](int(gl), int(gv))):
                gano = False
    return gano


def cargar_registros(conn, *, origen: str | None = None) -> list[RegistroMeta]:
    """Lee los veredictos de Cerebro 2 y resuelve cada ticket con los resultados reales de `partidos`."""
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT estado, categoria, payload -> 'ticket_resumen' AS resumen FROM auditoria_decisiones "
                    "WHERE componente = 'cerebro2' AND (%(o)s::text IS NULL OR origen = %(o)s) ORDER BY id", {"o": origen})
        filas = cur.fetchall()
        ids = sorted({p["partido_id"] for f in filas if f["resumen"] for p in f["resumen"]["piernas"]})
        res = {}
        if ids:
            cur.execute("SELECT id, estado, goles_local, goles_visitante FROM partidos WHERE id = ANY(%s)", (ids,))
            res = {r["id"]: (r["estado"], r["goles_local"], r["goles_visitante"]) for r in cur.fetchall()}
    out = []
    for f in filas:
        r = f["resumen"]
        out.append(RegistroMeta(f["estado"], f["categoria"], resolver_ticket(r, res),
                                None if not r else r.get("cuota_combinada")))
    return out


def calcular_desde_bd(conn, cfg: dict, *, origen: str | None = None) -> MetricasAuditor:
    return metricas(cargar_registros(conn, origen=origen), cfg)


if __name__ == "__main__":                                   # python -m golmodel.cerebro2.metacalibracion
    import yaml
    from golmodel import db
    from golmodel.config import RUTA_DEFECTO

    cfg_v3 = yaml.safe_load((RUTA_DEFECTO.parent / "v3_umbrales.yaml").read_text(encoding="utf-8"))
    with db.conectar() as c:
        print(json.dumps(calcular_desde_bd(c, cfg_v3).a_json(), indent=2, default=str))
