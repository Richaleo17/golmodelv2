"""Catálogo de equipos, abreviaturas de 3 letras («CHE», «MCI») y sugerencias del análisis de boleto.

Las abreviaturas se DERIVAN del nombre: no se inventa ninguna lista de la casa. La regla de seguridad es que un par de abreviaturas solo se acepta si
entre los partidos PROGRAMADOS hay uno solo donde ambos equipos encajan. Los verificadores se prueban contra casos que deben FALLAR (un emparejador
que acepta todo no verifica nada)."""
from __future__ import annotations

import pytest

from golmodel import db
from golmodel.modelos import incertidumbre as inc
from golmodel.motor import dependencia as dep
from golmodel.servicios import boleto as bo
from golmodel.servicios import catalogo as cat
from tests.test_boleto import _legs, _momento, _partidos, api  # noqa: F401
from tests.test_integracion import cadenas, cargado, cfg, dsn, mundo  # noqa: F401
from tests.test_integracion_benchmark import bd, con  # noqa: F401
from tests.test_integracion_candidatas import _dia, poblada  # noqa: F401
from tests.test_tickets import _cfg


# ------------------------------------------------------------------ abreviaturas (puras)
@pytest.mark.parametrize("nombre,esperadas", [
    ("Chelsea", {"CHE"}), ("Man City", {"MAN", "CIT", "MCI"}), ("Man United", {"MAN", "UNI", "MUN"}), ("Real Madrid", {"REA", "MAD", "RMA"}),
    ("Paris Saint Germain", {"PAR", "PSG"}), ("BK Hacken", {"BKH", "HAC"}), ("Manchester City FC", {"MAN", "CIT", "MCI"}), ("Tottenham", {"TOT"}),
])
def test_codigos_derivados_del_nombre(nombre, esperadas):
    assert esperadas <= cat.codigos_de(nombre)


def test_codigos_no_inventa_lo_que_no_se_deriva():
    assert "BHA" not in cat.codigos_de("Brighton")                 # «BHA» no sale del nombre: va por alias manual
    assert cat.codigos_de("") == set() and all(len(c) == 3 for c in cat.codigos_de("Real Sociedad de Fútbol"))


@pytest.mark.parametrize("t,esperado", [("CHE", True), ("mci", True), ("PSG", True), ("Chelsea", False), ("Man City", False), ("1X", False), ("", False), ("A", False)])
def test_es_abreviatura(t, esperado):
    assert cat.es_abreviatura(t) is esperado


def test_encaja_acepta_abreviaturas_y_nombres_y_rechaza_lo_demas():
    chelsea = {"Chelsea", "Chelsea FC"}
    assert cat.encaja("CHE", chelsea) >= 0.8 and cat.encaja("Chelsea", chelsea) == 1.0 and cat.encaja("chelsea fc", chelsea) >= 0.8
    assert cat.encaja("ARS", chelsea) == 0 and cat.encaja("Arsenal", chelsea) == 0 and cat.encaja("CHX", chelsea) == 0
    assert cat.encaja("MCI", {"Man City", "Manchester City FC"}) >= 0.8 and cat.encaja("MCI", {"Man United"}) == 0


# ------------------------------------------------------------------ emparejar_flexible (puro)
def _p(i, local, visita, liga="EPL"):
    return {"id": i, "local_id": 100 + 2 * i, "visitante_id": 101 + 2 * i, "local": local, "visitante": visita, "liga": liga}


def _nombres(partidos):
    return {p[k]: {p[n]} for p in partidos for k, n in (("local_id", "local"), ("visitante_id", "visitante"))}


def test_par_de_abreviaturas_unico_se_acepta():
    ps = [_p(1, "BK Hacken", "Chelsea", "MK:x"), _p(2, "Arsenal", "Everton")]
    p, motivo, ops = cat.emparejar_flexible("HAC", "CHE", ps, _nombres(ps))
    assert p["id"] == 1 and motivo == "" and ops == []
    assert cat.emparejar_flexible("BK Hacken", "CHE", ps, _nombres(ps))[0]["id"] == 1             # mezcla nombre + abreviatura
    assert cat.emparejar_flexible("ARS", "EVE", ps, _nombres(ps))[0]["id"] == 2


def test_par_ambiguo_no_se_adivina_y_ofrece_las_opciones():
    ps = [_p(1, "Man City", "Chelsea"), _p(2, "Man United", "Chelsea")]
    p, motivo, ops = cat.emparejar_flexible("MAN", "CHE", ps, _nombres(ps))
    assert p is None and "Ambiguo" in motivo and {o["partido_id"] for o in ops} == {1, 2}
    assert cat.emparejar_flexible("MCI", "CHE", ps, _nombres(ps))[0]["id"] == 1                  # una abreviatura más específica sí desempata
    assert cat.emparejar_flexible("MUN", "CHE", ps, _nombres(ps))[0]["id"] == 2


