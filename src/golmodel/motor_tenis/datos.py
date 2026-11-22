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
from golmodel.momios import cuota_valida

BASE_TD = "https://www.tennis-data.co.uk"
# Históricos de Jeff Sackmann: el repositorio original ya no responde (404); se usa el espejo público con el que se calibró el Elo
# (Aneeshers/tennis-sackmann-archive, congelado: llega al 25-may-2026). tennis-data.co.uk responde 403 a Render. Lo que falta desde
# esa fecha, y los partidos próximos, salen de ESPN (`parse_espn_tenis`).
BASE_SACKMANN = "https://raw.githubusercontent.com"
RUTA_SACKMANN = {"atp": "/Aneeshers/tennis-sackmann-archive/main/atp/atp_matches_{a}.csv",
                 "wta": "/Aneeshers/tennis-sackmann-archive/main/wta/wta_matches_{a}.csv"}
RUTA_SACKMANN_CH = "/Aneeshers/tennis-sackmann-archive/main/atp/atp_matches_qual_chall_{a}.csv"   # Challenger + qualys ATP
BASE_ESPN = "https://site.api.espn.com"
# LiveScore (JSON público de su app): única fuente gratuita que encontramos con el calendario y resultados de los Challenger, que ni
# ESPN ni The Odds API cubren. Los ITF (M15/M25) no vienen en ninguna fuente gratuita.
BASE_LIVESCORE = "https://prod-public-api.livescore.com"


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


def parse_espn_tenis(cuerpo: dict) -> list[dict]:
    """Scoreboard de tenis de ESPN → un renglón por partido de SINGLES (los dobles se ignoran). El circuito sale del cuadro
    («mens-singles» → atp, «womens-singles» → wta): el scoreboard de un circuito trae también el cuadro del otro en torneos mixtos.
    Estado: «pre» (por jugar), «post» con ganador (resultado; los walkovers se marcan) o «in»."""
    salida = []
    for e in (cuerpo or {}).get("events") or []:
        lugar = ((e.get("venue") or {}).get("displayName") or "").split(",")[0].strip()
        torneo = f"{e.get('name') or ''} ({lugar})" if lugar else str(e.get("name") or "")
        for g in e.get("groupings") or []:
            slug = ((g.get("grouping") or {}).get("slug") or "")
            if slug not in ("mens-singles", "womens-singles"):
                continue
            tour = "atp" if slug == "mens-singles" else "wta"
            for c in g.get("competitions") or []:
                cs = sorted(c.get("competitors") or [], key=lambda x: x.get("order", 0))
                if len(cs) != 2 or any(x.get("type") != "athlete" or not (x.get("athlete") or {}).get("displayName")
                                       or str(x["athlete"]["displayName"]).strip().upper() in ("TBD", "TBA", "BYE") for x in cs):
                    continue                                        # dobles, cuadro sin definir
                tipo = ((c.get("status") or {}).get("type") or {})
                ganador = next((i for i, x in enumerate(cs) if x.get("winner") is True), None)
                salida.append({
                    "id": str(c["id"]), "tour": tour, "torneo": torneo, "evento_inicio": str(e.get("date") or "")[:10],
                    "inicio": datetime.fromisoformat(str(c.get("date") or e.get("date")).replace("Z", "+00:00")),
                    "estado": tipo.get("state"), "status": str(tipo.get("name") or ""),
                    "jugador1": cs[0]["athlete"]["displayName"], "jugador2": cs[1]["athlete"]["displayName"], "ganador": ganador,
                    "rank1": _i(((cs[0].get("curatedRank") or {}).get("current"))), "rank2": _i(((cs[1].get("curatedRank") or {}).get("current"))),
                    "ronda": str((c.get("round") or {}).get("displayName") or ""),
                    "best_of": 5 if tour == "atp" and e.get("major") else 3})
    return salida


_ESTADO_LS = {"NS": "pre", "FT": "post", "Ret.": "post", "AET": "post"}


