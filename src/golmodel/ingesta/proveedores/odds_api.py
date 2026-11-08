"""The Odds API v4 (plan Free: 500 créditos/mes; costo = mercados × regiones).

Documentación: https://the-odds-api.com/liveapi/guides/v4/
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from golmodel.ingesta.proveedores.base import ErrorFormato, Proveedor
from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.ingesta.tipos import Capacidad, CuotaExterna, PartidoExterno, Solicitud

BASE_URL = "https://api.the-odds-api.com"

SPORT_KEYS = {  # VERIFICAR EN DOCUMENTACIÓN con GET /v4/sports (no cuesta créditos)
    "EPL": "soccer_epl",
    "SA": "soccer_italy_serie_a",
    "MX1": "soccer_mexico_ligamx",
    "MLS": "soccer_usa_mls",
    "BR1": "soccer_brazil_campeonato",
}
LINEAS = {1.5, 2.5, 3.5}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_eventos(cuerpo: Any, tipo: str, observada_en: datetime | None = None) -> list[CuotaExterna]:
    """observada_en: momento de NUESTRA consulta. La frescura se mide desde ahí, no desde el
    last_update de la casa (una cuota que no cambió en 2 h sigue siendo la cuota actual)."""
    if not isinstance(cuerpo, list):
        raise ErrorFormato("odds_api", "se esperaba una lista de eventos")
    salida = []
    for ev in cuerpo:
        local, visit = ev["home_team"], ev["away_team"]
        inicio = _parse_iso(ev["commence_time"])
        for casa in ev.get("bookmakers", []):
            capt = observada_en or _parse_iso(casa.get("last_update") or ev["commence_time"])
            for m in casa.get("markets", []):
                for o in m.get("outcomes", []):
                    sel = None
                    if m["key"] == "h2h":
                        sel = {local: "1X2:1", visit: "1X2:2", "Draw": "1X2:X"}.get(o["name"])
                    elif m["key"] == "totals" and o.get("point") in LINEAS:
                        sel = f"OU:{o['point']}:{o['name'].lower()}"
                    elif m["key"] == "btts":
                        sel = {"Yes": "BTTS:si", "No": "BTTS:no"}.get(o["name"])
                    if sel:
                        salida.append(CuotaExterna("odds_api", casa["key"], sel, float(o["price"]),
                                                   tipo, capt, local, visit, inicio, ev["id"]))
    return salida


def parse_calendario(cuerpo: Any, liga: str) -> list[PartidoExterno]:
    """Calendario de la liga a partir del listado de eventos (sin cuotas)."""
    if not isinstance(cuerpo, list):
        raise ErrorFormato("odds_api", "se esperaba una lista de eventos")
    return [PartidoExterno("odds_api", liga, _parse_iso(ev["commence_time"]), ev["home_team"], ev["away_team"],
                           "programado", id_externo=ev["id"]) for ev in cuerpo]


class OddsApi(Proveedor):
    nombre = "odds_api"
    capacidades = frozenset({Capacidad.CUOTAS, Capacidad.CUOTAS_CIERRE, Capacidad.FIXTURES})
    ligas = frozenset(SPORT_KEYS)
    presupuesto = "odds_api"

    def __init__(self, api_key: str, *, regiones: list[str], mercados: list[str], **kw: Any):
        self.api_key = api_key
        self.regiones = regiones
        self.mercados = mercados
        self.http = ClienteHTTP(self.nombre, BASE_URL, **kw)

    def costo_estimado(self, s: Solicitud) -> int:
        if s.capacidad is Capacidad.FIXTURES:
            return 0     # VERIFICAR EN DOCUMENTACIÓN: /events no consume créditos (el contador real lo confirma)
        return len(self.regiones) * len(self.mercados)

    @staticmethod
    def _costo(r) -> int:
        v = r.headers.get("x-requests-last")
        return int(v) if v and v.isdigit() else 0

    @staticmethod
    def _restante(r):
        v = r.headers.get("x-requests-remaining")
        try:
            return int(float(v)) if v else None
        except ValueError:
            return None

    def obtener(self, s: Solicitud) -> list[Any]:
        ahora = datetime.now(timezone.utc)
        if s.capacidad is Capacidad.FIXTURES:
            # Solo partidos por venir; la consulta registra su costo real según el header del proveedor.
            params = {"apiKey": self.api_key, "dateFormat": "iso"}
            if s.hasta:
                params["commenceTimeTo"] = _iso(datetime.combine(s.hasta, datetime.min.time(), timezone.utc)
                                                + timedelta(days=1))
            cuerpo = self.http.get_json(f"/v4/sports/{SPORT_KEYS[s.liga]}/events", params,
                                        costo=self._costo, restante=self._restante)
            return parse_calendario(cuerpo, s.liga)
        params: dict[str, Any] = {
            "apiKey": self.api_key, "regions": ",".join(self.regiones),
            "markets": ",".join(self.mercados), "oddsFormat": "decimal", "dateFormat": "iso",
        }
        if s.desde:
            params["commenceTimeFrom"] = _iso(datetime.combine(s.desde, datetime.min.time(), timezone.utc))
        if s.hasta:
            params["commenceTimeTo"] = _iso(datetime.combine(s.hasta, datetime.min.time(), timezone.utc)
                                            + timedelta(days=1))
        if s.ids_partido.get(self.nombre):
            params["eventIds"] = s.ids_partido[self.nombre]
        cuerpo = self.http.get_json(f"/v4/sports/{SPORT_KEYS[s.liga]}/odds", params,
                                    costo=self._costo, restante=self._restante)
        tipo = "cierre" if s.capacidad is Capacidad.CUOTAS_CIERRE else "actual"
        return parse_eventos(cuerpo, tipo, observada_en=ahora)
