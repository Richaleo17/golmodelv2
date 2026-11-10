"""PWA en un Chromium MÓVIL real (Playwright, vía scripts/pwa_e2e.js): login con roles, las cinco pantallas, rol lector, escape de HTML y banner Beta.

* modo `mock`: datos fijos (incluye HTML malicioso en nombres) sobre la API real sin base de datos → prueba la lógica de pantalla y el candado.
* modo `real`: la API real sobre PostgreSQL con el mundo sintético y un ticket V3 con sello BETA persistido → prueba la cadena completa (BD → vistas → API → PWA).
Se omiten solos si no hay Node/Playwright/Chromium."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

NODE = shutil.which("node")
RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "scripts" / "pwa_e2e.js"
ADMIN, LECTOR = "clave-admin-1", "clave-lector-2"


def _hay_playwright() -> bool:
    if not NODE:
        return False
    r = subprocess.run([NODE, "-e", "const {chromium}=require('playwright');process.exit(require('fs').existsSync(chromium.executablePath())?0:1)"],
                       capture_output=True, timeout=60)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _hay_playwright(), reason="Node/Playwright/Chromium no disponibles")

from tests.test_integracion import cargado, cfg, dsn, mundo  # noqa: E402,F401
from tests.test_integracion_benchmark import bd  # noqa: E402,F401
from tests.test_integracion_candidatas import _dia, poblada  # noqa: E402,F401


def _puerto_libre() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


class Servidor:
    def __init__(self, app, puerto):
        import uvicorn
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="warning"))
        self.hilo = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.hilo.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.1)
        raise RuntimeError("el servidor de prueba no arrancó")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.hilo.join(timeout=10)


def _correr(url, modo, salida, dia="", beta=""):
    env = {**os.environ, "NODE_PATH": subprocess.run([NODE, "-e", "console.log(require.resolve('playwright').split('node_modules')[0]+'node_modules')"],
                                                     capture_output=True, text=True).stdout.strip()}
    r = subprocess.run([NODE, str(SCRIPT), url, modo, str(salida), ADMIN, LECTOR, dia, beta], capture_output=True, text=True, timeout=240, env=env)
    return r.returncode, r.stdout + r.stderr


@pytest.fixture
def claves(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", ADMIN)
    monkeypatch.setenv("APP_VIEWER_PASSWORD", LECTOR)
    monkeypatch.setenv("UI_BIND", "0.0.0.0")
    from golmodel import acceso
    from golmodel.api import seguridad
    monkeypatch.setattr(seguridad, "limitador", acceso.Limitador())


def test_pwa_en_navegador_movil_con_datos_simulados(claves, tmp_path):
    from golmodel.api import main
    free_tcp_port = _puerto_libre()
    with Servidor(main.app, free_tcp_port):
        rc, salida = _correr(f"http://127.0.0.1:{free_tcp_port}", "mock", tmp_path)
    assert rc == 0, salida
    assert "TODO OK" in salida and "✗" not in salida
    assert {p.name for p in tmp_path.glob("*.png")} >= {"00-login.png", "01-diario.png", "02-meter.png", "03-sonadoras.png", "04-cuotas.png", "05-datos.png", "06-lector.png"}


def test_pwa_en_navegador_movil_con_la_api_real_y_el_banner_beta(claves, tmp_path, bd, cfg, mundo):  # noqa: F811
    free_tcp_port = _puerto_libre()
    from datetime import timedelta
    import pandas as pd
    from golmodel import db
    from golmodel.api import main
    from golmodel.integracion import benchmark as bm
    from tests.test_integracion import JORNADA, cadenas
    from tests.test_modo_beta import _cfg_beta, _decision
    momento = pd.Timestamp(JORNADA).to_pydatetime() + timedelta(hours=5)
    main._servicio = main.Servicio(cfg, cadenas(mundo, cfg), dsn=bd, reloj=lambda: momento)
    try:
        with db.conectar(bd) as c:
            dia = _dia(c)
            bm.persistir(c, _decision(c, dia, _cfg_beta()))
        with Servidor(main.app, free_tcp_port):
            rc, salida = _correr(f"http://127.0.0.1:{free_tcp_port}", "real", tmp_path, dia.isoformat(), "beta")
    finally:
        main._servicio = None
    assert rc == 0, salida
    assert "real: el banner Beta rojo viene de la API real" in salida and "✗" not in salida
