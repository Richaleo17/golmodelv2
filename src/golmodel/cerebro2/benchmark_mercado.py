"""Benchmark de mercado independiente del Cerebro 1 (decisión 3).

C1 usa Pinnacle + Shin. El Cerebro 2 usa el CONSENSO de ≥ N casas elegibles: devig Power por casa, mediana por
selección y renormalización del grupo. Con menos de N casas no hay fuente alterna: el benchmark no es auditable
y el gate de mercado se degrada a AVISO (nunca PASS ni RECHAZADO).

"Mismo lote" (diseño §2.3): en el esquema, `cuotas.lote` identifica la captura de UNA casa; las casas de un
mismo evento se guardan con el mismo `capturada_en`. Por eso "mismo lote" se operacionaliza como "misma
captura" (mismo `capturada_en`): se usa la captura más reciente ≤ `info_hasta` con ≥ N casas completas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from golmodel.cerebro2.politica import Politica
from golmodel.modelos import devig
from golmodel.modelos.mercados import GRUPO_DE, GRUPOS

_DC = {"DC:1X": ("1X2:1", "1X2:X"), "DC:X2": ("1X2:X", "1X2:2"), "DC:12": ("1X2:1", "1X2:2")}


@dataclass(frozen=True)
class Benchmark:
    grupo: str
    p: dict[str, float] | None
    casas: tuple[str, ...]
    capturada_en: datetime | None
    auditable: bool
    motivo: str
    metodo: str

    def p_de(self, seleccion: str) -> float | None:
        if self.p is None:
            return None
        if seleccion in _DC:
            a, b = _DC[seleccion]
            return self.p[a] + self.p[b]
        return self.p.get(seleccion)

    def a_json(self) -> dict:
        return {"grupo": self.grupo, "p": self.p, "casas": list(self.casas), "auditable": self.auditable,
                "capturada_en": self.capturada_en.isoformat() if self.capturada_en else None,
                "motivo": self.motivo, "metodo": self.metodo, "agregacion": "mediana"}


def selecciones_del_grupo(seleccion: str) -> tuple[str, str]:
    """(grupo de devig, grupo de cuotas). Las DC se derivan del 1X2."""
    g = GRUPO_DE[seleccion]
    return g, ("1X2" if g == "DC" else g)


def consenso(cuotas: pd.DataFrame, seleccion: str, politica: Politica, info_hasta: datetime) -> Benchmark:
    """Consenso de mercado para el grupo de `seleccion`.

    `cuotas`: columnas casa, seleccion, cuota, capturada_en y, opcional, rol (de casas_apuestas).
    Solo se usa información con `capturada_en <= info_hasta` (anti look-ahead).
    """
    grupo, g_cuotas = selecciones_del_grupo(seleccion)
    sels = GRUPOS[g_cuotas]

    def sin(motivo: str, casas=()) -> Benchmark:
        return Benchmark(grupo, None, tuple(casas), None, False, motivo, politica.metodo_devig)

    if cuotas is None or cuotas.empty:
        return sin("sin cuotas")
    d = cuotas.copy()
    d["casa"] = d["casa"].astype(str).str.lower()
    d = d[d["casa"].isin(politica.casas_elegibles) & d["seleccion"].isin(sels)]
    if "rol" in d.columns:
        d = d[d["rol"] != "agregado"]           # nunca los promedios/máximos de football-data
    d = d[pd.to_datetime(d["capturada_en"], utc=True) <= pd.Timestamp(info_hasta)]
    if d.empty:
        return sin("sin cuotas de casas elegibles antes de info_hasta")
    d = d.assign(capturada_en=pd.to_datetime(d["capturada_en"], utc=True))

    mejor_n, mejor_casas = 0, ()
    for ts in sorted(d["capturada_en"].unique(), reverse=True):
        captura = d[d["capturada_en"] == ts]
        por_casa = {}
        for casa, filas in captura.groupby("casa"):
            r = filas.drop_duplicates("seleccion", keep="last").set_index("seleccion")["cuota"]
            if set(sels) <= set(r.index):
                por_casa[casa] = [float(r[s]) for s in sels]
        if len(por_casa) > mejor_n:
            mejor_n, mejor_casas = len(por_casa), tuple(sorted(por_casa))
        if len(por_casa) >= politica.n_min_casas:
            p = devig.consenso(por_casa, metodo=politica.metodo_devig, min_casas=politica.n_min_casas)
            ts_py = pd.Timestamp(ts).to_pydatetime()
            return Benchmark(grupo, {s: float(v) for s, v in zip(sels, np.asarray(p))}, tuple(sorted(por_casa)),
                             ts_py, True, f"consenso de {len(por_casa)} casas", politica.metodo_devig)
    return sin(f"máximo {mejor_n} casas en una misma captura (< {politica.n_min_casas})", mejor_casas)
