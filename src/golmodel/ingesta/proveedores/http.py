"""Cliente HTTP compartido: timeouts, reintentos y traducción de errores."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from golmodel.ingesta.proveedores.base import (
    ErrorFormato,
    ErrorPermanente,
    ErrorPlan,
    ErrorTransitorio,
)

# registrar(proveedor, endpoint, costo, http_status, restante_reportado)
Registrador = Callable[[str, str, int, int | None, int | None], None]


def _no_registrar(*_: Any) -> None:
    return None


class ClienteHTTP:
    def __init__(
        self,
        proveedor: str,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_s: float = 20.0,
        reintentos: int = 2,
        espera_base_s: float = 2.0,
        registrar: Registrador = _no_registrar,
        transport: httpx.BaseTransport | None = None,
        dormir: Callable[[float], None] = time.sleep,
    ):
        self.proveedor = proveedor
        self.reintentos = reintentos
        self.espera_base_s = espera_base_s
        self.registrar = registrar
        self._dormir = dormir
        self._http = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": "golmodelv2/0.1", **(headers or {})},
            timeout=timeout_s,
            transport=transport,
            follow_redirects=True,
        )

    def get(
        self,
        ruta: str,
        params: dict[str, Any] | None = None,
        *,
        costo: int | Callable[[httpx.Response], int] = 1,
        restante: Callable[[httpx.Response], int | None] | None = None,
    ) -> httpx.Response:
        ultimo: Exception | None = None
        for intento in range(self.reintentos + 1):
            try:
                r = self._http.get(ruta, params=params)
            except httpx.TransportError as e:  # timeout, DNS, conexión
                self.registrar(self.proveedor, ruta, 0, None, None)
                ultimo = ErrorTransitorio(self.proveedor, f"red: {e!r}")
            else:
                c = costo(r) if callable(costo) else costo
                self.registrar(self.proveedor, ruta, c, r.status_code, restante(r) if restante else None)
                if r.status_code < 400:
                    return r
                if r.status_code == 429 or r.status_code >= 500:
                    ultimo = ErrorTransitorio(self.proveedor, f"HTTP {r.status_code}")
                elif r.status_code in (401, 403):
                    raise ErrorPlan(self.proveedor, f"HTTP {r.status_code}: llave inválida o fuera del plan")
                else:
                    raise ErrorPermanente(self.proveedor, f"HTTP {r.status_code} en {ruta}")
            if intento < self.reintentos:
                self._dormir(self.espera_base_s * (2**intento))
        assert ultimo is not None
        raise ultimo

    def get_json(self, ruta: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        r = self.get(ruta, params, **kw)
        try:
            return r.json()
        except ValueError as e:
            raise ErrorFormato(self.proveedor, f"JSON inválido en {ruta}: {e}") from e

    def cerrar(self) -> None:
        self._http.close()
