"""Acceso a la app: contraseña con dos roles y "recordarme" con token firmado.

* admin  (APP_PASSWORD)        : todo, incluidos actualizar, capturar cuotas y resolver nombres.
* lector (APP_VIEWER_PASSWORD) : solo consulta (ideal para compartir con un amigo).

Sin contraseña la app solo se permite si escucha únicamente en la computadora local
(UI_BIND=127.0.0.1). Si está abierta a la red y no hay contraseña, NO muestra nada.
Las comparaciones usan hmac.compare_digest (tiempo constante) y los intentos fallidos
se limitan: 5 en 10 minutos bloquean 5 minutos.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

ADMIN, LECTOR = "admin", "lector"
DIAS_TOKEN = 30
HOSTS_LOCALES = {"127.0.0.1", "localhost", "::1"}


def claves_desde_env(env: Mapping[str, str]) -> tuple[str, str]:
    return env.get("APP_PASSWORD", "").strip(), env.get("APP_VIEWER_PASSWORD", "").strip()


def modo_acceso(admin: str, lector: str, bind: str) -> str:
    """'abierto' | 'login' | 'sin_clave' (abierta a la red sin contraseña) | 'config_invalida'."""
    if not admin and not lector:
        return "abierto" if bind.strip() in HOSTS_LOCALES else "sin_clave"
    if not admin:
        return "config_invalida"          # contraseña de lector sin contraseña de administrador
    return "login"


def verificar(clave: str, admin: str, lector: str) -> str | None:
    c = clave.encode()
    es_admin = bool(admin) and hmac.compare_digest(c, admin.encode())
    es_lector = bool(lector) and hmac.compare_digest(c, lector.encode())
    return ADMIN if es_admin else (LECTOR if es_lector else None)


def secreto(admin: str, lector: str, env: Mapping[str, str]) -> bytes:
    """Llave de los tokens. Cambiar cualquier contraseña invalida los tokens ya emitidos."""
    base = env.get("APP_SECRET", "").strip() or f"{admin}|{lector}"
    return hashlib.sha256(f"golmodelv2|{base}".encode()).digest()


def _firma(sec: bytes, rol: str, exp: int) -> str:
    return hmac.new(sec, f"{rol}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def crear_token(rol: str, sec: bytes, ahora: float | None = None, dias: int = DIAS_TOKEN) -> str:
    exp = int((ahora if ahora is not None else time.time()) + dias * 86400)
    return f"{rol}.{exp}.{_firma(sec, rol, exp)}"


def validar_token(token: str | None, sec: bytes, ahora: float | None = None) -> str | None:
    try:
        rol, exp_txt, firma = (token or "").split(".")
        exp = int(exp_txt)
    except ValueError:
        return None
    if rol not in (ADMIN, LECTOR) or not hmac.compare_digest(firma, _firma(sec, rol, exp)):
        return None
    return rol if (ahora if ahora is not None else time.time()) <= exp else None


@dataclass
class Limitador:
    """Freno a la adivinanza de contraseñas (global: una sola app)."""

    max_fallos: int = 5
    ventana_s: int = 600
    bloqueo_s: int = 300
    _fallos: list[float] = field(default_factory=list)
    _hasta: float = 0.0

    def bloqueado(self, ahora: float | None = None) -> float:
        """Segundos que faltan para poder intentar de nuevo (0 = libre)."""
        ahora = time.time() if ahora is None else ahora
        return max(0.0, self._hasta - ahora)

    def fallo(self, ahora: float | None = None) -> None:
        ahora = time.time() if ahora is None else ahora
        self._fallos = [t for t in self._fallos if ahora - t <= self.ventana_s] + [ahora]
        if len(self._fallos) >= self.max_fallos:
            self._hasta = ahora + self.bloqueo_s
            self._fallos = []

    def exito(self) -> None:
        self._fallos = []