def test_par_que_no_existe_o_invertido_se_rechaza_con_motivo():
    ps = [_p(1, "Man City", "Arsenal")]
    p, motivo, ops = cat.emparejar_flexible("MAN", "LIV", ps, _nombres(ps))
    assert p is None and "No encontré" in motivo and ops == []
    p, motivo, _ = cat.emparejar_flexible("ARS", "MAN", ps, _nombres(ps))                       # visitante primero
    assert p is None and "invertido" in motivo.lower()


# ------------------------------------------------------------------ resolver con abreviaturas y con el nombre completo que lea Anthropic
def _resolver(monkeypatch, partidos, lectura):
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(bo.repo, "partidos_de_dias", lambda conn, dias: partidos)
    monkeypatch.setattr(bo.captura, "_nombres", lambda conn, ps: _nombres(ps))
    return bo.resolver(None, _momento(), ZoneInfo("America/Mexico_City"), lectura)


def _bloque(local, visita, sel, **extra):
    return {"partidos": [{"local": local, "visita": visita, **extra, "selecciones": [{"mercado_texto": "GANA", "seleccion_texto": sel, "linea": None, "cuota": 1.2}]}]}


def test_resolver_entiende_abreviaturas_del_partido_y_de_la_seleccion(monkeypatch):
    ps = [_p(1, "BK Hacken", "Chelsea", "MK:x"), _p(2, "Arsenal", "Everton")]
    r = _resolver(monkeypatch, ps, _bloque("HAC", "CHE", "CHE"))
    g = r["legs"][0]
    assert g["estado"] == "ok" and g["partido_id"] == 1 and g["seleccion"] == "1X2:2"           # «CHE» es el visitante → gana el visitante
    assert "abreviatura" in g["entendido_como"] and "confírmalo" in g["entendido_como"]
    assert _resolver(monkeypatch, ps, _bloque("HAC", "CHE", "HAC"))["legs"][0]["seleccion"] == "1X2:1"
    exacto = _resolver(monkeypatch, ps, _bloque("Arsenal", "Everton", "Arsenal"))["legs"][0]   # con nombres completos no cambia nada ni pide confirmar
    assert exacto["estado"] == "ok" and exacto["partido_id"] == 2 and "entendido_como" not in exacto


def test_resolver_ambiguo_deja_elegir_y_no_adivina(monkeypatch):
    ps = [_p(1, "Man City", "Chelsea"), _p(2, "Man United", "Chelsea")]
    g = _resolver(monkeypatch, ps, _bloque("MAN", "CHE", "CHE"))["legs"][0]
    assert g["estado"] == "sin_partido" and g["partido_id"] is None and "Ambiguo" in g["motivo"] and len(g["opciones"]) == 2


def test_nombre_completo_leido_por_anthropic_solo_vale_si_el_partido_esta_programado(monkeypatch):
    ps = [_p(1, "BK Hacken", "Chelsea", "MK:x")]
    ok = _resolver(monkeypatch, ps, _bloque("QX", "ZZ", "CHE", local_completo="BK Hacken", visita_completo="Chelsea"))["legs"][0]
    assert ok["estado"] == "ok" and ok["partido_id"] == 1 and "nombre completo" in ok["entendido_como"]
    inventado = _resolver(monkeypatch, ps, _bloque("QX", "ZZ", "CHE", local_completo="Real Madrid", visita_completo="Barcelona"))["legs"][0]
    assert inventado["estado"] == "sin_partido" and inventado["partido_id"] is None            # el modelo no puede «crear» un partido que no existe


# ------------------------------------------------------------------ catálogo en la base y alias manuales
def _equipo(con, nombre, pais="Inglaterra"):
    return con.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, %s) RETURNING id", (nombre, pais)).fetchone()["id"]


def test_listar_trae_alias_por_fuente_y_abreviaturas(con):  # noqa: F811
    e = _equipo(con, "Brighton")
    con.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo) VALUES (%s, 'odds_api', 'Brighton and Hove Albion')", (e,))
    con.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo) VALUES (%s, 'manual', 'BHA')", (e,))
    r = [x for x in cat.listar(con, q="brighton") if x["id"] == e][0]
    assert r["alias"] == {"manual": ["BHA"], "odds_api": ["Brighton and Hove Albion"]} and "BRI" in r["abreviaturas"] and r["pais"] == "Inglaterra"
    assert any(x["id"] == e for x in cat.listar(con, q="BHA"))                                   # buscar por la abreviatura manual encuentra al equipo
    assert not any(x["id"] == e for x in cat.listar(con, q="zzzz-no-existe"))
    assert len(cat.listar(con, limite=3)) <= 3


