"""Histórico de ligas que football-data.co.uk NO publica (Liga MX Femenil), por temporada completa vía la cadena
de RESULTADOS (API-Football primero, TheSportsDB de respaldo).

Cuesta 1 request por temporada en API-Football (una temporada = Apertura N + Clausura N+1). La primera fuente
que entrega una temporada fija los nombres canónicos de sus equipos; las demás se unen a ellos por alias
(misma regla de precisión que el resto: nada se adivina por parecido al crear equipos).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from golmodel import repositorio as repo
from golmodel.ingesta.normalizacion import ResolverEquipos, clave, semillas
from golmodel.ingesta.proveedores.cadena import Cadena, Interruptor
from golmodel.ingesta.proveedores.football_data_csv import CODIGOS
from golmodel.ingesta.tipos import Calidad, Capacidad, Solicitud

# Intentos automáticos desde el pipeline: uno por liga y día (no quemar el límite diario de API-Football).
_INTENTOS: dict[str, date] = {}
MIN_FINALIZADOS = 100          # con menos partidos jugados se considera que la liga no tiene histórico


def necesita_historico_api(codigo: str) -> bool:
    """Ligas sin CSV de football-data: su histórico solo puede venir de las APIs."""
    return codigo not in CODIGOS


def anios_temporada(n_temporadas: int, hoy: datetime | None = None) -> list[int]:
    hoy = hoy or datetime.now(timezone.utc)
    actual = hoy.year if hoy.month >= 7 else hoy.year - 1
    return list(range(actual - n_temporadas, actual + 1))


def _equipo(conn, resolver: ResolverEquipos, fuente: str, nombre: str, liga: dict, temporada: str) -> int | None:
    """Equipo ya conocido (alias, tabla de semillas o nombre idéntico) → ese; si no, alta canónica."""
    equipos = resolver._equipos_liga(liga["id"])
    k = clave(nombre)
    canon = semillas().get(liga["codigo"], {}).get(k)
    conocido = (fuente, nombre) in resolver._alias or any(k in c or (canon and canon in c) for c in equipos.values())
    if conocido:
        eid = resolver.resolver(fuente, nombre, liga["id"])
        if eid is None:
            return None
        conn.execute("""INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING""", (eid, liga["id"], temporada))
        return eid
    eid = repo.equipo_canonico(conn, nombre, liga["pais"], liga["id"], temporada, fuente)
    resolver._alias[(fuente, nombre)] = eid
    resolver._por_liga.pop(liga["id"], None)
    return eid


def cargar_historico_api(conn, cadena_resultados, codigo: str, anios: list[int], *,
                         resolver: ResolverEquipos | None = None) -> dict:
    """Carga las temporadas `anios` (año de inicio). Devuelve un resumen por temporada; nunca lanza por una
    temporada que ninguna fuente entregue (queda en el resumen)."""
    liga = repo.ligas(conn)[codigo]
    resolver = resolver or ResolverEquipos(conn)
    res: dict = {"partidos": 0, "temporadas": {}, "avisos": []}
    for y in anios:
        # Interruptor PROPIO por temporada: el plan gratis de API-Football rechaza la temporada en curso («try from 2022
        # to 2024») y ese ErrorPlan pausa al proveedor 24 h en el interruptor compartido. Aquí una temporada fuera del
        # plan no debe bloquear las que sí cubre (ni las del resto del pipeline dependen de esto).
        cad = Cadena(Capacidad.RESULTADOS, cadena_resultados.eslabones, interruptor=Interruptor(),
                     presupuestos=cadena_resultados.presupuestos)
        r = cad.obtener(Solicitud(Capacidad.RESULTADOS, codigo, temporada=f"{y}-Apertura"))
        if r.calidad is Calidad.SIN_DATOS:
            res["temporadas"][y] = "sin datos"
            res["avisos"] += r.avisos
            continue
        eq = r.calidad is not Calidad.DEGRADADA
        n = sin = 0
        for ext in r.datos:
            if ext.estado != "finalizado" or ext.goles_local is None:
                continue
            temp = repo.temporada_de(liga, ext.inicio)
            lid = _equipo(conn, resolver, r.proveedor, ext.local, liga, temp)
            vid = _equipo(conn, resolver, r.proveedor, ext.visitante, liga, temp)
            if lid is None or vid is None or lid == vid:
                sin += 1
                continue
            repo.upsert_partido(conn, liga, ext, lid, vid, equivalente=eq)
            n += 1
        res["temporadas"][y] = f"{n} partidos ({r.proveedor})" + (f", {sin} sin equipo" if sin else "")
        res["partidos"] += n
        res["avisos"] += r.avisos
    repo.marcar_ascendidos(conn, liga["id"])
    return res


def autocargar(conn, cadena_resultados, liga: dict, n_temporadas: int, hoy: date, resolver: ResolverEquipos) -> dict | None:
    """Desde «Actualizar datos»: si una liga sin CSV no tiene histórico, lo carga (una vez al día como máximo)."""
    if cadena_resultados is None or not necesita_historico_api(liga["codigo"]) or _INTENTOS.get(liga["codigo"]) == hoy:
        return None
    n = conn.execute("SELECT count(*) AS n FROM partidos WHERE liga_id = %s AND estado = 'finalizado'",
                     (liga["id"],)).fetchone()["n"]
    if n >= MIN_FINALIZADOS:
        return None
    _INTENTOS[liga["codigo"]] = hoy
    return cargar_historico_api(conn, cadena_resultados, liga["codigo"], anios_temporada(n_temporadas),
                                resolver=resolver)
