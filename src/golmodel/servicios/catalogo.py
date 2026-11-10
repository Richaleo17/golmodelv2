"""Catálogo de equipos y emparejado por ABREVIATURAS (las casas, p. ej. Draftea, escriben «CHE», «MCI», «RMA»).

El catálogo ya existe en la base: `equipos` (nombre canónico) + `equipo_alias` (cómo lo llama cada API: football-data, API-Football,
The Odds API… y alias manuales). Este módulo NO crea tablas nuevas: solo
  * lista ese catálogo con sus alias y las abreviaturas plausibles de cada equipo, y
  * resuelve un par de abreviaturas («CHE - HAC») contra los partidos PROGRAMADOS, con una regla de seguridad:
    el par solo se acepta si entre los partidos de esos días hay UNO solo donde ambos equipos encajan.
    Si hay varios o ninguno, NO se adivina: se devuelven las opciones para que la persona elija.

Las abreviaturas se DERIVAN del nombre (no se inventa ninguna lista de la casa). Una abreviatura que no se pueda derivar
(p. ej. «BHA» para Brighton) se resuelve agregándola como alias manual (`agregar_alias`) o por el nombre completo que lea Anthropic.
"""
from __future__ import annotations

import re
import unicodedata

from golmodel.servicios import captura

# Palabras que no aportan a una abreviatura («FC», «CF», «AFC»…).
RUIDO = {"fc", "cf", "afc", "sc", "ac", "as", "cd", "ud", "rc", "ca", "de", "del", "la", "el", "los", "las", "club", "the", "women", "femenil", "w"}
MAX_OPCIONES = 4