def test_alias_manual_sirve_para_abreviaturas_que_no_se_derivan(con):  # noqa: F811
    e = _equipo(con, "Brighton")
    assert cat.encaja("BHA", {"Brighton"}) == 0                                                  # sin alias, «BHA» no se entiende
    r = cat.agregar_alias(con, e, "BHA")
    assert r == {"equipo_id": e, "equipo": "Brighton", "alias": "BHA", "nuevo": True}
    nombres = {"Brighton"} | {x["nombre_externo"] for x in con.execute("SELECT nombre_externo FROM equipo_alias WHERE equipo_id = %s", (e,))}
    assert cat.encaja("BHA", nombres) >= 0.8                                                     # con el alias exacto, sí
    assert cat.agregar_alias(con, e, "BHA")["nuevo"] is False                                    # repetirlo no duplica


def test_un_alias_no_puede_apuntar_a_dos_equipos_y_se_valida_la_entrada(con):  # noqa: F811
    a, b = _equipo(con, "Equipo Uno"), _equipo(con, "Equipo Dos")
    cat.agregar_alias(con, a, "EQX")
    with pytest.raises(cat.ErrorCatalogo, match="ya es alias de «Equipo Uno»") as e:
        cat.agregar_alias(con, b, "EQX")
    assert e.value.codigo == 409
    for malo, cod in (("x", 422), ("", 422), ("a" * 61, 422)):
        with pytest.raises(cat.ErrorCatalogo) as ee:
            cat.agregar_alias(con, a, malo)
        assert ee.value.codigo == cod
    with pytest.raises(cat.ErrorCatalogo) as ee:
        cat.agregar_alias(con, 99999999, "ZZZ")
    assert ee.value.codigo == 404


def test_api_catalogo_y_roles(api):
    cli, bd_ = api
    with db.conectar(bd_) as c:
        e = _equipo(c, "Brighton")
        c.commit()
    r = cli.get("/catalogo/equipos", params={"q": "brigh"})
    assert r.status_code == 200 and any(x["id"] == e and "BRI" in x["abreviaturas"] for x in r.json())
    ok = cli.post("/catalogo/alias", json={"equipo_id": e, "alias": "BHA"})
    assert ok.status_code == 200 and ok.json()["nuevo"] is True
    assert cli.post("/catalogo/alias", json={"equipo_id": e, "alias": "BHA"}).json()["nuevo"] is False
    otro = None
    with db.conectar(bd_) as c:
        otro = _equipo(c, "Otro Club")
        c.commit()
    r409 = cli.post("/catalogo/alias", json={"equipo_id": otro, "alias": "BHA"})
    assert r409.status_code == 409 and "Brighton" in r409.json()["detail"]
    assert cli.post("/catalogo/alias", json={"equipo_id": e, "alias": "x"}).status_code == 422
    from golmodel import acceso
    from golmodel.api import seguridad
    env = {"APP_PASSWORD": "admin-1", "APP_VIEWER_PASSWORD": "lector-1", "UI_BIND": "0.0.0.0"}
    tok = f"Bearer {acceso.crear_token(acceso.LECTOR, acceso.secreto('admin-1', 'lector-1', {}))}"
    assert seguridad.decidir("POST", "/catalogo/alias", tok, env)[0] == 403                    # el lector no escribe alias
    assert seguridad.decidir("GET", "/catalogo/equipos", tok, env)[0] is None                  # pero sí consulta el catálogo


# ------------------------------------------------------------------ sugerencias del análisis (números del modelo, recomputados aparte)
def _piernas_ref(con, ps_ids, sels, ahora, v3):
    from golmodel.integracion import benchmark as bm
    from golmodel.integracion import candidatas as cd
    ver = bm.resolver_version(con, ahora, v3)
    out = []
    for pid, sel in zip(ps_ids, sels):
        p = con.execute("SELECT p.*, l.codigo AS liga FROM partidos p JOIN ligas l ON l.id = p.liga_id WHERE p.id = %s", (pid,)).fetchone()
        pred = cd._prediccion(con, p["id"], ahora)
        pesos = cd._pesos(con, pred["version_id"], [p["local_id"], p["visitante_id"]])
        out.append(dep.Pierna(p["id"], p["liga"], float(pred["lambda_local"]), float(pred["lambda_visitante"]), float(pred["rho"]), (sel,),
                              inc.tau_idiosincratico(ver.c_global, pesos[p["local_id"]]), inc.tau_idiosincratico(ver.c_global, pesos[p["visitante_id"]])))
    return out, inc.FactorSamplerJerarquico(sigma_g=float(ver.sigma_g), sigma_liga=ver.sigma_liga)


