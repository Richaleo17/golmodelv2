"""Backtest OOS de la calibración de tickets sobre la BD REAL (orquestador).

    docker compose run --rm api python scripts/backtest_oos.py --datos-reales

Lee cestas ex-post del histórico, ajusta `c` en walk-forward estricto, corre las pruebas A (calibración de p_media) y B (sobredispersión día×liga) sobre el test OOS, aplica el
protocolo de σ_g, guarda la versión en `versiones_calibrador_tickets` (migración 005; también si los gates fallan) y escribe el reporte.

Códigos de salida: 0 = corrió y las pruebas A y B PASARON · 3 = NO superados (A de calibración de p_media o B de sobredispersión día×liga FALLA con evidencia;
enmienda v0.14) · 4 = NO EVALUABLE (sin evidencia suficiente: muestra chica, pocos eventos/grupos, potencia insuficiente, σ>0 o `c` sin identificar; no es un fallo
del calibrador) · 2 = error de uso/datos. En 3 y 4 la versión queda registrada y no se usa. NO apaga `modo_experimental`: eso es una decisión humana sobre el reporte.
`--datos-reales`: declaración explícita de que la BD contiene datos reales. Sin ella, aun con gates OK la versión queda como 'sinteticos'
(que NO sirve para apagar `modo_experimental`).
Lento por diseño (Monte Carlo sobre miles de cestas, ver presupuesto del pre-registro §2.7): `--n-draws` reduce la precisión para una prueba rápida.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from golmodel import db  # noqa: E402
from golmodel.config import cargar, cargar_v3  # noqa: E402
from golmodel.integracion import backtest_oos as bo  # noqa: E402


def _fecha(s: str) -> datetime:
    return datetime.combine(date.fromisoformat(s), datetime.min.time(), timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    hoy = datetime.now(timezone.utc).date()
    ap.add_argument("--desde", type=_fecha, default=_fecha((hoy - timedelta(days=730)).isoformat()), help="por defecto: hace 730 días")
    ap.add_argument("--hasta", type=_fecha, default=_fecha(hoy.isoformat()), help="por defecto: hoy (UTC)")
    ap.add_argument("--casa", default=None, help="casa de cuotas del histórico (por defecto backtest_oos.casa de la config; sin respaldo a otra)")
    ap.add_argument("--semilla", type=int, default=0)
    ap.add_argument("--n-draws", type=int, default=None, help="sorteos de Monte Carlo (por defecto calibracion_tickets.n_draws)")
    ap.add_argument("--datos-reales", action="store_true", help="declara que la BD tiene datos reales (habilita calibrado_con='oos_real')")
    ap.add_argument("--sin-guardar", action="store_true", help="no escribe en versiones_calibrador_tickets (ensayo)")
    ap.add_argument("--reporte", default=str(RAIZ / "reports" / "backtest_oos_resultado.md"))
    a = ap.parse_args(argv)
    if a.desde >= a.hasta:
        print("--desde debe ser anterior a --hasta", file=sys.stderr)
        return 2
    cfg_v2, cfg_v3 = cargar(), cargar_v3()
    conn = db.conectar()
    try:
        r, vid = bo.ejecutar(conn, cfg_v2, cfg_v3, desde=a.desde, hasta=a.hasta, casa=a.casa, semilla=a.semilla, datos_reales=a.datos_reales,
                             n_draws=a.n_draws, guardar=not a.sin_guardar, progreso=lambda m: print(f"  · {m}", flush=True))
    except ValueError as e:
        conn.rollback()
        print(f"✗ {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    ruta = Path(a.reporte)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(bo.reporte_md(r, version_id=vid), encoding="utf-8")
    print(f"{'✓' if r.gates_ok else '✗'} gates {bo.ETIQUETA_VEREDICTO[r.veredicto]} · calibrado_con={r.calibrado_con} · "
          f"c_global={r.version.c_global:.4f} · σ_g={r.sigma_g} · versión {vid if vid else 'no guardada'}")
    print(f"  motivo: {r.motivo}\n  reporte: {ruta}")
    return {"superados": 0, "no_superados": 3, "no_evaluable": 4}[r.veredicto]


if __name__ == "__main__":
    raise SystemExit(main())
