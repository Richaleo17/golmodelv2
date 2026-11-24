"""Carga inicial del histórico real: CSV de football-data (resultados + cuotas pre-partido y de
cierre) y xG de Understat (EPL y Serie A). Los nombres de football-data son los canónicos.

    DATABASE_URL=postgresql://golmodel:...@localhost:5432/golmodel \\
    python scripts/cargar_historico.py --temporadas 5

Es idempotente: volver a correrlo no duplica partidos ni cuotas.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from golmodel import db, repositorio as repo  # noqa: E402
from golmodel.config import cargar  # noqa: E402
from golmodel.ingesta import estadisticas  # noqa: E402
from golmodel.ingesta.historico_api import cargar_historico_api, necesita_historico_api  # noqa: E402
from golmodel.ingesta.limpieza import limpiar  # noqa: E402
from golmodel.ingesta.normalizacion import ResolverEquipos  # noqa: E402
from golmodel.ingesta.proveedores.base import ErrorProveedor  # noqa: E402
from golmodel.ingesta.pipeline import cargar_historico  # noqa: E402
from golmodel.ingesta.proveedores.football_data_csv import CODIGOS, FootballDataCsv, ruta_csv  # noqa: E402
from golmodel.ingesta.proveedores.understat import Understat  # noqa: E402
from golmodel.ingesta.tipos import Capacidad, Solicitud  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--temporadas", type=int, default=None, help="por defecto: config historico.temporadas")
    ap.add_argument("--ligas", nargs="*", default=None)
    ap.add_argument("--sin-xg", action="store_true", help="no descargar xG de Understat")
    ap.add_argument("--solo-xg", action="store_true", help="solo xG (los CSV ya están cargados)")
    a = ap.parse_args()
    cfg = cargar()
    n_temp = a.temporadas or cfg["historico"]["temporadas"]
    ligas = a.ligas or cfg["ligas_activas"]
    hoy = datetime.now(timezone.utc)
    anio_actual = hoy.year if hoy.month >= 7 else hoy.year - 1
    reg = repo.registrador_api()
    fd = FootballDataCsv(registrar=reg)
    und = Understat(pausa_s=cfg["fuentes"]["understat"]["min_segundos_entre_requests"], registrar=reg)
    conn = db.conectar()
    fallos: list[str] = []
    cadena_api = None
    for codigo in ligas:
        liga = repo.ligas(conn)[codigo]
        if necesita_historico_api(codigo):              # sin CSV de football-data (Liga MX Femenil)
            if cadena_api is None:
                import os

                from golmodel.ingesta.proveedores import registro
                provs = registro.construir_proveedores(cfg, dict(os.environ), registrar=reg)
                cadena_api = registro.construir_cadenas(cfg, provs, registro.construir_presupuestos(cfg))[
                    Capacidad.RESULTADOS]
            anios = list(range(anio_actual - n_temp, anio_actual + 1))
            try:
                r = cargar_historico_api(conn, cadena_api, codigo, anios)
                conn.commit()
            except Exception as e:                           # noqa: BLE001
                conn.rollback()
                fallos.append(f"{codigo} (APIs): {type(e).__name__}: {e}")
                continue
            for y, txt in r["temporadas"].items():
                print(f"{codigo} {y}-{y + 1}: {txt}")
            for aviso in dict.fromkeys(r["avisos"]):
                print(f"  ⚠ {codigo}: {aviso}")
            continue
        cod_fd, formato = CODIGOS[codigo]
        anios = list(range(anio_actual - n_temp, anio_actual + 1))
        rutas = [ruta_csv(codigo, None)] if formato == "extra" else [ruta_csv(codigo, y) for y in anios]
        for ruta in ([] if a.solo_xg else rutas):
            try:
                texto = fd.http.get(ruta, costo=0).text
                partidos, cuotas, rep = limpiar(texto, codigo)
                if formato == "extra":                       # un solo archivo con todas las temporadas
                    corte = datetime(anio_actual - n_temp, 1, 1, tzinfo=timezone.utc)
                    partidos = partidos[partidos["inicio"] >= corte]
                n = cargar_historico(conn, codigo, partidos, cuotas)
                if formato == "principal":                   # córners, tiros y árbitro (mercados nuevos)
                    n["estadisticas"] = estadisticas.guardar(conn, liga, estadisticas.leer(texto, codigo))["guardadas"]
                conn.commit()
            except Exception as e:                           # un archivo malo no debe tumbar las demás ligas
                conn.rollback()
                fallos.append(f"{codigo} {ruta}: {type(e).__name__}: {e}")
                print(f"  ⚠ {codigo} {ruta}: se omitió ({type(e).__name__}: {e})")
                continue
            print(f"{codigo} {ruta}: {n} · descartes {rep.descartes}")
        if liga["usa_xg"] and not a.sin_xg:
            resolver = ResolverEquipos(conn, similitud_min=cfg["normalizacion"]["similitud_min"],
                                       margen_min=cfg["normalizacion"]["margen_min"])
            for y in anios:
                temporada = f"{y}-{y + 1}"
                try:
                    xs = und.obtener(Solicitud(Capacidad.XG, codigo, temporada=temporada))
                except ErrorProveedor as e:
                    # Sin xG el partido entra al modelo solo con goles (nunca se mezcla otra fuente).
                    print(f"  ⚠ {codigo} xG {temporada}: Understat no respondió ({e}). Se sigue sin xG; "
                          "reintenta luego con --solo-xg.")
                    continue
                ok = sin = 0
                for x in xs:
                    lid = resolver.resolver("understat", x.local, liga["id"])
                    vid = resolver.resolver("understat", x.visitante, liga["id"])
                    p = repo.buscar_partido(conn, liga["id"], lid, vid, x.inicio) if lid and vid else None
                    if p is None:
                        sin += 1
                        continue
                    repo.set_xg(conn, p["id"], x.xg_local, x.xg_visitante, x.familia_xg)
                    ok += 1
                conn.commit()
                print(f"{codigo} xG {temporada}: {ok} partidos con xG, {sin} sin emparejar")
            if resolver.pendientes:
                print(f"  ⚠ {len(set(resolver.pendientes))} nombres de Understat sin resolver: "
                      "revísalos en la UI (pestaña Datos) y vuelve a correr este script.")
    conn.close()
    if fallos:
        print(f"\n⚠ {len(fallos)} archivo(s) omitidos; el resto quedó cargado:")
        for f in fallos:
            print("  -", f)


if __name__ == "__main__":
    main()
