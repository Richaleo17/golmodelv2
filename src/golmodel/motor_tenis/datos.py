"""Datos de tenis.

* tennis-data.co.uk (gratis, sin llave): un Excel por temporada y circuito con resultado, superficie, ronda, ranking y momios
  de cierre (Pinnacle «PS», Bet365, promedio). Es la base del Elo y del comparativo contra el mercado.
  ATP: /{año}/{año}.xlsx · WTA: /{año}w/{año}.xlsx   (VERIFICAR si cambian las rutas).
* The Odds API: torneos de tenis activos (`/v4/sports`, gratis) y momios h2h de cada torneo (1 crédito por torneo y región).
* Los nombres no coinciden entre fuentes («Sinner J.» vs «Jannik Sinner»): se usa la clave «apellido inicial».
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from datetime import datetime, timezone

import pandas as pd

from golmodel.ingesta.proveedores.http import ClienteHTTP

BASE_TD = "https://www.tennis-data.co.uk"
# Históricos de Jeff Sackmann (GitHub, públicos): la fuente con la que se calibró el Elo. tennis-data.co.uk responde 403 a Render
# (medido: 84 descargas fallidas y 0 partidos cargados en producción), así que el histórico sale de aquí.
BASE_SACKMANN = "https://raw.githubusercontent.com"
RUTA_SACKMANN = {"atp": "/JeffSackmann/tennis_atp/master/atp_matches_{a}.csv", "wta": "/JeffSackmann/tennis_wta/master/wta_matches_{a}.csv"}


def _plano(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.replace(".", " ").strip().lower())


def clave_abreviada(nombre: str) -> str:
    """«Sinner J.» → «sinner j»; «De Minaur A.» → «de minaur a»; «Auger-Aliassime F.» → «auger-aliassime f»."""
    toks = _plano(nombre).split()
    if not toks:
        return ""
    iniciales = []
    while len(toks) > 1 and len(toks[-1]) <= 2 and toks[-1].isalpha():
        iniciales.insert(0, toks.pop())
    return f"{' '.join(toks)} {iniciales[0][0] if iniciales else ''}".strip()


def claves_nombre_completo(nombre: str) -> list[str]:
    """«Alex de Minaur» → ['de minaur a', 'minaur a']: posibles claves (apellido compuesto primero)."""
    toks = _plano(nombre).split()
    if len(toks) < 2:
        return [" ".join(toks)]
    inicial = toks[0][0]
    return [f"{' '.join(toks[i:])} {inicial}" for i in range(1, len(toks))]


def resolver(nombre: str, conocidos: set[str]) -> str:
    """Clave de un nombre completo (The Odds API) entre los jugadores conocidos; si no está, la más probable."""
    cands = claves_nombre_completo(nombre)
    for c in cands:
        if c in conocidos:
            return c
    return cands[-1] if cands else _plano(nombre)


def _f(x):
    try:
        v = float(x)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def _i(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def parse_tennis_data(contenido: bytes, tour: str) -> list[dict]:
    """Excel de tennis-data.co.uk → partidos. Se descartan walkovers; los retiros se conservan (cuentan como resultado)."""
    df = pd.read_excel(io.BytesIO(contenido), engine="openpyxl")
    filas = []
    for i, r in enumerate(df.to_dict("records")):
        com = str(r.get("Comment") or "Completed")
        if "walk" in com.lower() or not isinstance(r.get("Winner"), str) or not isinstance(r.get("Loser"), str):
            continue
        fecha = pd.to_datetime(r.get("Date"), errors="coerce")
        if pd.isna(fecha):
            continue
        g, p = clave_abreviada(r["Winner"]), clave_abreviada(r["Loser"])
        torneo = str(r.get("Tournament") or "")
        f = fecha.date().isoformat()
        filas.append({
            "id": hashlib.md5(f"{tour}|{f}|{torneo}|{g}|{p}".encode()).hexdigest()[:20], "tour": tour, "fecha": f,
            "torneo": torneo, "superficie": str(r.get("Surface") or "Hard"), "ronda": str(r.get("Round") or ""),
            "best_of": _i(r.get("Best of")) or 3, "ganador": g, "perdedor": p, "nombre_g": r["Winner"], "nombre_p": r["Loser"],
            "rank_g": _i(r.get("WRank")), "rank_p": _i(r.get("LRank")), "comentario": com, "orden": i,
            # momio de cierre: Pinnacle si existe; si no, el promedio del mercado
            "cuota_g": _f(r.get("PSW")) or _f(r.get("AvgW")) or _f(r.get("B365W")),
            "cuota_p": _f(r.get("PSL")) or _f(r.get("AvgL")) or _f(r.get("B365L")),
        })
    return filas


def parse_sackmann(contenido: bytes, tour: str) -> list[dict]:
    """CSV de Jeff Sackmann → partidos (mismo formato que parse_tennis_data, sin momios). Nombres completos → la misma clave que se usa con
    The Odds API (`claves_nombre_completo`, primera opción). Se descartan walkovers; los retiros cuentan como resultado."""
    df = pd.read_csv(io.BytesIO(contenido), dtype=str, keep_default_na=False)
    filas = []
    for i, r in enumerate(df.to_dict("records")):
        sc = str(r.get("score") or "")
        if not r.get("winner_name") or not r.get("loser_name") or any(x in sc for x in ("W/O", "DEF", "w/o")):
            continue
        d = str(r.get("tourney_date") or "")
        if len(d) != 8 or not d.isdigit():
            continue
        f = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        g, p = claves_nombre_completo(r["winner_name"])[0], claves_nombre_completo(r["loser_name"])[0]
        torneo = str(r.get("tourney_name") or "")
        filas.append({
            "id": hashlib.md5(f"{tour}|{f}|{torneo}|{g}|{p}".encode()).hexdigest()[:20], "tour": tour, "fecha": f,
            "torneo": torneo, "superficie": str(r.get("surface") or "Hard") or "Hard", "ronda": str(r.get("round") or ""),
            "best_of": _i(r.get("best_of")) or 3, "ganador": g, "perdedor": p, "nombre_g": r["winner_name"], "nombre_p": r["loser_name"],
            "rank_g": _i(r.get("winner_rank")), "rank_p": _i(r.get("loser_rank")), "comentario": "Retired" if "RET" in sc else "Completed",
            "orden": _i(r.get("match_num")) or i, "cuota_g": None, "cuota_p": None})
    return filas


def parse_eventos(sport: dict, cuerpo, capturada: datetime, casas: list[str]) -> list[dict]:
    """Momios h2h de un torneo (The Odds API) → un renglón por partido con la casa preferida y la probabilidad sin margen."""
    tour = "wta" if "wta" in sport["key"] else "atp"
    salida = []
    for ev in cuerpo or []:
        j1, j2 = ev["home_team"], ev["away_team"]
        por_casa = {}
        for c in ev.get("bookmakers", []):
            for m in c.get("markets", []):
                if m["key"] != "h2h":
                    continue
                pr = {o["name"]: float(o["price"]) for o in m.get("outcomes", [])}
                if j1 in pr and j2 in pr:
                    por_casa[c["key"]] = (pr[j1], pr[j2])
        if not por_casa:
            continue
        casa = next((c for c in casas if c in por_casa), sorted(por_casa)[0])
        c1, c2 = por_casa[casa]
        salida.append({"odds_api_id": ev["id"], "tour": tour, "sport_key": sport["key"], "torneo": sport.get("title") or sport["key"],
                       "inicio": datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")), "jugador1": j1, "jugador2": j2,
                       "cuota1": c1, "cuota2": c2, "casa": casa, "capturada_en": capturada,
                       "best_of": 5 if tour == "atp" and re.search(r"aus_open|french_open|wimbledon|us_open", sport["key"]) else 3})
    return salida


class FuenteTenis:
    def __init__(self, odds_key: str | None, cfg: dict, **kw):
        self.cfg = cfg
        self.td = ClienteHTTP("tennis_data", BASE_TD, timeout_s=90.0, **kw)
        self.gh = ClienteHTTP("sackmann", BASE_SACKMANN, timeout_s=90.0, **kw)
        self.odds = ClienteHTTP("odds_api", "https://api.the-odds-api.com", **kw) if odds_key else None
        self.odds_key = odds_key

    def temporada(self, anio: int, tour: str) -> list[dict]:
        """Histórico de una temporada. Por defecto Sackmann (GitHub); con `tenis.fuente_historico: tennis_data` se usa tennis-data.co.uk
        (trae momios de cierre, pero bloquea a Render). Una sola fuente para todo el histórico: mezclar las dos duplicaría jugadores."""
        if (self.cfg.get("tenis") or {}).get("fuente_historico", "sackmann") == "tennis_data":
            ruta = f"/{anio}/{anio}.xlsx" if tour == "atp" else f"/{anio}w/{anio}.xlsx"
            return parse_tennis_data(self.td.get(ruta, costo=0).content, tour)
        return parse_sackmann(self.gh.get(RUTA_SACKMANN[tour].format(a=anio), costo=0).content, tour)

    def torneos_activos(self) -> list[dict]:
        if self.odds is None:
            return []
        lista = self.odds.get_json("/v4/sports", {"apiKey": self.odds_key}, costo=0)
        return [s for s in lista or [] if s.get("group") == "Tennis" and s.get("active") and not s.get("has_outrights")]

    def eventos(self, sport: dict) -> list[dict]:
        t = self.cfg["tenis"]["cuotas"]

        def _costo(r):
            v = r.headers.get("x-requests-last")
            return int(v) if v and v.isdigit() else 1
        cuerpo = self.odds.get_json(f"/v4/sports/{sport['key']}/odds",
                                    {"apiKey": self.odds_key, "regions": ",".join(t["regiones"]), "markets": "h2h",
                                     "oddsFormat": "decimal", "dateFormat": "iso"}, costo=_costo)
        return parse_eventos(sport, cuerpo, datetime.now(timezone.utc), list(t["casas_preferidas"]))
