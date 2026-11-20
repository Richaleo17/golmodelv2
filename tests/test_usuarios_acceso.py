"""Acceso por persona: tokens con identidad, códigos, bloqueo inmediato, registro de accesos y rutas de administrador."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from golmodel import acceso, usuarios
from golmodel.api import main, seguridad

ADMIN, LECTOR = "clave-admin-1", "clave-lector-2"
SEC = acceso.secreto(ADMIN, LECTOR, {})


# ------------------------------------------------------------------ puro
def test_token_con_identidad_ida_y_vuelta_y_compatibilidad():
    t = acceso.crear_token(acceso.LECTOR, SEC, ahora=1000.0, uid=7)
    assert len(t.split(".")) == 4 and acceso.leer_token(t, SEC, ahora=1001.0) == ("lector", 7)
    assert acceso.validar_token(t, SEC, ahora=1001.0) == "lector"
    viejo = acceso.crear_token(acceso.LECTOR, SEC, ahora=1000.0)                       # formato anterior: sigue valiendo, sin persona
    assert len(viejo.split(".")) == 3 and acceso.leer_token(viejo, SEC, ahora=1001.0) == ("lector", None)
    rol, exp, uid, firma = t.split(".")
    assert acceso.leer_token(f"{rol}.{exp}.8.{firma}", SEC, ahora=1001.0) is None      # cambiar de persona no funciona
    assert acceso.leer_token(f"admin.{exp}.{uid}.{firma}", SEC, ahora=1001.0) is None  # ni subir de rol
    assert acceso.leer_token(t, SEC, ahora=1000.0 + 31 * 86400) is None                # vencido
    assert acceso.leer_token(f"{rol}.{exp}.x.{firma}", SEC) is None and acceso.leer_token("a.b.c.d.e", SEC) is None
    assert acceso.crear_token(acceso.ADMIN, SEC, ahora=1000.0).count(".") == 2          # el admin nunca lleva uid


def test_codigos_legibles_y_huella_estable():
    c = usuarios.generar_codigo()
    assert len(c) == 14 and c.count("-") == 2 and not set(c.replace("-", "")) - set(usuarios.ALFABETO)
    assert usuarios.generar_codigo() != c
    assert usuarios.huella(c) == usuarios.huella(c.lower().replace("-", " "))           # se puede teclear sin guiones ni mayúsculas
    assert usuarios.huella(c) != usuarios.huella(usuarios.generar_codigo())
    assert usuarios.dispositivo_corto("Mozilla/5.0 (iPhone; CPU iPhone OS 17) AppleWebKit Safari/604.1") == "iPhone Safari"
    assert usuarios.dispositivo_corto(None) == "?"


def test_rutas_admin_solo_para_administrador():
    env = {"APP_PASSWORD": ADMIN, "APP_VIEWER_PASSWORD": LECTOR, "UI_BIND": "0.0.0.0"}
    lec = "Bearer " + acceso.crear_token(acceso.LECTOR, SEC)
    adm = "Bearer " + acceso.crear_token(acceso.ADMIN, SEC)
    assert seguridad.decidir("GET", "/admin/usuarios", lec, env)[0] == 403                # ni siquiera consultar
    assert seguridad.decidir("GET", "/admin/accesos", adm, env)[0] is None
    assert seguridad.decidir("GET", "/estado", lec, env)[0] is None                       # el resto de la consulta sigue igual
    assert seguridad.identidad(lec, env) == ("lector", None)
    assert seguridad.identidad("Bearer basura", env) is None and seguridad.identidad(None, env) is None


def test_control_cachea_y_frena_latidos():
    t = [1000.0]
    ctl = usuarios.Control(None, ttl=15, latido_s=600, reloj=lambda: t[0])
    llamadas = []
    ctl._conectar = lambda: (_ for _ in ()).throw(RuntimeError("sin base"))            # la base caída no rompe nada
    assert ctl.activa(5) == (True, "?")                                                  # sin dato: se deja pasar
    ctl.latido("Ana", 5, "/estado", "1.1.1.1", "iPhone")                                 # no lanza aunque falle
    assert llamadas == []


# ------------------------------------------------------------------ con base de datos
@pytest.fixture()
def api_bd(monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        adm = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_usr_{uuid.uuid4().hex[:8]}"
    adm.execute(f"CREATE DATABASE {nombre}")
    dsn = admin_dsn.replace("/postgres?", f"/{nombre}?")
    raiz = Path(__file__).resolve().parents[1]
    c = psycopg.connect(dsn, row_factory=dict_row)
    c.execute((raiz / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    c.execute((raiz / "db/seed.sql").read_text())
    from golmodel import migraciones
    migraciones.aplicar(c)
    c.commit()
    for v in ("APP_PASSWORD", "APP_VIEWER_PASSWORD", "APP_SECRET", "UI_BIND"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("APP_PASSWORD", ADMIN)
    monkeypatch.setenv("APP_VIEWER_PASSWORD", LECTOR)
    monkeypatch.setenv("UI_BIND", "0.0.0.0")
    monkeypatch.setattr(seguridad, "limitador", acceso.Limitador())
    serv = main.Servicio(cadenas={}, dsn=dsn)
    monkeypatch.setattr(main, "_servicio", serv)
    monkeypatch.setattr(main, "_control", None)
    yield TestClient(main.app), c
    c.close()
    adm.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    adm.close()


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_flujo_completo_crear_entrar_bloquear_reactivar(api_bd):
    cli, c = api_bd
    adm = cli.post("/auth/login", json={"clave": ADMIN}).json()["token"]
    r = cli.post("/admin/usuarios", json={"nombre": "Luis"}, headers=_h(adm))
    assert r.status_code == 200
    luis, codigo = r.json()["id"], r.json()["codigo"]
    assert cli.post("/admin/usuarios", json={"nombre": "luis"}, headers=_h(adm)).status_code == 422          # nombre repetido
    guardado = c.execute("SELECT codigo_hash FROM usuarios_app WHERE id = %s", (luis,)).fetchone()["codigo_hash"]
    assert codigo.replace("-", "") not in guardado and guardado == usuarios.huella(codigo)                  # solo la huella

    l = cli.post("/auth/login", json={"clave": codigo.lower()})                                             # entra con su código (sin mayúsculas)
    assert l.status_code == 200 and l.json()["rol"] == "lector" and l.json()["nombre"] == "Luis"
    tok = l.json()["token"]
    assert cli.get("/salud").status_code == 200
    assert cli.get("/admin/usuarios", headers=_h(tok)).status_code == 403                                   # no es administrador
    assert cli.post("/admin/usuarios", json={"nombre": "X"}, headers=_h(tok)).status_code == 403
    assert cli.get("/estado", headers=_h(tok)).status_code != 401                                           # sí puede consultar

    assert cli.post(f"/admin/usuarios/{luis}/bloquear", json={"bloqueado": True}, headers=_h(adm)).status_code == 200
    b = cli.get("/estado", headers=_h(tok))                                                                 # su token ya no sirve, al instante
    assert b.status_code == 403 and "bloqueado" in b.json()["detail"]
    assert cli.post("/auth/login", json={"clave": codigo}).status_code == 403                               # ni entrar de nuevo
    assert cli.get("/estado", headers=_h(adm)).status_code != 403                                           # el resto de la gente sigue igual

    assert cli.post(f"/admin/usuarios/{luis}/bloquear", json={"bloqueado": False}, headers=_h(adm)).status_code == 200
    assert cli.get("/estado", headers=_h(tok)).status_code != 403                                           # reactivado
    assert cli.post("/admin/usuarios/9999/bloquear", json={"bloqueado": True}, headers=_h(adm)).status_code == 404


def test_registro_de_accesos_y_fallidos(api_bd):
    cli, c = api_bd
    assert cli.post("/auth/login", json={"clave": "mala"}).status_code == 401
    assert cli.post("/auth/login", json={"clave": "ABCD-EFGH-JKMN"}).status_code == 401                      # código con formato pero inexistente
    adm = cli.post("/auth/login", json={"clave": ADMIN}, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10) Chrome/120", "X-Forwarded-For": "8.8.8.8, 10.0.0.1"}).json()["token"]
    cli.get("/estado", headers={**_h(adm), "User-Agent": "Mozilla/5.0 (Windows NT 10) Chrome/120", "X-Forwarded-For": "8.8.8.8"})
    cli.get("/estado", headers={**_h(adm), "User-Agent": "Mozilla/5.0 (Windows NT 10) Chrome/120", "X-Forwarded-For": "8.8.8.8"})
    filas = cli.get("/admin/accesos?limite=50", headers=_h(adm)).json()["accesos"]
    ev = [(f["quien"], f["evento"]) for f in filas]
    assert ev.count(("?", "login_fallido")) == 2 and ("admin", "login") in ev
    assert ev.count(("admin", "sesion")) <= 2                                                                # el latido no inunda: 1 por 10 min por dispositivo
    ent = next(f for f in filas if f["evento"] == "login" and f["quien"] == "admin")
    assert ent["ip"] == "8.8.8.8" and ent["dispositivo"] == "Windows Chrome"                                 # IP real detrás del proxy

    r = cli.post("/admin/usuarios", json={"nombre": "Marta", "nota": "prima"}, headers=_h(adm)).json()
    tok = cli.post("/auth/login", json={"clave": r["codigo"]}).json()["token"]
    cli.get("/estado", headers=_h(tok))
    lista = cli.get("/admin/usuarios", headers=_h(adm)).json()["usuarios"]
    m = next(u for u in lista if u["nombre"] == "Marta")
    assert m["ultimo_acceso"] is not None and m["accesos_7d"] >= 1 and m["bloqueado"] is False and m["nota"] == "prima"