def _limpio(t: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def codigos_de(nombre: str) -> set[str]:
    """Abreviaturas de 3 letras (MAYÚSCULAS) plausibles de un nombre. «Man City» → {MAN, CIT, MCI, MC…}; «Chelsea» → {CHE}."""
    toks = [t for t in _limpio(nombre).split() if t]
    util = [t for t in toks if t not in RUIDO] or toks
    out: set[str] = set()
    if not util:
        return out
    pegado = "".join(util)
    if len(pegado) >= 3:
        out.add(pegado[:3].upper())                                   # CHE (Chelsea) · MAN (Man City)
    for t in util:
        if len(t) >= 3:
            out.add(t[:3].upper())                                    # cada palabra: MAN, CIT
    if len(util) >= 2:
        out.add((util[0][0] + util[-1][:2]).upper())                  # MCI (Man City) · RMA (Real Madrid) · MUN (Man United)
        if len(util) >= 3:
            out.add("".join(t[0] for t in util[:3]).upper())          # PSG (Paris Saint Germain)
    return {c for c in out if len(c) == 3 and c.isalnum()}


def es_abreviatura(texto: str) -> bool:
    """Una sola palabra de 2 a 4 letras («CHE», «MCI», «PSG»)."""
    t = str(texto or "").strip()
    return bool(re.fullmatch(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2,4}\.?", t))


def encaja(texto: str, nombres: set[str]) -> float:
    """Qué tan bien encaja `texto` con un equipo (todos sus nombres y alias): 1.0 si es una abreviatura derivada o un alias exacto,
    si no el puntaje normal de nombre. 0 si no encaja."""
    base = captura.puntaje(texto, nombres)
    if base >= captura.MIN_PUNTAJE:
        return base
    if es_abreviatura(texto):
        t = _limpio(texto).upper().replace(" ", "")
        if any(t == _limpio(n).upper().replace(" ", "") or t in codigos_de(n) for n in nombres):
            return 0.85                                                # válido, pero menos firme que un nombre idéntico
    return 0.0


def emparejar_flexible(local: str, visitante: str, partidos: list[dict], nombres: dict[int, set[str]]) -> tuple[dict | None, str, list[dict]]:
    """(partido | None, motivo, opciones). Acepta nombres completos, parciales o abreviaturas, en cualquier mezcla.
    Regla de seguridad: SOLO si hay un único partido donde ambos equipos encajan en el orden local-visitante."""
    cand: list[tuple[float, dict]] = []
    inv = 0
    for p in partidos:
        sl, sv = encaja(local, nombres[p["local_id"]]), encaja(visitante, nombres[p["visitante_id"]])
        if sl and sv:
            cand.append((sl + sv, p))
        elif encaja(local, nombres[p["visitante_id"]]) and encaja(visitante, nombres[p["local_id"]]):
            inv += 1
    if len(cand) == 1:
        return cand[0][1], "", []
    if not cand:
        if inv:
            return None, "Orden invertido: en tu casa el equipo LOCAL va primero (el de la izquierda).", []
        return None, f"No encontré «{local} - {visitante}» entre los partidos programados de los próximos días.", []
    cand.sort(key=lambda x: -x[0])
    if cand[0][0] - cand[1][0] >= 0.10:                                # uno encaja claramente mejor (p. ej. nombre completo vs. abreviatura)
        return cand[0][1], "", []
    opciones = [{"partido_id": p["id"], "partido": f"{p['local']} vs {p['visitante']}", "liga": p["liga"]} for _, p in cand[:MAX_OPCIONES]]
    return None, "Ambiguo: la abreviatura encaja con varios partidos (" + "; ".join(o["partido"] for o in opciones) + "). Elige el correcto.", opciones


# ------------------------------------------------------------------ catálogo (lectura y alias manuales)
def listar(conn, *, q: str = "", limite: int = 60) -> list[dict]:
    """Equipos con sus alias por fuente, sus ligas y sus abreviaturas derivadas. `q` filtra por nombre, alias o abreviatura."""
    limite = max(1, min(int(limite), 200))
    filas = conn.execute("""
        SELECT e.id, e.nombre, e.pais,
               coalesce(array_agg(DISTINCT l.codigo) FILTER (WHERE l.codigo IS NOT NULL), '{}') AS ligas
        FROM equipos e
        LEFT JOIN partidos p ON p.local_id = e.id OR p.visitante_id = e.id
        LEFT JOIN ligas l ON l.id = p.liga_id
        GROUP BY e.id ORDER BY e.nombre""").fetchall()
    alias: dict[int, dict[str, list[str]]] = {}
    for r in conn.execute("SELECT equipo_id, fuente, nombre_externo FROM equipo_alias ORDER BY fuente, nombre_externo"):
        alias.setdefault(r["equipo_id"], {}).setdefault(r["fuente"], []).append(r["nombre_externo"])
    qn = _limpio(q)
    qc = str(q or "").strip().upper()
    out = []
    for f in filas:
        nombres = {f["nombre"]} | {n for lst in alias.get(f["id"], {}).values() for n in lst}
        codigos = sorted(set().union(*(codigos_de(n) for n in nombres)))
        if qn and not (any(qn in _limpio(n) for n in nombres) or (len(qc) == 3 and qc in codigos)):
            continue
        out.append({"id": f["id"], "nombre": f["nombre"], "pais": f["pais"], "ligas": sorted(f["ligas"]),
                    "alias": alias.get(f["id"], {}), "abreviaturas": codigos})
        if len(out) >= limite:
            break
    return out


class ErrorCatalogo(Exception):
    def __init__(self, mensaje: str, codigo: int = 422):
        super().__init__(mensaje)
        self.codigo = codigo


def agregar_alias(conn, equipo_id: int, alias: str) -> dict:
    """Alias MANUAL (fuente 'manual') para un equipo: sirve para abreviaturas que no se derivan del nombre («BHA» → Brighton).
    Un alias solo puede pertenecer a UN equipo: si ya es de otro, se rechaza diciendo de cuál."""
    alias = " ".join(str(alias or "").split())
    if not (2 <= len(alias) <= 60):
        raise ErrorCatalogo("El alias debe tener entre 2 y 60 caracteres.")
    eq = conn.execute("SELECT id, nombre FROM equipos WHERE id = %s", (equipo_id,)).fetchone()
    if eq is None:
        raise ErrorCatalogo("Ese equipo no existe.", 404)
    previo = conn.execute("""SELECT e.id, e.nombre FROM equipo_alias a JOIN equipos e ON e.id = a.equipo_id
                             WHERE a.fuente = 'manual' AND a.nombre_externo = %s""", (alias,)).fetchone()
    if previo is not None:
        if previo["id"] == eq["id"]:
            return {"equipo_id": eq["id"], "equipo": eq["nombre"], "alias": alias, "nuevo": False}
        raise ErrorCatalogo(f"«{alias}» ya es alias de «{previo['nombre']}». Un alias no puede apuntar a dos equipos.", 409)
    conn.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo, automatico) VALUES (%s, 'manual', %s, false)", (eq["id"], alias))
    return {"equipo_id": eq["id"], "equipo": eq["nombre"], "alias": alias, "nuevo": True}
