"""Demostración sin llaves de API: mundo simulado -> base de datos -> pipeline -> API.

    python scripts/demo_simulado.py --admin-dsn "postgresql://postgres@/postgres?host=/var/tmp&port=54329"
    API_URL=http://localhost:8000 GOLMODEL_FECHA=<fecha impresa> streamlit run ui/app.py

ADVERTENCIA: liga y cuotas SIMULADAS (parámetros conocidos). No usar para apostar.
"""
import argparse
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import psycopg
import uvicorn

RAIZ = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RAIZ / "src"), str(RAIZ / "tests"), str(RAIZ)]

import test_integracion as mundo_sim  # noqa: E402
from golmodel import db  # noqa: E402
from golmodel.api import main  # noqa: E402
from golmodel.ingesta.pipeline import Pipeline, cargar_historico  # noqa: E402


def preparar(admin_dsn: str, nombre: str) -> str:
    admin = psycopg.connect(admin_dsn, autocommit=True)
    admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn = admin_dsn.replace("/postgres?", f"/{nombre}?")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((RAIZ / "db/schema.sql").read_text())
        c.execute("SET search_path TO golmodel, public")
        c.execute((RAIZ / "db/seed.sql").read_text())
    return dsn


def demo_multiliga(dsn: str, puerto: int) -> None:
    from tests import mundo_multiliga as mm
    mundo = mm.Mundo()
    cfg = mm.configuracion(mundo_sim.cargar())
    conn = db.conectar(dsn)
    for liga in mm.LIGAS:
        cargar_historico(conn, liga, mundo.hist[liga], mundo.cuotas_prepartido(liga))
    conn.commit()
    conn.close()
    Pipeline(cfg, mm.cadenas(mundo), dsn=dsn, reloj=lambda: mundo.ahora).ejecutar("actualizar_todo")
    fecha = mundo.jornada.tz_convert("America/Mexico_City").date()
    print(f"Base de datos lista: {dsn}\nFecha de la jornada simulada (GOLMODEL_FECHA): {fecha}", flush=True)
    main._servicio = main.Servicio(cfg, mm.cadenas(mundo), dsn=dsn, reloj=lambda: mundo.ahora + timedelta(minutes=10))
    uvicorn.run(main.app, host="127.0.0.1", port=puerto, log_level="warning")


def main_() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-dsn", required=True)
    ap.add_argument("--db", default="golmodel_demo")
    ap.add_argument("--puerto", type=int, default=8000)
    ap.add_argument("--multiliga", action="store_true", help="5 ligas simuladas (Premier, La Liga, Liga MX, MLS, Bundesliga)")
    a = ap.parse_args()
    dsn = preparar(a.admin_dsn, a.db)
    if a.multiliga:
        return demo_multiliga(dsn, a.puerto)
    cfg = mundo_sim.cargar()
    cfg["ligas_activas"] = ["EPL"]
    cfg["mercado"]["casa_ejecucion"] = "bet365"
    cfg["modelo"]["rejilla_calibracion"] = {"xi": [0.002, 0.005], "w_xg": [0.0, 0.5]}
    conn = db.conectar(dsn)
    pre = [{"local": r.local, "visitante": r.visitante, "inicio": r.inicio, "casa": "pinnacle", "tipo": "prepartido",
            "capturada_en": r.inicio - pd.Timedelta(days=2), "seleccion": s, "cuota": c}
           for r in mundo_sim.HIST.itertuples() for s, c in mundo_sim.cuotas_ref(r, margen=0.04).items()]
    cargar_historico(conn, "EPL", mundo_sim.HIST, pd.DataFrame(pre))
    conn.commit()
    conn.close()
    m = mundo_sim.Mundo()
    momento = mundo_sim.AHORA + timedelta(minutes=10)
    m.ahora = mundo_sim.AHORA
    Pipeline(cfg, mundo_sim.cadenas(m, cfg), dsn=dsn, reloj=lambda: mundo_sim.AHORA).ejecutar("actualizar_todo")
    fecha = pd.Timestamp(mundo_sim.JORNADA).tz_convert("America/Mexico_City").date()
    print(f"Base de datos lista: {dsn}\nFecha de la jornada simulada (GOLMODEL_FECHA): {fecha}", flush=True)
    main._servicio = main.Servicio(cfg, mundo_sim.cadenas(m, cfg), dsn=dsn, reloj=lambda: momento)
    uvicorn.run(main.app, host="127.0.0.1", port=a.puerto, log_level="warning")


if __name__ == "__main__":
    main_()
