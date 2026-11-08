"""Limpieza de CSVs de football-data.co.uk para modelado y backtest.

Cada regla deja constancia en el reporte: nada se descarta en silencio.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from golmodel.ingesta.proveedores.football_data_csv import cuotas_desde_df, leer_csv
from golmodel.modelos.mercados import GRUPO_DE

MAX_GOLES = 15                 # por encima: error de captura
MARGEN_MIN, MARGEN_MAX = 0.0, 0.30   # margen fuera de rango -> cuotas del grupo descartadas


@dataclass
class ReporteLimpieza:
    filas_leidas: int = 0
    partidos: int = 0
    finalizados: int = 0
    descartes: dict[str, int] = field(default_factory=dict)

    def descartar(self, motivo: str, n: int) -> None:
        if n:
            self.descartes[motivo] = self.descartes.get(motivo, 0) + n


def limpiar(texto_csv: str, liga: str) -> tuple[pd.DataFrame, pd.DataFrame, ReporteLimpieza]:
    rep = ReporteLimpieza()
    crudo = pd.read_csv(pd.io.common.StringIO(texto_csv), encoding_errors="replace")
    rep.filas_leidas = len(crudo)
    df = leer_csv(texto_csv, liga)
    rep.descartar("sin fecha/equipos/hora válida", rep.filas_leidas - len(df))

    df["HomeTeam"] = df["HomeTeam"].astype(str).str.strip()
    df["AwayTeam"] = df["AwayTeam"].astype(str).str.strip()
    misma = df["HomeTeam"] == df["AwayTeam"]
    rep.descartar("local = visitante", int(misma.sum()))
    df = df[~misma]

    for c in ("FTHG", "FTAG"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    # Partido sin jugar = ambos marcadores vacíos (válido). Un solo marcador vacío es error.
    jugado = df["FTHG"].notna() & df["FTAG"].notna()
    medio = df["FTHG"].notna() ^ df["FTAG"].notna()
    malo = medio | (jugado & ((df["FTHG"] < 0) | (df["FTAG"] < 0) | (df["FTHG"] > MAX_GOLES)
                              | (df["FTAG"] > MAX_GOLES) | (df["FTHG"] % 1 != 0) | (df["FTAG"] % 1 != 0)))
    rep.descartar("marcador inválido", int(malo.sum()))
    df = df[~malo]

    dia = df["inicio"].dt.date
    dup = df.assign(_d=dia).duplicated(subset=["HomeTeam", "AwayTeam", "_d"], keep="last")
    rep.descartar("duplicado", int(dup.sum()))
    df = df[~dup]

    partidos = pd.DataFrame({
        "inicio": df["inicio"], "liga": liga,
        "temporada": df["Season"].astype(str) if "Season" in df else None,
        "local": df["HomeTeam"], "visitante": df["AwayTeam"],
        "goles_local": df["FTHG"].astype("Int64"), "goles_visitante": df["FTAG"].astype("Int64"),
    }).sort_values("inicio").reset_index(drop=True)
    partidos["estado"] = partidos["goles_local"].notna().map({True: "finalizado", False: "programado"})
    rep.partidos = len(partidos)
    rep.finalizados = int((partidos["estado"] == "finalizado").sum())

    cuotas = []
    for cierre in (False, True):
        cuotas.extend(cuotas_desde_df(df, cierre=cierre))
    c = pd.DataFrame([x.__dict__ for x in cuotas])
    if not c.empty:
        c["grupo"] = c["seleccion"].map(GRUPO_DE)
        clave = ["local", "visitante", "inicio", "casa", "tipo", "grupo"]
        tam_grupo = c.groupby("grupo")["seleccion"].transform("nunique")
        n_sel = c.groupby(clave)["seleccion"].transform("nunique")
        incompleto = n_sel < tam_grupo
        rep.descartar("grupo de cuotas incompleto", int(incompleto.sum()))
        c = c[~incompleto]
        m = c.groupby(clave)["cuota"].transform(lambda s: (1 / s).sum() - 1)
        fuera = (m < MARGEN_MIN) | (m > MARGEN_MAX)
        rep.descartar("margen fuera de rango", int(fuera.sum()))
        c = c[~fuera].assign(margen=m[~fuera])
    return partidos, c, rep
