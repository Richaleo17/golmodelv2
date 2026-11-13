"""Clima del día del partido con Open-Meteo (gratis, sin llave): temperatura en la sede a la hora del partido y
temperatura de ese mismo día en la ciudad de cada equipo (lo que acostumbra). Si falla, devuelve None y el factor
de clima simplemente no se aplica (nunca tumba el cálculo)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

BASE = "https://api.open-meteo.com/v1/forecast"


class Clima:
    def __init__(self, transport: httpx.BaseTransport | None = None, timeout_s: float = 10.0):
        self._http = httpx.Client(timeout=timeout_s, transport=transport, headers={"User-Agent": "golmodelv2/0.1"})
        self._cache: dict[tuple, dict | None] = {}

    def _serie(self, lat: float, lon: float, dia: str) -> dict | None:
        k = (round(lat, 2), round(lon, 2), dia)
        if k not in self._cache:
            try:
                r = self._http.get(BASE, params={"latitude": lat, "longitude": lon, "hourly": "temperature_2m",
                                                 "timezone": "UTC", "start_date": dia, "end_date": dia})
                r.raise_for_status()
                h = r.json().get("hourly") or {}
                self._cache[k] = dict(zip(h.get("time", []), h.get("temperature_2m", [])))
            except Exception:                                    # noqa: BLE001 — opcional
                self._cache[k] = None
        return self._cache[k]

    def temperatura(self, lat: float, lon: float, cuando: datetime) -> float | None:
        """°C a la hora (UTC) más cercana al inicio del partido."""
        cuando = cuando.astimezone(timezone.utc)
        s = self._serie(lat, lon, cuando.date().isoformat())
        if not s:
            return None
        clave = cuando.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
        v = s.get(clave)
        return float(v) if v is not None else None

    def media_dia(self, lat: float, lon: float, cuando: datetime) -> float | None:
        s = self._serie(lat, lon, cuando.astimezone(timezone.utc).date().isoformat())
        vals = [v for v in (s or {}).values() if v is not None]
        return float(sum(vals) / len(vals)) if vals else None