def test_sugerencias_de_bajar_la_linea_coinciden_con_el_motor_y_estan_ordenadas(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))[:4]
    v3, ahora = _cfg(), _momento()
    r = bo.analizar(con, cfg, v3, ahora, _legs(ps, 4, sel="OU:2.5:over", cuota=1.9), cuota_total=13.0)
    sug = r["conjunta"]["sugerencias"]
    assert 0 < len(sug) <= bo.MAX_SUGERENCIAS and [s["sube_x"] for s in sug] == sorted((s["sube_x"] for s in sug), reverse=True)
    assert all(s["sube_x"] > 1.0 for s in sug)
    bajar = [s for s in sug if s["tipo"] == "bajar"]
    assert bajar and all(s["de"] == "OU:2.5:over" and s["a"] == "OU:1.5:over" for s in bajar)       # la alternativa MÁS SEGURA de la escalera, no otra cosa
    s = bajar[0]
    i = [f["partido"] for f in r["piernas"]].index(s["partido"])
    piernas, sampler = _piernas_ref(con, [f["partido_id"] for f in r["piernas"]], ["OU:2.5:over"] * 4, ahora, v3)
    piernas[i] = dep.Pierna(*(getattr(piernas[i], k) for k in ("partido_id", "liga", "lambda_local", "lambda_visitante", "rho")), ("OU:1.5:over",), piernas[i].tau_local, piernas[i].tau_visit)
    ref = dep.probabilidad_conjunta(piernas, sampler, v3, int(ahora.strftime("%Y%m%d")))
    assert s["p_conservadora_despues"] == ref.p_conservadora                                          # bit a bit con el motor
    assert s["sube_x"] == pytest.approx(ref.p_conservadora / r["conjunta"]["p_conservadora"])
    assert s["cuota_justa_nueva"] == pytest.approx(1 / s["p_pierna_despues"]) and s["p_pierna_despues"] > 0.5


def test_sugerencias_incluyen_quitar_un_partido_y_no_inventan_alternativas(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))[:3]
    r = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 3, sel="BTTS:si", cuota=1.8), cuota_total=5.8)     # BTTS no tiene escalera de alternativas
    sug = r["conjunta"]["sugerencias"]
    assert sug and all(s["tipo"] == "quitar" for s in sug)
    sin = {x["partido"]: x for x in r["conjunta"]["sin_cada_partido"]}
    assert all(s["sube_x"] == pytest.approx(sin[s["partido"]]["sube_x"]) for s in sug)
    uno = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 1, sel="BTTS:si"))
    assert uno["conjunta"]["sugerencias"] == []                                                        # un solo partido: no hay qué quitar


def test_el_analisis_avisa_que_el_modelo_no_sabe_de_motivacion_ni_rotaciones(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))[:2]
    r = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 2))
    assert any("motivación" in a and "rotaciones" in a for a in r["avisos"])


# ------------------------------------------------------------------ competiciones solo mercado: prioridad de las femeniles y diagnóstico
def _d(k, t=None):
    return {"key": k, "title": t or k, "active": True}


def test_una_femenil_pedida_sin_patron_no_queda_al_final_bajo_el_tope_de_creditos():
    from golmodel.config import cargar
    from golmodel.ingesta.competiciones import elegir_competiciones
    cm = cargar()["competiciones_mercado"]
    # Enmienda 2026-11-11: Liga MX Femenil tiene MODELO PROPIO y salió de las reglas solo-mercado de la config. El MECANISMO
    # (una femenil pedida sin patrón no queda al final) se sigue probando con una regla explícita.
    cm = {**cm, "femeniles": [*cm["femeniles"], {"nombre": "Liga MX Femenil", "todos": [["ligamx", "liga_mx"], ["women", "womens", "femenil"]]}]}
    dep = [_d("soccer_spain_copa_del_rey"), _d("soccer_argentina_primera_division"), _d("soccer_mexico_ligamx_women"), _d("soccer_uefa_champs_league")]
    k = [d["key"] for d in elegir_competiciones(dep, set(), cm)]
    assert set(k) == {d["key"] for d in dep}
    assert k.index("soccer_mexico_ligamx_women") < k.index("soccer_argentina_primera_division") < k.index("soccer_spain_copa_del_rey")   # antes que las de patrones bajos
    assert k.index("soccer_uefa_champs_league") <= k.index("soccer_mexico_ligamx_women") or k.index("soccer_mexico_ligamx_women") == 0          # la Champions no se desplaza hacia atrás por ella


