"""Calibración del propio Cerebro 2 (decisión 7): ¿sus rechazos discriminan y cuánto cuestan?

Entrada: un DataFrame de decisiones ya resueltas (las cestas se resuelven igual aunque no se apuesten), con
columnas `estado`, `resultado` ('gana' | 'pierde' | 'nulo'), `cuota` y, opcionales, `p_conservadora` y `clv`.

* Las ABSTENCIONES no cuentan como rechazos: van aparte como `rechazo_por_falta_de_auditoria`.
* Los nulos (push/void) se excluyen de tasas y yield.
* U9  — alarma de falsos rechazos: yield plano de RECHAZADOS > 0 y significativo (bootstrap IC excluye 0).
        Es SEÑAL DE ALARMA para una revisión versionada, nunca un gatillo que relaje el auditor en runtime.
* U10 — `tasa_rechazos_correctos` ≥ piso, con n ≥ mínimo y cota inferior Wilson sobre el piso.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from golmodel.cerebro2.estadistica import bootstrap_diferencia, bootstrap_media, wilson
from golmodel.cerebro2.politica import Politica

APOSTABLES = ("APROBADO", "CON_RESERVAS")


@dataclass
class InformeAuditor:
    n_total: int
    por_estado: dict[str, int]
    rechazo_por_falta_de_auditoria: int
    tasa_rechazos_correctos: dict[str, Any] | None = None
    tasa_rechazos_incorrectos: float | None = None
    tasa_apostables_correctos: dict[str, Any] | None = None
    yield_rechazados: dict[str, Any] | None = None
    yield_apostables: dict[str, Any] | None = None
    cobertura_apostables: dict[str, Any] | None = None
    valor_auditor_clv: dict[str, Any] | None = None
    alarmas: list[str] = field(default_factory=list)

    def a_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _profit(d: pd.DataFrame) -> np.ndarray:
    return np.where(d["resultado"] == "gana", d["cuota"].astype(float) - 1.0, -1.0)


def _tasa(k: int, n: int, conf: float) -> dict[str, Any]:
    lo, hi = wilson(k, n, conf)
    return {"k": int(k), "n": int(n), "tasa": k / n, "wilson": [lo, hi]}


def evaluar(df: pd.DataFrame, politica: Politica, *, semilla: int = 0) -> InformeAuditor:
    conteo = df["estado"].value_counts().to_dict() if not df.empty else {}
    inf = InformeAuditor(n_total=len(df), por_estado={str(k): int(v) for k, v in conteo.items()},
                         rechazo_por_falta_de_auditoria=int(conteo.get("ABSTENCION", 0)))
    if df.empty:
        return inf
    res = df[df["resultado"].isin(["gana", "pierde"])]
    rech = res[res["estado"] == "RECHAZADO"]
    apos = res[res["estado"].isin(APOSTABLES)]

    if len(rech):
        k = int((rech["resultado"] == "pierde").sum())
        t = _tasa(k, len(rech), politica.wilson_ic)
        t["piso"] = politica.tasa_rechazos_correctos_min
        t["muestra_suficiente"] = len(rech) >= politica.rechazos_n_min
        t["cumple"] = bool(t["muestra_suficiente"] and t["wilson"][0] >= politica.tasa_rechazos_correctos_min)
        inf.tasa_rechazos_correctos = t
        inf.tasa_rechazos_incorrectos = 1 - t["tasa"]
        m, lo, hi = bootstrap_media(_profit(rech), politica.bootstrap_ic, semilla=semilla)
        inf.yield_rechazados = {"n": len(rech), "yield": m, "ic": [lo, hi]}
        if len(rech) >= politica.rechazos_n_min and lo > 0:
            inf.alarmas.append("U9: los RECHAZADOS ganan dinero de forma significativa → revisión versionada")
        if t["muestra_suficiente"] and not t["cumple"]:
            inf.alarmas.append("U10: tasa de rechazos correctos bajo el piso pre-registrado")

    if len(apos):
        k = int((apos["resultado"] == "gana").sum())
        inf.tasa_apostables_correctos = _tasa(k, len(apos), politica.wilson_ic)
        m, lo, hi = bootstrap_media(_profit(apos), politica.bootstrap_ic, semilla=semilla)
        inf.yield_apostables = {"n": len(apos), "yield": m, "ic": [lo, hi]}
        if "p_conservadora" in apos and apos["p_conservadora"].notna().all():
            lo_w, hi_w = wilson(k, len(apos), politica.wilson_ic)
            p_c = float(apos["p_conservadora"].mean())
            inf.cobertura_apostables = {"frecuencia": k / len(apos), "p_conservadora_media": p_c,
                                        "wilson": [lo_w, hi_w], "cumple": hi_w >= p_c}
            if not hi_w >= p_c:
                inf.alarmas.append("Apostables por debajo de p_conservadora: revisar τ/σ aguas arriba")

    if "clv" in res and len(apos) and len(rech):
        ca, cr = apos["clv"].dropna(), rech["clv"].dropna()
        if len(ca) and len(cr):
            d, lo, hi = bootstrap_diferencia(ca, cr, politica.bootstrap_ic, semilla=semilla)
            inf.valor_auditor_clv = {"diferencia": d, "ic": [lo, hi], "positivo": lo > 0,
                                     "n_apostables": len(ca), "n_rechazados": len(cr)}
    return inf
