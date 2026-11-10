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