def test_diagnostico_dice_con_certeza_si_la_api_ofrece_liga_mx_femenil():
    from golmodel.config import cargar
    from golmodel.ingesta.competiciones import diagnostico
    cm = cargar()["competiciones_mercado"]
    assert "Liga MX Femenil" not in {r["nombre"] for r in cm["femeniles"]}       # ya no es solo mercado (modelo propio MXF)
    # Enmienda 2026-11-11: Liga MX Femenil tiene MODELO PROPIO y salió de las reglas solo-mercado de la config. El MECANISMO
    # (una femenil pedida sin patrón no queda al final) se sigue probando con una regla explícita.
    cm = {**cm, "femeniles": [*cm["femeniles"], {"nombre": "Liga MX Femenil", "todos": [["ligamx", "liga_mx"], ["women", "womens", "femenil"]]}]}
    sin = diagnostico([_d("soccer_mexico_ligamx"), _d("soccer_uefa_champs_league_women"), _d("soccer_epl_women")], {"soccer_mexico_ligamx"}, cm)
    regla = {r["nombre"]: r for r in sin["reglas_femeniles"]}
    assert regla["Liga MX Femenil"]["ofrecida"] is False and regla["Liga MX Femenil"]["coincide"] == []
    assert regla["UEFA Women's Champions League"]["ofrecida"] is True
    assert {f["key"] for f in sin["femeniles_ofrecidas"]} == {"soccer_uefa_champs_league_women", "soccer_epl_women"}      # muestra lo que SÍ hay, incluso lo que no se pidió
    assert sin["total_futbol_activo"] == 3 and [m["key"] for m in sin["mexico"]] == ["soccer_mexico_ligamx"]
    con = diagnostico([_d("soccer_mexico_ligamx_women", "Liga MX Femenil")], set(), cm)
    rc = {r["nombre"]: r for r in con["reglas_femeniles"]}["Liga MX Femenil"]
    assert rc["ofrecida"] is True and rc["coincide"][0]["key"] == "soccer_mexico_ligamx_women"
    assert [e["key"] for e in con["elegidas"]] == ["soccer_mexico_ligamx_women"]


def test_api_revisar_competiciones_sin_llave_con_error_y_con_datos(api):
    from fastapi.testclient import TestClient  # noqa: F401
    from golmodel.api import main
    cli, _ = api
    pipe = main._servicio.pipeline

    class Prov:
        ligas_claves = {"EPL": "soccer_epl"}

        def deportes_futbol(self):
            return [_d("soccer_epl"), _d("soccer_mexico_ligamx_women", "Liga MX Femenil")]
    pipe._proveedor_mercado = lambda: None
    r = cli.post("/catalogo/competiciones/revisar")
    assert r.status_code == 503 and "ODDS_API_KEY" in r.json()["detail"]
    pipe._proveedor_mercado = lambda: Prov()
    r = cli.post("/catalogo/competiciones/revisar")
    j = r.json()
    assert r.status_code == 200 and j["reglas_femeniles"][0] == {"nombre": "UEFA Women's Champions League", "ofrecida": False, "coincide": []}
    # El diagnóstico sigue diciendo con certeza si la API ofrece la Femenil mexicana...
    assert {"key": "soccer_mexico_ligamx_women", "title": "Liga MX Femenil"} in j["femeniles_ofrecidas"] + j["mexico"]
    # ...pero ya no se elige como «solo mercado»: tiene modelo propio (enmienda 2026-11-11). soccer_epl está modelada.
    assert j["elegidas"] == []

    class Roto:
        def deportes_futbol(self):
            raise RuntimeError("https://api.the-odds-api.com/v4/sports?apiKey=SECRETA")
    pipe._proveedor_mercado = lambda: Roto()
    r = cli.post("/catalogo/competiciones/revisar")
    assert r.status_code == 502 and "RuntimeError" in r.json()["detail"] and "SECRETA" not in r.text and "apiKey" not in r.text   # nunca filtra la llave
    from golmodel import acceso
    from golmodel.api import seguridad
    env = {"APP_PASSWORD": "admin-1", "APP_VIEWER_PASSWORD": "lector-1", "UI_BIND": "0.0.0.0"}
    tok = f"Bearer {acceso.crear_token(acceso.LECTOR, acceso.secreto('admin-1', 'lector-1', {}))}"
    assert seguridad.decidir("POST", "/catalogo/competiciones/revisar", tok, env)[0] == 403            # solo el administrador


