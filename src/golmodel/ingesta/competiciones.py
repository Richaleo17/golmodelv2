"""Elección de competiciones SOLO-MERCADO (sin histórico ni modelo propio) entre los deportes que ofrece The Odds API.

Regla (decisión 2026-11-10): las competiciones femeniles (`femeniles` en config) se incluyen por reglas de TOKENS sobre la clave del deporte, aunque la
config excluya `women`. NO se inventan claves: si The Odds API no ofrece la competición, simplemente no aparece (ni partidos ni cuotas). Como el resto de
las competiciones solo-mercado, no tienen histórico ni modelo: el sistema no genera picks ni tickets V3 para ellas y quedan fuera del ajuste de calibración.
"""
from __future__ import annotations

TOKENS_FEMENIL = ("women", "womens", "femenil")


def _coincide_femenil(clave: str, regla: dict) -> bool:
    """`todos`: lista de grupos; cada grupo es una lista de alternativas. Coincide si de CADA grupo aparece al menos una alternativa en la clave."""
    return all(any(t.lower() in clave for t in grupo) for grupo in regla["todos"])


def elegir_competiciones(deportes: list[dict], modeladas: set[str], cm: dict) -> list[dict]:
    pats = [x.lower() for x in cm.get("patrones", [])]
    excl = [x.lower() for x in cm.get("excluir", [])]
    femeniles = cm.get("femeniles") or []
    elegidos = []
    fem_de: dict[str, bool] = {}
    for d in deportes:
        k = str(d["key"]).lower()
        if d["key"] in modeladas:
            continue
        es_fem = any(_coincide_femenil(k, r) for r in femeniles)
        fem_de[d["key"]] = es_fem
        excl_ef = [x for x in excl if x not in TOKENS_FEMENIL] if es_fem else excl     # una femenil elegida NO se excluye por ser femenil
        if (es_fem or any(x in k for x in pats)) and not any(x in k for x in excl_ef):
            elegidos.append(d)
    # Prioridad = orden de los patrones (Champions primero). Una femenil pedida por el responsable que NO case ningún patrón (p. ej. Liga MX Femenil)
    # tendría prioridad 99 y sería la primera en caerse con el tope de créditos: se le da la del primer patrón.
    elegidos.sort(key=lambda d: min((i for i, x in enumerate(pats) if x in d["key"].lower()), default=0 if fem_de.get(d["key"]) else 99))
    return elegidos


def diagnostico(deportes: list[dict], modeladas: set[str], cm: dict) -> dict:
    """Qué ofrece The Odds API frente a las reglas del proyecto (sin gastar créditos: solo usa la lista de deportes).
    Sirve para saber CON CERTEZA si una competición (p. ej. Liga MX Femenil) existe y cómo se llama su clave, en vez de adivinarla."""
    def _ficha(d):
        return {"key": d["key"], "title": d.get("title") or d["key"]}
    claves = lambda d: str(d["key"]).lower()                                                       # noqa: E731
    elegidas = elegir_competiciones(deportes, modeladas, cm)
    reglas = []
    for r in cm.get("femeniles") or []:
        hallazgos = [_ficha(d) for d in deportes if _coincide_femenil(claves(d), r)]
        reglas.append({"nombre": r.get("nombre", "?"), "ofrecida": bool(hallazgos), "coincide": hallazgos})
    return {"total_futbol_activo": len(deportes), "elegidas": [_ficha(d) for d in elegidas],
            "femeniles_ofrecidas": [_ficha(d) for d in deportes if any(t in claves(d) for t in TOKENS_FEMENIL)],
            "mexico": [_ficha(d) for d in deportes if any(t in claves(d) for t in ("mexic", "ligamx", "liga_mx"))],
            "reglas_femeniles": reglas}
