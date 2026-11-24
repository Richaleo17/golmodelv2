"""Resolución de nombres de equipo entre fuentes.

Regla de precisión: un alias equivocado mezcla los datos de dos equipos y
contamina el modelo sin que se note. Por eso:
1. alias exacto ya registrado -> se usa;
2. clave normalizada idéntica a la de un equipo de ESA liga -> alias automático;
3. similitud >= similitud_min y claramente mejor que el segundo candidato -> alias automático;
4. cualquier otro caso -> alias_pendientes (se resuelve a mano en la UI). Nunca se adivina.
Todos los alias automáticos quedan marcados (equipo_alias.automatico) para auditarlos.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import psycopg
import yaml

PALABRAS_VACIAS = {"fc", "cf", "sc", "ac", "afc", "acf", "ss", "bc", "club", "cd", "ca", "ec", "se", "ssc", "as",
                   "the", "de", "del", "da", "do", "futebol", "clube", "calcio", "us", "sv", "cfc", "fbc", "sad",
                   "deportivo"}
# Abreviaturas que usan algunas fuentes. 'united' y 'city' NO se quitan nunca: distinguen equipos.
ABREVIATURAS = {"man": "manchester", "utd": "united", "nottm": "nottingham"}


def clave(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    tokens = [ABREVIATURAS.get(t, t) for t in re.split(r"[^a-z0-9]+", s) if t]
    # Fuera palabras vacías y años de fundación ("Parma Calcio 1913" -> "parma")
    tokens = [t for t in tokens if t not in PALABRAS_VACIAS and not (t.isdigit() and len(t) == 4)]
    return " ".join(tokens) or s.strip()


@lru_cache(maxsize=1)
def semillas() -> dict[str, dict[str, str]]:
    """{liga: {clave(variante): clave(nombre en mi base)}} desde config/alias_equipos.yaml (tabla explícita)."""
    ruta = Path(__file__).resolve().parents[3] / "config" / "alias_equipos.yaml"
    if not ruta.exists():
        ruta = Path("config/alias_equipos.yaml")
    if not ruta.exists():
        return {}
    crudo = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    return {liga: {clave(v): clave(canon) for canon, vs in eqs.items() for v in [canon, *vs]}
            for liga, eqs in crudo.items()}


# Tablas que apuntan a `equipos` (db/schema.sql). `equipo_alias` se mueve aparte y `equipo_temporada` tiene llave compuesta.
TABLAS_EQUIPO = (("partidos", "local_id"), ("partidos", "visitante_id"), ("alias_pendientes", "sugerencia_id"),
                 ("jugadores", "equipo_id"), ("jugador_produccion", "equipo_id"), ("contexto_lesiones", "equipo_id"),
                 ("alineaciones", "equipo_id"), ("calendario_equipos", "equipo_id"), ("parametros_equipo", "equipo_id"))


def fusionar_equipo(conn: psycopg.Connection, viejo: int, nuevo: int) -> dict:
    """Funde el equipo duplicado `viejo` en `nuevo`: todo lo que lo apuntaba pasa a `nuevo` y `viejo` se borra si queda libre.
    Cada tabla va en su propio punto de guardado: si una no se puede mover (llave repetida o tabla inmutable) se deja y se informa;
    en ese caso `viejo` no se borra (nunca se pierde una fila)."""
    if viejo == nuevo:
        return {}
    out: dict = {}
    for tabla, col in TABLAS_EQUIPO:
        try:
            with conn.transaction():
                out[f"{tabla}.{col}"] = conn.execute(f"UPDATE {tabla} SET {col} = %s WHERE {col} = %s", (nuevo, viejo)).rowcount
        except Exception:                                                       # noqa: BLE001
            out[f"{tabla}.{col}"] = "omitida"
    with conn.transaction():
        conn.execute("""INSERT INTO equipo_temporada (equipo_id, liga_id, temporada)
                        SELECT %s, liga_id, temporada FROM equipo_temporada WHERE equipo_id = %s ON CONFLICT DO NOTHING""", (nuevo, viejo))
        conn.execute("DELETE FROM equipo_temporada WHERE equipo_id = %s", (viejo,))
        out["alias"] = conn.execute("UPDATE equipo_alias SET equipo_id = %s WHERE equipo_id = %s", (nuevo, viejo)).rowcount
    try:
        with conn.transaction():
            conn.execute("DELETE FROM equipos WHERE id = %s", (viejo,))
        out["borrado"] = True
    except Exception:                                                           # noqa: BLE001
        out["borrado"] = False
    return out


class ResolverEquipos:
    def __init__(self, conn: psycopg.Connection, *, similitud_min: float = 0.90, margen_min: float = 0.08):
        self.conn = conn
        self.similitud_min = similitud_min
        self.margen_min = margen_min
        self._alias: dict[tuple[str, str], int] = {}
        self._por_liga: dict[int, dict[int, set[str]]] = {}
        self.pendientes: list[tuple[str, str, int]] = []
        for r in conn.execute("SELECT fuente, nombre_externo, equipo_id FROM equipo_alias"):
            self._alias[(r["fuente"], r["nombre_externo"])] = r["equipo_id"]
        # Pendientes que ya no lo son (se unieron después, o el nombre es idéntico al de un equipo de la liga)
        conn.execute("""UPDATE alias_pendientes p SET resuelto = true WHERE NOT resuelto AND (
                          EXISTS (SELECT 1 FROM equipo_alias a WHERE a.fuente = p.fuente AND a.nombre_externo = p.nombre_externo)
                          OR EXISTS (SELECT 1 FROM equipos e WHERE e.nombre = p.nombre_externo
                                     AND e.id IN (SELECT equipo_id FROM equipo_temporada WHERE liga_id = p.liga_id)))""")

    def _codigo_liga(self, liga_id: int) -> str:
        if not hasattr(self, "_codigos"):
            self._codigos = {r["id"]: r["codigo"] for r in self.conn.execute("SELECT id, codigo FROM ligas")}
        return self._codigos.get(liga_id, "")

    def _equipos_liga(self, liga_id: int) -> dict[int, set[str]]:
        if liga_id not in self._por_liga:
            filas = self.conn.execute("""
                SELECT e.id, e.nombre, a.nombre_externo
                FROM equipos e
                LEFT JOIN equipo_alias a ON a.equipo_id = e.id
                WHERE e.id IN (SELECT equipo_id FROM equipo_temporada WHERE liga_id = %(l)s
                               UNION SELECT local_id FROM partidos WHERE liga_id = %(l)s
                               UNION SELECT visitante_id FROM partidos WHERE liga_id = %(l)s)
            """, {"l": liga_id}).fetchall()
            d: dict[int, set[str]] = {}
            for f in filas:
                d.setdefault(f["id"], set()).add(clave(f["nombre"]))
                if f["nombre_externo"]:
                    d[f["id"]].add(clave(f["nombre_externo"]))
            self._por_liga[liga_id] = d
        return self._por_liga[liga_id]

    def _mercado_ids(self) -> set[int]:
        if not hasattr(self, "_mk"):
            self._mk = {r["id"] for r in self.conn.execute("SELECT id FROM ligas WHERE solo_mercado")}
        return self._mk

    def _asignar(self, fuente: str, nombre: str, eid: int, previo: int | None) -> int:
        """Alias nuevo, o corrección de uno que apuntaba a otro equipo. Si el anterior era un DUPLICADO creado por una competición
        solo-mercado (pais vacío: Champions, Copa, Segunda…), se funde con el equipo de la liga: sin esto las cuotas de la liga
        nunca encontraban su partido (Real Madrid–Celta, Barcelona–Villarreal… se quedaban sin una sola cuota)."""
        if previo is None:
            self._registrar_alias(fuente, nombre, eid)
        elif previo != eid:
            self.conn.execute("UPDATE equipo_alias SET equipo_id = %s, automatico = true WHERE fuente = %s AND nombre_externo = %s",
                              (eid, fuente, nombre))
            self._alias[(fuente, nombre)] = eid
            fila = self.conn.execute("SELECT pais FROM equipos WHERE id = %s", (previo,)).fetchone()
            if fila is not None and fila["pais"] == "":
                fusionar_equipo(self.conn, previo, eid)
                self._por_liga.clear()
        return eid

    def _registrar_alias(self, fuente: str, nombre: str, equipo_id: int) -> None:
        self.conn.execute("""
            INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo, automatico)
            VALUES (%s, %s, %s, true) ON CONFLICT (fuente, nombre_externo) DO NOTHING
        """, (equipo_id, fuente, nombre))
        self._alias[(fuente, nombre)] = equipo_id
        # Si estaba pendiente de revisión manual y ahora se resolvió con seguridad, sale de la lista
        self.conn.execute("UPDATE alias_pendientes SET resuelto = true WHERE fuente = %s AND nombre_externo = %s",
                          (fuente, nombre))

    def _pendiente(self, fuente: str, nombre: str, liga_id: int, sug: int | None, sim: float | None) -> None:
        self.conn.execute("""
            INSERT INTO alias_pendientes (fuente, nombre_externo, liga_id, sugerencia_id, similitud)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (fuente, nombre_externo, liga_id)
            DO UPDATE SET veces = alias_pendientes.veces + 1, visto_en = now(),
                          sugerencia_id = EXCLUDED.sugerencia_id, similitud = EXCLUDED.similitud
        """, (fuente, nombre, liga_id, sug, sim))
        self.pendientes.append((fuente, nombre, liga_id))

    def resolver(self, fuente: str, nombre: str, liga_id: int) -> int | None:
        nombre = nombre.strip()
        previo = self._alias.get((fuente, nombre))
        if previo is not None and liga_id in self._mercado_ids():
            return previo                                                    # competiciones solo-mercado: sin cambios
        equipos = self._equipos_liga(liga_id)
        k = clave(nombre)
        canon = semillas().get(self._codigo_liga(liga_id), {}).get(k)       # tabla explícita de equivalencias (manda sobre alias previos)
        if canon is not None:
            por_semilla = [eid for eid, claves in equipos.items() if canon in claves]
            if len(por_semilla) == 1:
                return self._asignar(fuente, nombre, por_semilla[0], previo)
        if previo is not None:
            fila = self.conn.execute("SELECT pais FROM equipos WHERE id = %s", (previo,)).fetchone()
            if fila is None or fila["pais"] != "":
                return previo                                                # alias a un equipo de liga: se respeta
            # alias hacia un duplicado de competición solo-mercado: se ignora y se vuelve a resolver contra los equipos de ESTA liga
        exactos = [eid for eid, claves in equipos.items() if k in claves]
        if len(exactos) == 1:
            return self._asignar(fuente, nombre, exactos[0], previo)
        # Todas las palabras de UN SOLO equipo están contenidas en el nombre externo
        # ("newcastle" ⊂ "newcastle united"). Si encajan dos o más equipos, no se adivina.
        if not exactos:
            tk = set(k.split())
            contenidos = [eid for eid, claves in equipos.items()
                          if any(c and set(c.split()) <= tk for c in claves)]
            if len(contenidos) == 1:
                return self._asignar(fuente, nombre, contenidos[0], previo)
        puntajes = sorted(((max(difflib.SequenceMatcher(None, k, c).ratio() for c in claves), eid)
                           for eid, claves in equipos.items()), reverse=True)
        if puntajes:
            mejor, eid = puntajes[0]
            segundo = puntajes[1][0] if len(puntajes) > 1 else 0.0
            if len(exactos) == 0 and mejor >= self.similitud_min and mejor - segundo >= self.margen_min:
                return self._asignar(fuente, nombre, eid, previo)
            self._pendiente(fuente, nombre, liga_id, eid, round(mejor, 3))
        else:
            self._pendiente(fuente, nombre, liga_id, None, None)
        return None
