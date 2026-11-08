"""Único job automático: snapshot de la cuota de cierre X minutos antes de cada partido con picks.

    python -m golmodel.jobs.cierre        # revisa cada minuto

Si el snapshot falla tras los reintentos, el cierre se completa después con el CSV de
football-data (liquidación); si tampoco hay, el pick queda "sin CLV".
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from golmodel import db, repositorio as repo
from golmodel.ingesta.normalizacion import ResolverEquipos
from golmodel.ingesta.tipos import Calidad, Capacidad, Solicitud

log = logging.getLogger("golmodel.cierre")


def procesar_pendientes(conn, cfg: dict, cadenas: dict, ahora: datetime) -> dict:
    res = {"ok": 0, "fallido": 0, "reintentar": 0}
    pend = conn.execute("""
        SELECT c.*, p.liga_id, p.inicio, p.odds_api_id, p.api_football_id, p.local_id, p.visitante_id, l.codigo AS liga
        FROM cierres_programados c JOIN partidos p ON p.id = c.partido_id JOIN ligas l ON l.id = p.liga_id
        WHERE c.estado = 'pendiente' AND c.programado_para <= %s AND p.inicio > %s
    """, (ahora, ahora - timedelta(minutes=1))).fetchall()
    cad = cadenas.get(Capacidad.CUOTAS_CIERRE)
    resolver = ResolverEquipos(conn)
    for c in pend:
        ids = {k: str(v) for k, v in (("odds_api", c["odds_api_id"]), ("api_football", c["api_football_id"])) if v}
        r = cad.obtener(Solicitud(Capacidad.CUOTAS_CIERRE, c["liga"], desde=c["inicio"].date(), hasta=c["inicio"].date(),
                                  ids_partido=ids, para_cierre=True)) if cad else None
        filas = {}
        if r is not None and r.calidad is not Calidad.SIN_DATOS:
            for q in r.datos:
                if q.id_partido_externo and q.id_partido_externo in ids.values():
                    mismo = True
                else:
                    lid = resolver.resolver(r.proveedor, q.local, c["liga_id"])
                    vid = resolver.resolver(r.proveedor, q.visitante, c["liga_id"])
                    mismo = (lid, vid) == (c["local_id"], c["visitante_id"])
                if mismo:
                    filas.setdefault(q.casa, []).append((q.seleccion, q.cuota))
        if filas:
            for casa, f in filas.items():
                repo.insertar_cuotas(conn, c["partido_id"], repo.casa_id(conn, casa), f, "cierre", r.proveedor, ahora)
            conn.execute("UPDATE cierres_programados SET estado = 'ok', intentos = intentos + 1 WHERE partido_id = %s",
                         (c["partido_id"],))
            res["ok"] += 1
        else:
            intentos = c["intentos"] + 1
            estado = "fallido" if intentos > cfg["cierre"]["reintentos"] else "pendiente"
            conn.execute("""UPDATE cierres_programados SET estado = %s, intentos = %s, ultimo_error = %s
                            WHERE partido_id = %s""",
                         (estado, intentos, "; ".join(r.avisos) if r else "sin cadena de cierre", c["partido_id"]))
            res["fallido" if estado == "fallido" else "reintentar"] += 1
    conn.commit()
    return res


def main() -> None:  # pragma: no cover - proceso de larga vida
    from golmodel.config import cargar
    from golmodel.ingesta.proveedores.registro import construir_cadenas, construir_presupuestos, construir_proveedores
    logging.basicConfig(level=logging.INFO)
    cfg = cargar()
    reg = repo.registrador_api()
    provs = construir_proveedores(cfg, dict(os.environ), registrar=reg)
    cadenas = construir_cadenas(cfg, provs, construir_presupuestos(cfg))
    while True:
        conn = db.conectar()
        try:
            r = procesar_pendientes(conn, cfg, cadenas, datetime.now(timezone.utc))
            if any(r.values()):
                log.info("cierres: %s", r)
        except Exception:
            log.exception("fallo en el job de cierre")
        finally:
            conn.close()
        time.sleep(60)


if __name__ == "__main__":  # pragma: no cover
    main()
