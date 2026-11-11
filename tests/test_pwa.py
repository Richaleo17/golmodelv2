"""PWA del celular: candado de la API (contraseña con dos roles), login, y la parte estática (HTML, manifiesto, iconos, service worker).

Sin base de datos: el candado y la PWA estática no la necesitan (las rutas con datos se prueban en test_pwa_navegador.py con un navegador real).
Se verifica que: sin contraseña y en red NADA se sirve; con contraseña los datos exigen token; el lector no puede escribir; el limitador frena la adivinanza; la PWA nunca cachea la API."""
from __future__ import annotations

import json
import re
import struct
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from golmodel import acceso
from golmodel.api import main, seguridad

WEB = Path(__file__).resolve().parents[1] / "web"
ADMIN, LECTOR = "clave-admin-1", "clave-lector-2"


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    for v in ("APP_PASSWORD", "APP_VIEWER_PASSWORD", "APP_SECRET", "UI_BIND"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(seguridad, "limitador", acceso.Limitador())


@pytest.fixture
def cli():
    return TestClient(main.app)


def con_claves(monkeypatch, admin=ADMIN, lector=LECTOR, bind="0.0.0.0"):
    if admin:
        monkeypatch.setenv("APP_PASSWORD", admin)
    if lector:
        monkeypatch.setenv("APP_VIEWER_PASSWORD", lector)
    monkeypatch.setenv("UI_BIND", bind)


def login(cli, clave):
    return cli.post("/auth/login", json={"clave": clave})


def cabecera(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ decisión pura
SEC = acceso.secreto(ADMIN, LECTOR, {})
ENV_LOGIN = {"APP_PASSWORD": ADMIN, "APP_VIEWER_PASSWORD": LECTOR, "UI_BIND": "0.0.0.0"}


@pytest.mark.parametrize("env,metodo,ruta,auth,esperado", [
    ({}, "GET", "/estado", None, (None, "admin")),                                          # sin claves y en local: abierta, como siempre
    ({"UI_BIND": "127.0.0.1"}, "POST", "/actualizar/recalcular", None, (None, "admin")),
    ({"UI_BIND": "0.0.0.0"}, "GET", "/estado", None, (503, None)),                           # en red SIN contraseña: nada
    ({"UI_BIND": "0.0.0.0"}, "GET", "/salud", None, (None, None)),                            # salvo lo público
    ({"APP_VIEWER_PASSWORD": "x", "UI_BIND": "0.0.0.0"}, "GET", "/panel/hoy", None, (503, None)),   # lector sin administrador
    (ENV_LOGIN, "GET", "/panel/hoy", None, (401, None)),                                     # con contraseña: exige token
    (ENV_LOGIN, "GET", "/panel/hoy", "Bearer basura", (401, None)),
    (ENV_LOGIN, "GET", "/panel/hoy", "Basic xxx", (401, None)),
    (ENV_LOGIN, "GET", "/app/", None, (None, None)),                                          # la PWA estática es pública (no trae datos)
    (ENV_LOGIN, "GET", "/app", None, (None, None)),
    (ENV_LOGIN, "GET", "/app/app.js", None, (None, None)),
    (ENV_LOGIN, "GET", "/", None, (None, None)),
    (ENV_LOGIN, "POST", "/auth/login", None, (None, None)),
    (ENV_LOGIN, "GET", "/auth/estado", None, (None, None)),
    (ENV_LOGIN, "GET", "/application", None, (401, None)),                                    # prefijo parecido pero NO público
    (ENV_LOGIN, "GET", "/appx/app.js", None, (401, None)),
    (ENV_LOGIN, "GET", "/docs", None, (401, None)),                                           # ni siquiera la documentación
])
def test_decidir_tabla(env, metodo, ruta, auth, esperado):
    status, _, rol = seguridad.decidir(metodo, ruta, auth, env)
    assert (status, rol) == esperado


def test_decidir_roles_con_token_valido():
    tok_a, tok_l = acceso.crear_token(acceso.ADMIN, SEC), acceso.crear_token(acceso.LECTOR, SEC)
    for metodo in ("GET", "HEAD", "OPTIONS"):
        assert seguridad.decidir(metodo, "/estado", f"Bearer {tok_l}", ENV_LOGIN) == (None, None, "lector")
    for metodo in ("POST", "PUT", "DELETE", "PATCH"):
        status, detalle, rol = seguridad.decidir(metodo, "/actualizar/solo_cuotas", f"Bearer {tok_l}", ENV_LOGIN)
        assert status == 403 and rol == "lector" and "administrador" in detalle                # el lector JAMÁS escribe
        assert seguridad.decidir(metodo, "/actualizar/solo_cuotas", f"Bearer {tok_a}", ENV_LOGIN) == (None, None, "admin")
    assert seguridad.decidir("get", "/estado", f"bearer {tok_a}", ENV_LOGIN)[2] == "admin"       # esquema sin distinguir mayúsculas


def test_token_vencido_manipulado_o_de_otra_llave_no_pasa():
    viejo = acceso.crear_token(acceso.ADMIN, SEC, ahora=time.time() - 40 * 86400)               # emitido hace 40 días (vigencia 30)
    assert seguridad.decidir("GET", "/estado", f"Bearer {viejo}", ENV_LOGIN)[0] == 401
    bueno = acceso.crear_token(acceso.LECTOR, SEC)
    rol, exp, firma = bueno.split(".")
    for falso in (f"admin.{exp}.{firma}", f"{rol}.{int(exp) + 999}.{firma}", f"{rol}.{exp}.{'0' * 32}"):    # subir de rol / alargar vigencia / firma falsa
        assert seguridad.decidir("POST", "/actualizar/recalcular", f"Bearer {falso}", ENV_LOGIN)[0] == 401
    otra = acceso.crear_token(acceso.ADMIN, acceso.secreto("otra", "cosa", {}))
    assert seguridad.decidir("GET", "/estado", f"Bearer {otra}", ENV_LOGIN)[0] == 401
    cambiada = {**ENV_LOGIN, "APP_PASSWORD": "nueva"}                                         # cambiar la contraseña invalida los tokens viejos
    assert seguridad.decidir("GET", "/estado", f"Bearer {acceso.crear_token(acceso.ADMIN, SEC)}", cambiada)[0] == 401


# ------------------------------------------------------------------ login por HTTP
def test_login_entrega_token_con_el_rol_correcto(cli, monkeypatch):
    con_claves(monkeypatch)
    a, l = login(cli, ADMIN), login(cli, LECTOR)
    assert a.status_code == l.status_code == 200
    assert a.json()["rol"] == "admin" and l.json()["rol"] == "lector" and a.json()["dias"] == 30
    assert acceso.validar_token(a.json()["token"], SEC) == "admin" and acceso.validar_token(l.json()["token"], SEC) == "lector"


def test_login_con_clave_incorrecta_es_401_y_no_da_token(cli, monkeypatch):
    con_claves(monkeypatch)
    for mala in ("", "x", ADMIN + " ", ADMIN.upper(), "clave-admin"):
        r = login(cli, mala)
        assert r.status_code == 401 and "token" not in r.json()


def test_el_limitador_frena_la_adivinanza_incluso_con_la_clave_buena(cli, monkeypatch):
    con_claves(monkeypatch)
    for _ in range(5):
        assert login(cli, "mala").status_code == 401
    r = login(cli, ADMIN)                                                                      # bloqueado: ni la buena entra
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    seguridad.limitador = acceso.Limitador()


def test_un_login_exitoso_reinicia_los_fallos(cli, monkeypatch):
    con_claves(monkeypatch)
    for _ in range(4):
        login(cli, "mala")
    assert login(cli, ADMIN).status_code == 200
    for _ in range(4):
        assert login(cli, "mala").status_code == 401                                            # el contador volvió a cero
    assert login(cli, ADMIN).status_code == 200


def test_modo_abierto_no_pide_nada(cli, monkeypatch):
    monkeypatch.setenv("UI_BIND", "127.0.0.1")
    assert cli.get("/auth/estado").json() == {"modo": "abierto"}
    r = login(cli, "")
    assert r.json() == {"modo": "abierto", "rol": "admin", "token": None}
    assert cli.get("/docs").status_code == 200


@pytest.mark.parametrize("claves,modo", [({"admin": None, "lector": None}, "sin_clave"), ({"admin": None, "lector": LECTOR}, "config_invalida")])
def test_en_red_sin_contrasena_o_configuracion_invalida_no_se_sirve_nada(cli, monkeypatch, claves, modo):
    con_claves(monkeypatch, **claves)
    assert cli.get("/auth/estado").json() == {"modo": modo}
    assert login(cli, "lo-que-sea").status_code == 503
    assert cli.get("/docs").status_code == 503 and cli.get("/estado").status_code == 503
    assert cli.get("/salud").status_code == 200 and cli.get("/app/").status_code == 200       # la cáscara estática no tiene datos


def test_la_api_protegida_responde_401_sin_token_y_403_al_lector(cli, monkeypatch):
    con_claves(monkeypatch)
    assert cli.get("/estado").status_code == 401 and cli.get("/panel/hoy").status_code == 401
    assert cli.post("/actualizar/solo_cuotas").status_code == 401
    tok_l = login(cli, LECTOR).json()["token"]
    r = cli.post("/actualizar/solo_cuotas", headers=cabecera(tok_l))
    assert r.status_code == 403 and "administrador" in r.json()["detail"]
    for ruta, cuerpo in (("/cuotas/manual/texto", {"fecha": "hoy", "texto": "a - b 2 3 4"}), ("/auth/login-no-existe", {})):
        assert cli.post(ruta, json=cuerpo, headers=cabecera(tok_l)).status_code in (403, 404)
    assert cli.get("/docs", headers=cabecera(tok_l)).status_code == 200                       # el lector sí consulta
    assert cli.get("/docs", headers=cabecera(login(cli, ADMIN).json()["token"])).status_code == 200


def test_todas_las_rutas_de_datos_estan_detras_del_candado(cli, monkeypatch):
    """Recorre las rutas REALES de la app: las únicas que responden sin token son las públicas declaradas."""
    con_claves(monkeypatch)
    for r in main.app.routes:
        ruta = getattr(r, "path", "")
        if not ruta or ruta.startswith("/app"):
            continue
        for m in getattr(r, "methods", None) or {"GET"}:
            if m in ("HEAD", "OPTIONS"):
                continue
            concreta = re.sub(r"\{[^}]+\}", "1", ruta)
            resp = cli.request(m, concreta, json={} if m != "GET" else None)
            if seguridad.es_publica(ruta):
                assert resp.status_code != 401, (m, ruta)
            else:
                assert resp.status_code == 401, (m, ruta, resp.status_code)                      # sin token: nada de datos


# ------------------------------------------------------------------ PWA estática
def test_la_raiz_lleva_a_la_pwa_y_la_pwa_se_sirve(cli):
    r = cli.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
    h = cli.get("/app/")
    assert h.status_code == 200 and "text/html" in h.headers["content-type"]
    assert "<title>GolModel</title>" in h.text and 'name="viewport"' in h.text and "viewport-fit=cover" in h.text
    assert 'rel="manifest"' in h.text and 'rel="apple-touch-icon"' in h.text and 'name="theme-color"' in h.text
    assert [m for m in re.findall(r'data-v="(\w+)"', h.text)] == ["diario", "meter", "sonadoras", "nfl", "cuotas", "datos"]      # el menú inferior que se pidió (+ NFL, 2026-11-11)
    for recurso in ("app.css", "app.js", "sw.js", "icon.svg", "manifest.webmanifest", "icon-192.png", "icon-512.png", "icon-180.png"):
        assert cli.get(f"/app/{recurso}").status_code == 200, recurso


def test_el_manifiesto_es_instalable():
    m = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m["display"] == "standalone" and m["start_url"] == "/app/" and m["scope"] == "/app/" and m["name"] == "GolModel"
    assert m["background_color"] == m["theme_color"] == "#070816"
    por_tam = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= por_tam
    for i in m["icons"]:
        assert (WEB / i["src"]).is_file(), i["src"]
        if i["src"].endswith(".png"):
            ancho, alto = struct.unpack(">II", (WEB / i["src"]).read_bytes()[16:24])                  # cabecera PNG: dimensiones reales
            assert f"{ancho}x{alto}" == i["sizes"], i["src"]
    ancho, alto = struct.unpack(">II", (WEB / "icon-180.png").read_bytes()[16:24])
    assert (ancho, alto) == (180, 180)


def test_el_service_worker_solo_toca_la_cascara_y_nunca_la_api():
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    assert 'startsWith("/app/")' in sw and 'method !== "GET"' in sw and "origin !== location.origin" in sw       # todo lo demás pasa directo a la red
    cascara = re.search(r"const CASCARA = \[(.*?)\]", sw, re.S).group(1)
    for ruta_api in ("panel", "estado", "parlays", "sonadoras", "tickets", "auth", "cuotas", "actualizar", "clv"):
        assert ruta_api not in cascara                                                          # ninguna ruta de datos en la caché
    for archivo in re.findall(r'"([^"]+)"', cascara):
        assert archivo == "./" or (WEB / archivo).is_file(), archivo


def test_el_js_no_usa_apis_peligrosas_y_cada_ruta_que_llama_existe_en_la_api():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"\beval\(|document\.write|new Function\(", js)
    rutas = [(frozenset(app.methods), app.path) for app in main.app.routes if getattr(app, "methods", None)]
    llamadas = re.findall(r'api\(\s*"(GET|POST)"\s*,\s*"(/[a-z/_]*)', js)
    assert len(llamadas) >= 10
    for metodo, prefijo in llamadas:
        raiz = prefijo.rstrip("/")
        assert any(metodo in ms and (p == raiz or p.startswith(raiz + "/")) for ms, p in rutas), (metodo, prefijo)    # contrato PWA ↔ API


def test_el_html_no_trae_scripts_en_linea_ni_manejadores_inline():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\son[a-z]+=", html)


# ------------------------------------------------------------------ la pantalla completa (Streamlit) con la API ya protegida
from tests.test_integracion import cargado, cfg, dsn, mundo  # noqa: E402,F401
from tests.test_integracion_benchmark import bd  # noqa: E402,F401
from tests.test_integracion_candidatas import _dia, poblada  # noqa: E402,F401

try:
    from streamlit.testing.v1 import AppTest
    from tests.test_ui_smoke import APP, _ir_a_panel, _textos, entorno  # noqa: E402,F401
except Exception:                                    # pragma: no cover
    AppTest = None


@pytest.mark.skipif(AppTest is None, reason="streamlit no disponible")
@pytest.mark.parametrize("rol", ["admin", "lector"])
def test_la_ui_de_streamlit_funciona_con_la_api_protegida_enviando_su_token(entorno, bd, monkeypatch, rol):  # noqa: F811
    """Regresión del 401 en 8501: con APP_PASSWORD puesto, la API exige token y la UI debe presentarse con el de su rol."""
    from golmodel import db
    from golmodel.integracion import benchmark as bm
    from tests.test_integracion_benchmark import _ticket_con_partidos_reales
    monkeypatch.setenv("APP_PASSWORD", ADMIN)
    monkeypatch.setenv("APP_VIEWER_PASSWORD", LECTOR)
    monkeypatch.setenv("UI_BIND", "0.0.0.0")
    with db.conectar(bd) as c:
        bm.persistir(c, _ticket_con_partidos_reales(c, _dia(c)))
    token = acceso.crear_token(rol, acceso.secreto(ADMIN, LECTOR, {}))            # el enlace «recordado» de la pantalla de acceso
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["s"] = token
    at = _ir_a_panel(at)
    assert not at.exception, [e.value for e in at.exception]
    textos = _textos(at)
    assert "401" not in textos and "No autenticado" not in textos and "La API respondió con un error" not in textos
    assert any(m.value.startswith('<div class="parlay"><h4>🎫 Ticket V3') for m in at.markdown)         # los datos SÍ llegaron a la pantalla
