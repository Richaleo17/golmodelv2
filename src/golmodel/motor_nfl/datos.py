"""Datos NFL gratuitos.

* nflverse (GitHub, sin llave): `games.csv` con calendario, marcadores y líneas de cierre; play-by-play por temporada para la EPA.
* The Odds API (`americanfootball_nfl`, mercados h2h/spreads/totals): cuotas en vivo. Usa los MISMOS créditos que el fútbol
  (≈ 3 por consulta con una región): se consulta solo cuando lo pides en la pestaña NFL.
Todos los nombres de columna vienen de la documentación pública de nflverse (VERIFICAR si cambian).
"""
from __future__ import annotations

import gzip
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.motor_nfl.modelo import Juego

URL_GAMES = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
URL_PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{temporada}.csv.gz"
ZONA_NFL = ZoneInfo("America/New_York")      # gameday/gametime de nflverse están en hora del Este
COLS_PBP = ["game_id", "posteam", "defteam", "epa", "play_type", "season_type"]

# Abreviaturas de nflverse -> nombre completo que usa The Odds API (para emparejar cuotas).
NOMBRES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers", "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
ABREV = {v: k for k, v in NOMBRES.items()}


def _num(x):
    return None if pd.isna(x) else x


def parse_games(texto: str, temporadas: list[int]) -> list[dict]:
    df = pd.read_csv(io.StringIO(texto))
    df = df[df["season"].isin(temporadas)]
    filas = []
    for r in df.itertuples(index=False):
        inicio = None
        if isinstance(r.gameday, str):
            hora = r.gametime if isinstance(getattr(r, "gametime", None), str) else "13:00"
            inicio = datetime.fromisoformat(f"{r.gameday}T{hora}").replace(tzinfo=ZONA_NFL).astimezone(timezone.utc)
        filas.append({
            "juego_id": r.game_id, "temporada": int(r.season), "tipo": r.game_type, "semana": int(r.week), "inicio": inicio,
            "local": r.home_team, "visitante": r.away_team,
            "puntos_local": None if pd.isna(r.home_score) else int(r.home_score),
            "puntos_visitante": None if pd.isna(r.away_score) else int(r.away_score),
            "neutral": str(getattr(r, "location", "Home")) == "Neutral",
            "spread_cierre": _num(getattr(r, "spread_line", None)), "total_cierre": _num(getattr(r, "total_line", None)),
        })
    return filas


def epa_por_juego(df: pd.DataFrame) -> list[dict]:
    """EPA por jugada de cada equipo en cada juego (pase y carrera). Ofensiva = posteam; defensa = defteam."""
    d = df[df["play_type"].isin(["pass", "run"]) & df["epa"].notna() & df["posteam"].notna()]
    of = d.groupby(["game_id", "posteam"])["epa"].agg(["mean", "size"]).reset_index()
    de = d.groupby(["game_id", "defteam"])["epa"].mean().reset_index().rename(columns={"defteam": "posteam", "epa": "def"})
    m = of.merge(de, on=["game_id", "posteam"], how="inner")
    return [{"juego_id": r["game_id"], "equipo": r["posteam"], "epa_of": float(r["mean"]), "epa_def": float(r["def"]),
             "jugadas": int(r["size"])} for r in m.to_dict("records")]


def leer_pbp(contenido: bytes) -> pd.DataFrame:
    """Descomprime en flujo y solo conserva 6 columnas (el archivo trae ~370): cabe en la memoria de Render."""
    with gzip.GzipFile(fileobj=io.BytesIO(contenido)) as f:
        return pd.read_csv(f, usecols=lambda c: c in COLS_PBP, low_memory=False)


def juegos_de_filas(filas) -> list[Juego]:
    return [Juego(f["juego_id"], int(f["temporada"]), int(f["semana"]), f["local"], f["visitante"],
                  f["puntos_local"], f["puntos_visitante"], bool(f.get("neutral", False))) for f in filas]


def parse_cuotas(cuerpo, capturada: datetime) -> list[dict]:
    """Eventos de The Odds API -> filas (local, visitante, inicio, casa, mercado, seleccion, linea, cuota)."""
    salida = []
    for ev in cuerpo or []:
        loc, vis = ev["home_team"], ev["away_team"]
        inicio = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        for casa in ev.get("bookmakers", []):
            for m in casa.get("markets", []):
                for o in m.get("outcomes", []):
                    if m["key"] in ("h2h", "spreads"):
                        sel = "local" if o["name"] == loc else ("visitante" if o["name"] == vis else None)
                    elif m["key"] == "totals":
                        sel = {"Over": "over", "Under": "under"}.get(o["name"])
                    else:
                        sel = None
                    if sel:
                        salida.append({"odds_api_id": ev["id"], "local": ABREV.get(loc, loc), "visitante": ABREV.get(vis, vis),
                                       "inicio": inicio, "casa": casa["key"], "mercado": m["key"], "seleccion": sel,
                                       "linea": o.get("point"), "cuota": float(o["price"]), "capturada_en": capturada})
    return salida


class FuenteNFL:
    def __init__(self, odds_key: str | None, cfg: dict, **kw):
        self.cfg = cfg
        self.github = ClienteHTTP("nflverse", "https://github.com", timeout_s=120.0, **kw)
        self.odds = ClienteHTTP("odds_api", "https://api.the-odds-api.com", **kw) if odds_key else None
        self.odds_key = odds_key

    def games(self, temporadas: list[int]) -> list[dict]:
        r = self.github.get(URL_GAMES.removeprefix("https://github.com"), costo=0)
        return parse_games(r.text, temporadas)

    def pbp(self, temporada: int) -> pd.DataFrame:
        r = self.github.get(URL_PBP.format(temporada=temporada).removeprefix("https://github.com"), costo=0)
        return leer_pbp(r.content)

    def cuotas(self) -> list[dict]:
        if self.odds is None:
            return []
        n = self.cfg["nfl"]["cuotas"]
        params = {"apiKey": self.odds_key, "regions": ",".join(n["regiones"]), "markets": ",".join(n["mercados"]),
                  "oddsFormat": "decimal", "dateFormat": "iso"}

        def _costo(r):
            v = r.headers.get("x-requests-last")
            return int(v) if v and v.isdigit() else 0
        cuerpo = self.odds.get_json(f"/v4/sports/{n['sport_key']}/odds", params, costo=_costo)
        return parse_cuotas(cuerpo, datetime.now(timezone.utc))
