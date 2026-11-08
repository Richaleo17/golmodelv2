"""CSVs de football-data.co.uk: resultados y cuotas históricas (incluido el cierre).

Gratis y sin límite. Es la base del backtest y el respaldo del cierre para CLV.
Columnas: https://www.football-data.co.uk/notes.txt  (# VERIFICAR EN DOCUMENTACIÓN por liga)
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from golmodel.ingesta.proveedores.base import ErrorFormato, Proveedor
from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.ingesta.tipos import Capacidad, CuotaExterna, PartidoExterno, Solicitud

BASE_URL = "https://www.football-data.co.uk"
CODIGOS = {"EPL": ("E0", "principal"), "SA": ("I1", "principal"),
           "MX1": ("MEX", "extra"), "MLS": ("USA", "extra"), "BR1": ("BRA", "extra"),
           "LL": ("SP1", "principal"), "BUN": ("D1", "principal"), "L1": ("F1", "principal"),
           "ERE": ("N1", "principal"), "BEL": ("B1", "principal"), "POR": ("P1", "principal")}
ZONA_CSV = ZoneInfo("Europe/London")  # VERIFICAR EN DOCUMENTACIÓN: hora del campo Time

# (prefijo de casa, casa normalizada). Pre-partido = sin 'C'; cierre = con 'C'.
# 'Max' (máximo entre casas) no se carga: no es una casa real y su "margen" es negativo.
CASAS = [("PS", "pinnacle"), ("B365", "bet365"), ("Avg", "promedio")]


def _columnas_1x2(pref: str, cierre: bool) -> dict[str, str]:
    c = "C" if cierre else ""
    return {f"{pref}{c}H": "1X2:1", f"{pref}{c}D": "1X2:X", f"{pref}{c}A": "1X2:2"}


def _columnas_ou(pref: str, cierre: bool) -> dict[str, str]:
    p = "P" if pref == "PS" else pref        # Pinnacle O/U usa 'P>2.5' / 'PC>2.5'
    c = "C" if cierre else ""
    return {f"{p}{c}>2.5": "OU:2.5:over", f"{p}{c}<2.5": "OU:2.5:under"}


def leer_csv(texto: str, liga: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(texto), encoding_errors="replace")
    df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam", "HG": "FTHG", "AG": "FTAG"})
    faltan = {"Date", "HomeTeam", "AwayTeam"} - set(df.columns)
    if faltan:
        raise ErrorFormato("football_data_csv", f"faltan columnas {faltan}")
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "Date"]).copy()
    hora = (df["Time"].fillna("15:00").astype(str) if "Time" in df.columns
            else pd.Series("15:00", index=df.index))
    local = pd.to_datetime(df["Date"].astype(str) + " " + hora, dayfirst=True, format="mixed")
    df["inicio"] = local.dt.tz_localize(ZONA_CSV, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    df["liga"] = liga
    return df.dropna(subset=["inicio"])


def partidos_desde_df(df: pd.DataFrame) -> list[PartidoExterno]:
    salida = []
    for r in df.itertuples(index=False):
        fin = pd.notna(getattr(r, "FTHG", None)) and pd.notna(getattr(r, "FTAG", None))
        salida.append(PartidoExterno(
            fuente="football_data_csv", liga=r.liga, inicio=r.inicio.to_pydatetime(),
            local=str(r.HomeTeam).strip(), visitante=str(r.AwayTeam).strip(),
            estado="finalizado" if fin else "programado",
            temporada=str(getattr(r, "Season", "")) or None,
            goles_local=int(r.FTHG) if fin else None, goles_visitante=int(r.FTAG) if fin else None,
        ))
    return salida


def _numero(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None                          # descarta NaN


def cuotas_desde_df(df: pd.DataFrame, *, cierre: bool) -> list[CuotaExterna]:
    salida = []
    tipo = "cierre" if cierre else "prepartido"
    for pref, casa in CASAS:
        cols = {**_columnas_1x2(pref, cierre), **_columnas_ou(pref, cierre)}
        presentes = {c: s for c, s in cols.items() if c in df.columns}
        for r in df.to_dict("records"):
            inicio = r["inicio"].to_pydatetime()
            # Cierre: observado justo antes del inicio. Pre-partido: fecha desconocida;
            # football-data la toma ~viernes/martes previo -> se fecha 2 días antes (conservador).
            capt = inicio if cierre else inicio - pd.Timedelta(days=2).to_pytimedelta()
            for col, sel in presentes.items():
                v = _numero(r.get(col))             # celdas con texto («x», «-») cuentan como sin cuota
                if v is not None and v > 1.0:
                    salida.append(CuotaExterna("football_data_csv", casa, sel, v, tipo, capt,
                                               str(r["HomeTeam"]).strip(), str(r["AwayTeam"]).strip(),
                                               inicio))
    return salida


def ruta_csv(liga: str, anio_inicio: int | None) -> str:
    codigo, formato = CODIGOS[liga]
    if formato == "extra":
        return f"/new/{codigo}.csv"                       # todas las temporadas en un archivo
    if anio_inicio is None:
        raise ValueError("Las ligas principales necesitan año de temporada")
    yy = f"{anio_inicio % 100:02d}{(anio_inicio + 1) % 100:02d}"
    return f"/mmz4281/{yy}/{codigo}.csv"


class FootballDataCsv(Proveedor):
    nombre = "football_data_csv"
    capacidades = frozenset({Capacidad.RESULTADOS, Capacidad.CUOTAS_CIERRE, Capacidad.CUOTAS})
    ligas = frozenset(CODIGOS)

    def __init__(self, **kw: Any):
        self.http = ClienteHTTP(self.nombre, BASE_URL, **kw)

    def obtener(self, s: Solicitud) -> list[Any]:
        anio = int(s.temporada[:4]) if s.temporada else (s.desde or datetime.now(timezone.utc).date()).year
        if CODIGOS[s.liga][1] == "principal" and not s.temporada:
            fecha = s.desde or datetime.now(timezone.utc).date()
            anio = fecha.year if fecha.month >= 7 else fecha.year - 1
        df = leer_csv(self.http.get(ruta_csv(s.liga, anio), costo=0).text, s.liga)
        if s.desde:
            df = df[df["inicio"].dt.date >= s.desde]
        if s.hasta:
            df = df[df["inicio"].dt.date <= s.hasta]
        if s.capacidad is Capacidad.RESULTADOS:
            return [p for p in partidos_desde_df(df) if p.estado == "finalizado"]
        return cuotas_desde_df(df, cierre=s.capacidad is Capacidad.CUOTAS_CIERRE)
