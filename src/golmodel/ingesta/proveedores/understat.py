"""Understat: xG por partido y xG/xA por jugador (EPL y Serie A).

Es la ÚNICA fuente de xG del MVP (familia 'understat'), en backtest y en
producción. No tiene API oficial: se leen los mismos datos JSON que usa su
web, con pausa entre requests y caché. Si falla, el partido queda sin xG
(el modelo usa solo goles para ese partido) y se reintenta en la siguiente
actualización; nunca se rellena con xG de otra familia.
"""
from __future__ import annotations

import codecs
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from golmodel.ingesta.proveedores.base import ErrorFormato, ErrorProveedor, ErrorTransitorio, Proveedor
from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.ingesta.tipos import Capacidad, Solicitud, XgJugador, XgPartido

BASE_URL = "https://understat.com"
FAMILIA = "understat"
CODIGOS = {"EPL": "EPL", "SA": "Serie_A", "LL": "La_liga", "BUN": "Bundesliga", "L1": "Ligue_1"}
ZONA_FECHAS = ZoneInfo("UTC")  # VERIFICAR: zona horaria del campo 'datetime'
_RE_VAR = r"var\s+{}\s*=\s*JSON\.parse\('(?P<d>.*?)'\)"
MANTENIMIENTO = "we will come back soon"
CABECERAS_AJAX = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}


def _resumen(r) -> str:
    """Diagnóstico corto de una respuesta: estado, tipo, tamaño e inicio del cuerpo."""
    cuerpo = " ".join(r.text[:120].split())
    return f"HTTP {r.status_code}, {r.headers.get('content-type', '?')}, {len(r.content)} bytes: «{cuerpo}»"


def _desde_html(html: str, variable: str) -> Any:
    m = re.search(_RE_VAR.format(variable), html, flags=re.S)
    if not m:
        raise ErrorFormato("understat", f"no se encontró '{variable}' en el HTML")
    return json.loads(codecs.decode(m.group("d"), "unicode_escape"))


def parse_partidos(fechas: list[dict], liga: str) -> list[XgPartido]:
    salida = []
    for p in fechas:
        if not p.get("isResult"):
            continue
        inicio = datetime.fromisoformat(p["datetime"]).replace(tzinfo=ZONA_FECHAS).astimezone(timezone.utc)
        salida.append(XgPartido("understat", FAMILIA, liga, inicio, p["h"]["title"], p["a"]["title"],
                                float(p["xG"]["h"]), float(p["xG"]["a"]), str(p["id"])))
    return salida


def parse_jugadores(jugadores: list[dict], liga: str, temporada: str) -> list[XgJugador]:
    def _ent(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return [XgJugador("understat", FAMILIA, liga, temporada, j["player_name"], j["team_title"],
                      int(j["time"]), float(j["xG"]), float(j["xA"]), str(j["id"]), _ent(j.get("games")), _ent(j.get("shots")))
            for j in jugadores]


class Understat(Proveedor):
    nombre = "understat"
    capacidades = frozenset({Capacidad.XG, Capacidad.XG_JUGADORES})
    ligas = frozenset(CODIGOS)
    familia_xg = FAMILIA

    def __init__(self, pausa_s: float = 5.0, **kw: Any):
        self.pausa_s = pausa_s
        self._ultimo = 0.0
        self._cache: dict[tuple[str, int], dict] = {}
        self.http = ClienteHTTP(self.nombre, BASE_URL, **kw)

    def _esperar(self) -> None:
        falta = self.pausa_s - (time.monotonic() - self._ultimo)
        if falta > 0:
            time.sleep(falta)
        self._ultimo = time.monotonic()

    def _liga_temporada(self, liga: str, anio: int) -> dict:
        clave = (liga, anio)
        if clave in self._cache:
            return self._cache[clave]
        codigo = CODIGOS[liga]
        pagina = f"/league/{codigo}/{anio}"
        fallas: list[str] = []
        # 1) Página de la liga, como un navegador: crea la cookie de sesión que el endpoint de datos
        #    exige, y además sirve si la web volviera a incrustar el JSON (formato anterior).
        self._esperar()
        r_pag = self.http.get(pagina, costo=0)
        if MANTENIMIENTO in r_pag.text.lower():
            # Página de mantenimiento: falla temporal (el interruptor reintenta en minutos, no en 24 h).
            raise ErrorTransitorio(self.nombre, "Understat está en mantenimiento ('We will come back soon')")
        try:
            datos = {"dates": _desde_html(r_pag.text, "datesData"), "players": _desde_html(r_pag.text, "playersData")}
            self._cache[clave] = datos
            return datos
        except ErrorFormato:
            fallas.append(f"HTML sin datesData ({_resumen(r_pag)})")
        # 2) Endpoint JSON que usa la web (petición AJAX con la cookie de la página y Referer).
        self._esperar()
        r = self.http.get(f"/getLeagueData/{codigo}/{anio}", costo=0,
                          headers=CABECERAS_AJAX | {"Referer": f"{BASE_URL}{pagina}"})
        try:
            cuerpo = r.json()
            datos = {"dates": cuerpo["dates"], "players": cuerpo["players"]}
        except (ValueError, KeyError, TypeError) as e:
            fallas.append(f"getLeagueData: {type(e).__name__} ({_resumen(r)})")
            raise ErrorFormato(self.nombre, f"{codigo} {anio}: ninguna vía funcionó | " + " | ".join(fallas)) from e
        self._cache[clave] = datos
        return datos

    def obtener(self, s: Solicitud) -> list[Any]:
        if not s.temporada:
            raise ErrorFormato(self.nombre, "se requiere temporada ('2025-2026')")
        anio = int(s.temporada[:4])
        datos = self._liga_temporada(s.liga, anio)
        if s.capacidad is Capacidad.XG_JUGADORES:
            return parse_jugadores(datos["players"], s.liga, s.temporada)
        partidos = parse_partidos(datos["dates"], s.liga)
        if s.desde:
            partidos = [p for p in partidos if p.inicio.date() >= s.desde]
        if s.hasta:
            partidos = [p for p in partidos if p.inicio.date() <= s.hasta]
        return partidos
