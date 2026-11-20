"""Acceso por persona (2026-11-20): cada quien entra con su código, se registra quién entra y se puede bloquear a una persona
sin tocar a las demás.

* El administrador (APP_PASSWORD) crea personas: recibe un código de un solo uso para entregarlo (se muestra UNA vez; en la base queda
  solo su huella SHA-256). La persona entra con ese código como «lector» (solo consulta); su token lleva su id.
* Bloquear es inmediato: cada petición revisa (con caché de segundos) que la persona siga activa. Un token de alguien bloqueado no sirve.
* Registro (`accesos_app`): login, intentos fallidos, bloqueos y un latido «sesion» como máximo cada 10 min por persona (con ruta, IP y dispositivo),
  para ver quién y cuándo sin llenar la base. Registrar NUNCA rompe una petición: si la base falla, la petición sigue.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time

import psycopg

log = logging.getLogger("golmodel.usuarios")

ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # sin 0/O, 1/I/L: se dicta y se teclea sin confusiones
LARGO_CODIGO = 12                                    # ≈ 59 bits: no se adivina ni se fuerza


def generar_codigo() -> str:
    c = "".join(secrets.choice(ALFABETO) for _ in range(LARGO_CODIGO))
    return "-".join(c[i:i + 4] for i in range(0, LARGO_CODIGO, 4))


def normalizar(codigo: str) -> str:
    return re.sub(r"[\s-]", "", str(codigo or "")).upper()


def huella(codigo: str) -> str:
    return hashlib.sha256(("golmodelv2|codigo|" + normalizar(codigo)).encode()).hexdigest()


def dispositivo_corto(ua: str | None) -> str:
    u = (ua or "").lower()
    if not u:
        return "?"
    so = next((n for k, n in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"), ("windows", "Windows"),
                              ("macintosh", "Mac"), ("linux", "Linux")) if k in u), "otro")
    nav = next((n for k, n in (("edg/", "Edge"), ("chrome", "Chrome"), ("safari", "Safari"), ("firefox", "Firefox")) if k in u), "")
    return f"{so} {nav}".strip()


# ---------------------------------------------------------------------------------------------------------------- base de datos
def crear(conn, nombre: str, nota: str | None = None) -> dict:
    """Crea la persona y devuelve su código en claro (única vez). ValueError si el nombre no sirve o ya existe."""
    nombre = " ".join(str(nombre or "").split())
    if not 2 <= len(nombre) <= 60:
        raise ValueError("El nombre debe tener entre 2 y 60 caracteres.")
    codigo = generar_codigo()
    try:
        with conn.transaction():
            r = conn.execute("INSERT INTO usuarios_app (nombre, codigo_hash, nota) VALUES (%s, %s, %s) RETURNING id",
                             (nombre, huella(codigo), (nota or "").strip()[:200] or None)).fetchone()
    except psycopg.errors.UniqueViolation:
        raise ValueError("Ya existe una persona con ese nombre.") from None
    return {"id": r["id"], "nombre": nombre, "codigo": codigo}


def entrar(conn, codigo: str) -> dict | None:
    """La persona dueña del código (aunque esté bloqueada: quien llama decide) o None."""
    if len(normalizar(codigo)) != LARGO_CODIGO:
        return None
    return conn.execute("SELECT id, nombre, bloqueado FROM usuarios_app WHERE codigo_hash = %s", (huella(codigo),)).fetchone()


def listar(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT u.id, u.nombre, u.nota, u.bloqueado, u.creado_en, u.bloqueado_en, u.ultimo_acceso,
               (SELECT count(*) FROM accesos_app a WHERE a.usuario_id = u.id AND a.evento IN ('login', 'sesion')
                  AND a.ts > now() - interval '7 days') AS accesos_7d
        FROM usuarios_app u ORDER BY u.bloqueado, u.ultimo_acceso DESC NULLS LAST, u.nombre""").fetchall()]


def bloquear(conn, uid: int, valor: bool) -> dict | None:
    return conn.execute("""UPDATE usuarios_app SET bloqueado = %s, bloqueado_en = CASE WHEN %s THEN now() ELSE NULL END
                           WHERE id = %s RETURNING id, nombre, bloqueado""", (bool(valor), bool(valor), uid)).fetchone()