# ------------------------------------------------------------------ selecciones: códigos de Draftea (ESP, ING, ALE, HOL…) que NO se derivan del nombre en inglés
# Pares tomados de una captura real de Draftea (12–15 nov): cada uno debe encajar con el partido de The Odds API (nombres en inglés).
PARES_DRAFTEA = [("TUR", "BEL", "Turkey", "Belgium"), ("CZE", "ESP", "Czech Republic", "Spain"), ("SMR", "BLR", "San Marino", "Belarus"), ("ITA", "FRA", "Italy", "France"),
                 ("MNE", "LVA", "Montenegro", "Latvia"), ("ING", "CRO", "England", "Croatia"), ("MDA", "KAZ", "Moldova", "Kazakhstan"), ("AND", "GIB", "Andorra", "Gibraltar"),
                 ("BUL", "ISL", "Bulgaria", "Iceland"), ("LUX", "EST", "Luxembourg", "Estonia"), ("HOL", "GRE", "Netherlands", "Greece"), ("SVK", "FRO", "Slovakia", "Faroe Islands"),
                 ("LIE", "AZE", "Liechtenstein", "Azerbaijan"), ("SVN", "SUI", "Slovenia", "Switzerland"), ("SCO", "MKD", "Scotland", "North Macedonia"), ("SRB", "ALE", "Serbia", "Germany"),
                 ("RKS", "ISR", "Kosovo", "Israel"), ("NOR", "WAL", "Norway", "Wales"), ("GEO", "HUN", "Georgia", "Hungary"), ("SWE", "BIH", "Sweden", "Bosnia and Herzegovina"),
                 ("ROU", "POL", "Romania", "Poland"), ("AUT", "IRL", "Austria", "Ireland"), ("NIR", "UKR", "Northern Ireland", "Ukraine"), ("PRT", "DEN", "Portugal", "Denmark"),
                 ("ESP", "ING", "Spain", "England")]


@pytest.mark.parametrize("a,b,na,nb", PARES_DRAFTEA)
def test_cada_codigo_de_draftea_encaja_con_el_nombre_de_la_api(a, b, na, nb):
    assert cat.encaja(a, {na}) >= 0.8 and cat.encaja(b, {nb}) >= 0.8, (a, b)
    assert cat.encaja(a, {nb}) == 0 or a in cat.codigos_de(nb)                       # y no con el rival (salvo coincidencia real de códigos)


def test_el_boleto_entero_de_draftea_empareja_cada_partido_con_el_suyo_y_no_con_otro():
    ps = [_p(i + 1, na, nb, "MK:soccer_uefa_nations_league") for i, (_, _, na, nb) in enumerate(PARES_DRAFTEA)]
    nom = _nombres(ps)
    for i, (a, b, _, _) in enumerate(PARES_DRAFTEA):
        p, motivo, ops = cat.emparejar_flexible(a, b, ps, nom)
        assert p is not None and p["id"] == i + 1 and motivo == "" and ops == [], (a, b, motivo)
    assert cat.emparejar_flexible("ISL", "BUL", ps, nom)[0] is None                    # orden invertido: se rechaza, no se adivina


def test_codigos_de_selecciones_no_chocan_por_palabras_de_relleno():
    assert "AND" not in cat.codigos_de("Bosnia and Herzegovina") and "REP" not in cat.codigos_de("Czech Republic")
    assert {"ESP"} <= cat.codigos_de("Spain") and {"ALE", "GER"} <= cat.codigos_de("Germany") and {"ING", "ENG"} <= cat.codigos_de("England")
    assert {"HOL", "NED"} <= cat.codigos_de("Netherlands") and cat.codigos_de("Chelsea") == {"CHE"}                  # un club no recibe códigos de selección
    assert cat.encaja("ALE", {"Germany"}) >= 0.8 and cat.encaja("ALE", {"Austria"}) == 0 and cat.encaja("ESP", {"Spain"}) >= 0.8


# ------------------------------------------------------------------ fallas halladas en auditoría (cada prueba FALLA con el código anterior)
def test_una_abreviatura_no_encaja_por_parecido_difuso_ni_por_palabras_de_relleno():
    assert cat.encaja("AND", {"Bosnia and Herzegovina"}) == 0                     # antes 0.9 por la palabra «and»: «AND» es Andorra
    assert cat.encaja("AND", {"Andorra"}) >= 0.8
    assert cat.encaja("NOR", {"Northern Ireland"}) == 0 and cat.encaja("NOR", {"Norway"}) >= 0.8      # «NOR» es Noruega, no Irlanda del Norte
    assert cat.encaja("IRE", {"Northern Ireland"}) == 0 and cat.encaja("SPA", {"Spain"}) == 0 and cat.encaja("NET", {"Netherlands"}) == 0
    assert cat.codigos_de("Northern Ireland") == {"NIR"} and cat.codigos_de("Spain") == {"ESP"}        # una selección: SOLO sus códigos
    assert cat.encaja("San", {"San Marino"}) == 0 and cat.encaja("SMR", {"San Marino"}) >= 0.8


def test_andorra_y_bosnia_o_noruega_e_irlanda_del_norte_no_se_confunden_aunque_jueguen_la_misma_semana():
    ps = [_p(1, "Bosnia and Herzegovina", "Gibraltar"), _p(2, "Andorra", "Gibraltar"), _p(3, "Northern Ireland", "Wales"), _p(4, "Norway", "Wales")]
    nom = _nombres(ps)
    assert cat.emparejar_flexible("AND", "GIB", ps, nom)[0]["id"] == 2
    assert cat.emparejar_flexible("BIH", "GIB", ps, nom)[0]["id"] == 1
    assert cat.emparejar_flexible("NOR", "WAL", ps, nom)[0]["id"] == 4
    assert cat.emparejar_flexible("NIR", "WAL", ps, nom)[0]["id"] == 3