def parse_livescore(cuerpo: dict, competencias: list[str]) -> list[dict]:
    """Día de tenis de LiveScore → mismo formato que `parse_espn_tenis`, solo singles y solo las competencias pedidas (Challenger).
    `evento_inicio` va 7 días antes del partido: el histórico de Sackmann fecha cada partido con el INICIO de su torneo, así que solo
    se agregan partidos al menos una semana posteriores al corte (nunca se duplica un torneo que ya está en el histórico)."""
    from datetime import timedelta
    salida = []
    for s in (cuerpo or {}).get("Stages") or []:
        if str(s.get("Cnm") or "") not in competencias:
            continue
        torneo = f"{s.get('Cnm')} {s.get('Snm') or ''}".strip()
        for e in s.get("Events") or []:
            t1, t2 = e.get("T1") or [], e.get("T2") or []
            if len(t1) != 1 or len(t2) != 1 or e.get("Et") not in (10, 15):
                continue                                            # dobles
            n1, n2 = str(t1[0].get("Nm") or "").strip(), str(t2[0].get("Nm") or "").strip()
            if not n1 or not n2 or n1.upper() in ("TBD", "TBA") or n2.upper() in ("TBD", "TBA"):
                continue
            try:
                inicio = datetime.strptime(str(e["Esd"]), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            eps = str(e.get("Eps") or "")
            estado = _ESTADO_LS.get(eps, "otro" if eps in ("Canc.", "W.O.", "Postp.", "Abd.", "Susp.") else "in")
            ewt = e.get("Ewt")
            salida.append({
                "id": f"l{e['Eid']}", "tour": "atp" if e.get("Et") == 10 else "wta", "torneo": torneo,
                "evento_inicio": (inicio.date() - timedelta(days=7)).isoformat(), "inicio": inicio, "estado": estado,
                "status": "STATUS_RETIRED" if eps.startswith("Ret") else ("STATUS_FINAL" if estado == "post" else eps),
                "jugador1": n1, "jugador2": n2, "ganador": {1: 0, 2: 1}.get(ewt) if estado == "post" else None,
                "rank1": None, "rank2": None, "ronda": "", "best_of": 3})
    return salida


def resultados_espn(filas: list[dict], superficie) -> list[dict]:
    """Partidos terminados de ESPN → formato de tenis_partidos (id «e:<partido>»). Sin walkovers ni partidos sin ganador."""
    out = []
    for f in filas:
        if f["estado"] != "post" or f["ganador"] is None or "WALKOVER" in f["status"] or "CANCEL" in f["status"]:
            continue
        if "qualif" in f["ronda"].lower():                         # el histórico de Sackmann no trae la qualy: mismo criterio
            continue
        g, p = (f["jugador1"], f["jugador2"]) if f["ganador"] == 0 else (f["jugador2"], f["jugador1"])
        rg, rp = (f["rank1"], f["rank2"]) if f["ganador"] == 0 else (f["rank2"], f["rank1"])
        out.append({"id": f"e:{f['id']}", "tour": f["tour"], "fecha": f["inicio"].date().isoformat(), "torneo": f["torneo"],
                    "superficie": superficie(f["torneo"]), "ronda": f["ronda"], "best_of": f["best_of"],
                    "ganador": claves_nombre_completo(g)[0], "perdedor": claves_nombre_completo(p)[0], "nombre_g": g, "nombre_p": p,
                    "rank_g": rg, "rank_p": rp, "comentario": "Retired" if "RETIRED" in f["status"] else "Completed",
                    "orden": int(f["inicio"].timestamp()) % 100000, "cuota_g": None, "cuota_p": None})
    return out


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
                pr = {o["name"]: float(o["price"]) for o in m.get("outcomes", []) if cuota_valida(o.get("price"))}
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
        self.espn_http = ClienteHTTP("espn", BASE_ESPN, timeout_s=45.0, **kw)
        self.ls_http = ClienteHTTP("livescore", BASE_LIVESCORE, timeout_s=30.0, headers={"User-Agent": "Mozilla/5.0"}, **kw)
        self.odds = ClienteHTTP("odds_api", "https://api.the-odds-api.com", **kw) if odds_key else None
        self.odds_key = odds_key

    def temporada(self, anio: int, tour: str) -> list[dict]:
        """Histórico de una temporada. Por defecto Sackmann (GitHub); con `tenis.fuente_historico: tennis_data` se usa tennis-data.co.uk
        (trae momios de cierre, pero bloquea a Render). Una sola fuente para todo el histórico: mezclar las dos duplicaría jugadores."""
        if (self.cfg.get("tenis") or {}).get("fuente_historico", "sackmann") == "tennis_data":
            ruta = f"/{anio}/{anio}.xlsx" if tour == "atp" else f"/{anio}w/{anio}.xlsx"
            return parse_tennis_data(self.td.get(ruta, costo=0).content, tour)
        return parse_sackmann(self.gh.get(RUTA_SACKMANN[tour].format(a=anio), costo=0).content, tour)

    def temporada_challenger(self, anio: int) -> list[dict]:
        """Challenger + qualys ATP de una temporada (Sackmann). id «c:…» para distinguirlos del circuito principal."""
        filas = parse_sackmann(self.gh.get(RUTA_SACKMANN_CH.format(a=anio), costo=0).content, "atp")
        for f in filas:
            f["id"] = "c:" + f["id"][:18]
        return filas

    def livescore(self, fecha, competencias: list[str]) -> list[dict]:
        return parse_livescore(self.ls_http.get_json(f"/v1/api/app/date/tennis/{fecha.strftime('%Y%m%d')}/0", {"MD": "1"}, costo=0),
                               competencias)

    def espn(self, tour: str, fecha) -> list[dict]:
        """Torneos de ESPN activos ese día (cuadro completo: resultados y partidos por jugar)."""
        return parse_espn_tenis(self.espn_http.get_json(f"/apis/site/v2/sports/tennis/{tour}/scoreboard",
                                                        {"dates": fecha.strftime("%Y%m%d")}, costo=0))

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