def registrar(conn, quien: str, evento: str, uid: int | None = None, ruta: str | None = None,
              ip: str | None = None, dispositivo: str | None = None) -> None:
    conn.execute("INSERT INTO accesos_app (usuario_id, quien, evento, ruta, ip, dispositivo) VALUES (%s, %s, %s, %s, %s, %s)",
                 (uid, quien[:80], evento, (ruta or "")[:120] or None, (ip or "")[:64] or None, (dispositivo or "")[:80] or None))
    if uid is not None and evento in ("login", "sesion"):
        conn.execute("UPDATE usuarios_app SET ultimo_acceso = now() WHERE id = %s", (uid,))


def recientes(conn, limite: int = 100, uid: int | None = None) -> list[dict]:
    limite = max(1, min(int(limite), 500))
    return [dict(r) for r in conn.execute(
        """SELECT ts, quien, evento, ruta, ip, dispositivo, usuario_id FROM accesos_app
           WHERE (%s::int IS NULL OR usuario_id = %s) ORDER BY ts DESC, id DESC LIMIT %s""", (uid, uid, limite)).fetchall()]


# ---------------------------------------------------------------------------------------------------------------- por petición
class Control:
    """Estado en memoria del proceso: caché de «¿sigue activa esta persona?» y freno del registro de latidos."""

    def __init__(self, dsn: str | None, ttl: float = 15.0, latido_s: float = 600.0, reloj=time.time):
        self.dsn, self.ttl, self.latido_s, self.reloj = dsn, ttl, latido_s, reloj
        self._estado: dict[int, tuple[float, bool, str]] = {}
        self._ultimo: dict[str, float] = {}

    def _conectar(self):
        from golmodel import db
        return db.conectar(self.dsn)

    def invalidar(self, uid: int | None = None) -> None:
        if uid is None:
            self._estado.clear()
        else:
            self._estado.pop(uid, None)

    def activa(self, uid: int) -> tuple[bool, str]:
        """(¿puede entrar?, nombre). Si la base no responde se usa lo último conocido o se deja pasar (el registro no debe tumbar la app)."""
        ahora = self.reloj()
        c = self._estado.get(uid)
        if c and ahora - c[0] < self.ttl:
            return c[1], c[2]
        try:
            conn = self._conectar()
            try:
                r = conn.execute("SELECT nombre, bloqueado FROM usuarios_app WHERE id = %s", (uid,)).fetchone()
            finally:
                conn.close()
        except Exception:                                                        # noqa: BLE001
            log.warning("acceso: no se pudo revisar a la persona %s", uid, exc_info=True)
            return (c[1], c[2]) if c else (True, "?")
        ok, nombre = (r is not None and not r["bloqueado"]), (r["nombre"] if r else "?")
        self._estado[uid] = (ahora, ok, nombre)
        return ok, nombre

    def anotar(self, quien: str, evento: str, uid: int | None = None, ruta: str | None = None,
               ip: str | None = None, dispositivo: str | None = None, cada: float | None = None) -> None:
        """Guarda un acceso. Con `cada` (segundos) se omite si esa misma persona/dispositivo ya quedó anotada hace menos."""
        clave = f"{evento}|{quien}|{dispositivo}|{ip}"
        ahora = self.reloj()
        if cada and ahora - self._ultimo.get(clave, 0.0) < cada:
            return
        try:
            conn = self._conectar()
            try:
                registrar(conn, quien, evento, uid, ruta, ip, dispositivo)
                conn.commit()
            finally:
                conn.close()
            self._ultimo[clave] = ahora
        except Exception:                                                        # noqa: BLE001
            log.warning("acceso: no se pudo registrar %s de %s", evento, quien, exc_info=True)

    def latido(self, quien: str, uid: int | None, ruta: str, ip: str | None, dispositivo: str | None) -> None:
        self.anotar(quien, "sesion", uid, ruta, ip, dispositivo, cada=self.latido_s)