def test_resolver_no_asigna_una_abreviatura_a_otro_equipo_por_parecido(monkeypatch):
    solo_bosnia = [_p(1, "Bosnia and Herzegovina", "Gibraltar")]                                      # Andorra NO juega: «AND - GIB» no debe caer en Bosnia
    g = _resolver(monkeypatch, solo_bosnia, _bloque("AND", "GIB", "AND"))["legs"][0]
    assert g["estado"] == "sin_partido" and g["partido_id"] is None
    g2 = _resolver(monkeypatch, [_p(1, "Northern Ireland", "Wales")], _bloque("NOR", "WAL", "NOR"))["legs"][0]
    assert g2["estado"] == "sin_partido" and g2["partido_id"] is None


def test_nombres_cortos_reales_siguen_encajando_con_reglas_estrictas():
    assert cat.encaja("Roma", {"AS Roma"}) == 1.0 and cat.encaja("Lyon", {"Lyon"}) == 1.0 and cat.encaja("Pau", {"Pau FC"}) == 1.0
    assert cat.encaja("City", {"Man City"}) == 0.85 and cat.encaja("Roma", {"Romania"}) == 0          # palabra completa sí; prefijo parecido no
    ps = [_p(1, "Real Madrid", "Girona"), _p(2, "Real Betis", "Girona")]
    p, motivo, ops = cat.emparejar_flexible("Real", "Girona", ps, _nombres(ps))                      # «Real» es ambiguo de verdad: se ofrece elegir
    assert p is None and "Ambiguo" in motivo and len(ops) == 2


# ------------------------------------------------------------------ la FECHA de cada fila del boleto afina el emparejado (nunca lo relaja)
def test_fecha_de_una_fila_del_boleto():
    from datetime import date
    hoy = date(2026, 11, 11)
    assert bo._fecha("12/11", hoy) == date(2026, 11, 12) and bo._fecha("12/nov", hoy) == date(2026, 11, 12) and bo._fecha("12-11", hoy) == date(2026, 11, 12)
    assert bo._fecha("3/ene", date(2026, 12, 28)) == date(2027, 1, 3) and bo._fecha("30/dic", date(2027, 1, 2)) == date(2026, 12, 30)     # cambio de año: el más cercano
    assert bo._fecha("31/02", hoy) is None and bo._fecha(None, hoy) is None and bo._fecha("nov", hoy) is None and bo._fecha("12/xyz", hoy) is None


def _pf(i, local, visita, dias):
    from datetime import timedelta
    return {**_p(i, local, visita), "inicio": _momento() + timedelta(days=dias, hours=1)}


def _dm(dias):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    return f"{(_momento() + timedelta(days=dias, hours=1)).astimezone(ZoneInfo('America/Mexico_City')):%d/%m}"


def test_la_fecha_explica_por_que_no_hay_partido_cargado_o_ya_paso(monkeypatch):
    ps = [_pf(1, "Turkey", "Belgium", 1), _pf(2, "Serbia", "Germany", 2)]
    futuro = _resolver(monkeypatch, ps, _bloque("NOR", "WAL", "NOR", fecha=_dm(4)))["legs"][0]                 # la base llega hasta +2 días
    assert futuro["estado"] == "sin_partido" and "todavía no lo tiene cargado" in futuro["motivo"] and f"hasta el {_dm(2)}" in futuro["motivo"]
    pasado = _resolver(monkeypatch, ps, _bloque("NOR", "WAL", "NOR", fecha=_dm(-3)))["legs"][0]
    assert pasado["estado"] == "sin_partido" and "ya pasó" in pasado["motivo"]
    ok = _resolver(monkeypatch, ps, _bloque("TUR", "BEL", "TUR", fecha=_dm(1)))["legs"][0]
    assert ok["estado"] == "ok" and ok["partido_id"] == 1 and ok["texto"].startswith(_dm(1))
    sin_fecha = _resolver(monkeypatch, ps, _bloque("NOR", "WAL", "NOR"))["legs"][0]                              # sin fecha: el motivo de siempre
    assert "No encontré" in sin_fecha["motivo"]


