"""Protección de la API para el acceso desde el celular (PWA): contraseña con DOS ROLES, reutilizando `golmodel.acceso`.

* Modo `abierto` (sin contraseñas y escuchando solo en la computadora local, `UI_BIND=127.0.0.1`): la API funciona como siempre (desarrollo, tests).
* Modo `login` (hay `APP_PASSWORD`): toda ruta que no sea pública exige `Authorization: Bearer <token>`; el token lo emite `POST /auth/login`. El rol `lector` solo puede
  consultar (GET/HEAD/OPTIONS): cualquier otro método devuelve 403, aunque alguien manipule la pantalla.
* Modo `sin_clave` (abierta a la red SIN contraseña) y `config_invalida` (contraseña de lector sin contraseña de administrador): nada se sirve (503). Fail-closed.

Públicas: `/salud`, `/auth/*` y la PWA estática (`/app/...`, que no contiene datos). Los datos siempre pasan por el candado.
"""
from __future__ import annotations

from collections.abc import Mapping

from golmodel import acceso

PUBLICAS = {"/", "/salud", "/auth/login", "/auth/estado"}
MENSAJE_MODO = {
    "sin_clave": "La app está abierta a la red pero no tiene contraseña: define APP_PASSWORD en .env y reinicia (docker compose up -d).",
    "config_invalida": "Hay APP_VIEWER_PASSWORD pero falta APP_PASSWORD: define también la contraseña de administrador.",
}
METODOS_LECTURA = {"GET", "HEAD", "OPTIONS"}


def es_publica(ruta: str) -> bool:
    return ruta in PUBLICAS or ruta == "/app" or ruta.startswith("/app/")


def modo(env: Mapping[str, str]) -> str:
    admin, lector = acceso.claves_desde_env(env)
    return acceso.modo_acceso(admin, lector, env.get("UI_BIND", "127.0.0.1"))


def decidir(metodo: str, ruta: str, authorization: str | None, env: Mapping[str, str]) -> tuple[int | None, str | None, str | None]:
    """(status_de_error | None, detalle | None, rol | None). Función pura: el middleware solo la aplica."""
    m = modo(env)
    if es_publica(ruta):
        return None, None, None
    if m == "abierto":
        return None, None, acceso.ADMIN
    if m != "login":
        return 503, MENSAJE_MODO[m], None
    admin, lector = acceso.claves_desde_env(env)
    token = authorization[7:].strip() if authorization and authorization[:7].lower() == "bearer " else None
    rol = acceso.validar_token(token, acceso.secreto(admin, lector, env))
    if rol is None:
        return 401, "No autenticado: inicia sesión.", None
    if rol != acceso.ADMIN and metodo.upper() not in METODOS_LECTURA:
        return 403, "Solo el administrador puede hacer esto.", rol
    return None, None, rol


limitador = acceso.Limitador()