def test_la_fecha_desempata_un_par_ambiguo_y_tolera_un_dia_por_huso_horario(monkeypatch):
    ps = [_pf(1, "Man City", "Chelsea", 1), _pf(2, "Man United", "Chelsea", 3)]
    assert _resolver(monkeypatch, ps, _bloque("MAN", "CHE", "CHE"))["legs"][0]["estado"] == "sin_partido"       # sin fecha: ambiguo, no se adivina
    g = _resolver(monkeypatch, ps, _bloque("MAN", "CHE", "CHE", fecha=_dm(3)))["legs"][0]
    assert g["estado"] == "ok" and g["partido_id"] == 2 and g["seleccion"] == "1X2:2" and "confírmalo" in g["entendido_como"]
    juntos = [_pf(1, "Man City", "Chelsea", 1), _pf(2, "Man United", "Chelsea", 2)]                              # a un día de distancia la fecha NO alcanza para desempatar
    assert _resolver(monkeypatch, juntos, _bloque("MAN", "CHE", "CHE", fecha=_dm(1)))["legs"][0]["estado"] == "sin_partido"
    cerca = _resolver(monkeypatch, [_pf(1, "Turkey", "Belgium", 2)], _bloque("TUR", "BEL", "TUR", fecha=_dm(1)))["legs"][0]
    assert cerca["estado"] == "ok" and cerca["partido_id"] == 1                                                  # ±1 día: se acepta


def test_si_el_unico_partido_que_encaja_es_de_otra_fecha_no_se_da_por_bueno_y_se_ofrece_elegirlo(monkeypatch):
    ps = [_pf(1, "Spain", "England", 1)]
    g = _resolver(monkeypatch, ps, _bloque("ESP", "ING", "ESP", fecha=_dm(5)))["legs"][0]                       # el boleto dice +5 días; el partido cargado es +1
    assert g["estado"] == "sin_partido" and g["partido_id"] is None and "La fecha no coincide" in g["motivo"]
    assert [o["partido_id"] for o in g["opciones"]] == [1] and g["seleccion_sugerida"] == "1X2:1"               # la persona puede elegirlo si de verdad es ese


# ------------------------------------------------------------------ comprobación aritmética de la lectura y mensaje del día cargado a medias
def test_el_producto_de_las_cuotas_leidas_se_compara_con_el_momio_del_boleto():
    # Las 25 cuotas de un boleto real de Draftea: su producto es su momio (90.51).
    reales = [1.16, 1.14, 1.21, 1.19, 1.14, 1.15, 1.50, 1.10, 1.07, 1.18, 1.19, 1.27, 1.27, 1.37, 1.22, 1.41, 1.06, 1.14, 1.10, 1.22, 1.24, 1.26, 1.10, 1.18, 1.17]
    legs = [{"cuota": q} for q in reales]
    v = bo._verificar_lectura(legs, 90.51)
    assert v["coincide"] is True and v["producto"] == pytest.approx(90.51, rel=0.005) and v["n"] == 25 and v["mensaje"].startswith("✓")
    mal = bo._verificar_lectura([{"cuota": 1.76}] + legs[1:], 90.51)                                # una cuota mal leída (1.16 → 1.76)
    assert mal["coincide"] is False and mal["mensaje"].startswith("⚠") and "Revisa las filas" in mal["mensaje"]
    assert bo._verificar_lectura(legs[:-1], 90.51)["coincide"] is False                              # falta una fila
    assert bo._verificar_lectura(legs, None) is None and bo._verificar_lectura([{"cuota": None}] + legs, 90.51) is None and bo._verificar_lectura([], 90.51) is None
    assert bo._verificar_lectura(legs, True) is None and bo._verificar_lectura(legs, 1.0) is None    # sin datos suficientes NO se afirma nada


def test_resolver_entrega_la_verificacion_y_explica_un_dia_cargado_a_medias(monkeypatch):
    ps = [_pf(1, "Turkey", "Belgium", 1), _pf(2, "Ponte Preta", "Operario", 3)]                     # el día +3 SÍ tiene partidos, pero no el de selecciones
    lect = {"cuota_total": 1.2, "partidos": [{"fecha": _dm(1), "local": "TUR", "visita": "BEL", "selecciones": [{"mercado_texto": "GANA", "seleccion_texto": "TUR", "linea": None, "cuota": 1.2}]},
                                              {"fecha": _dm(3), "local": "NOR", "visita": "WAL", "selecciones": [{"mercado_texto": "GANA", "seleccion_texto": "NOR", "linea": None, "cuota": 1.0}]}]}
    r = _resolver(monkeypatch, ps, lect)
    falta = r["legs"][1]
    assert falta["estado"] == "sin_partido" and f"partidos cargados del {_dm(3)}" in falta["motivo"] and "Actualizar datos" in falta["motivo"]
    assert r["verificacion"] is None                                                                 # una cuota no se leyó (1.0 no es cuota): no se afirma nada
    lect["partidos"][1]["selecciones"][0]["cuota"] = 1.5
    v = _resolver(monkeypatch, ps, lect)["verificacion"]
    assert v["coincide"] is False and v["producto"] == pytest.approx(1.8)
