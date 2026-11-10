"""Boleto por captura (servicios/boleto.py + /boleto/*): normalización al catálogo, lectura con Anthropic (cliente simulado), emparejado con partidos reales de la BD y análisis
con el motor de dependencia del V3. Datos SINTÉTICOS; PostgreSQL real. Nunca se llama a Anthropic de verdad."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from golmodel import db
from golmodel.api import main
from golmodel.modelos import incertidumbre as inc
from golmodel.motor import dependencia as dep
from golmodel.servicios import boleto as bo
from tests.test_integracion import AHORA as AHORA_BD, JORNADA, cadenas, cargado, cfg, dsn, mundo  # noqa: F401
from tests.test_integracion_benchmark import bd, con  # noqa: F401
from tests.test_integracion_candidatas import _dia, poblada  # noqa: F401
from tests.test_tickets import _cfg

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


# ------------------------------------------------------------------ normalización (pura)
@pytest.mark.parametrize("mercado,seleccion,linea,esperado", [
    ("GANA", "Cruz Azul", None, "1X2:1"), ("GANA", "América", None, "1X2:2"), ("GANADOR DEL PARTIDO", "Empate", None, "1X2:X"), ("Resultado final", "X", None, "1X2:X"),
    ("AMBOS ANOTAN", "Sí", None, "BTTS:si"), ("Ambos equipos anotan", "No", None, "BTTS:no"), ("BTTS", "Yes", None, "BTTS:si"),
    ("TOTAL DE GOLES", "Más 2.5", None, "OU:2.5:over"), ("Total de goles", "Menos", 3.5, "OU:3.5:under"), ("Goles más/menos", "Más", 1.5, "OU:1.5:over"),
    ("Over/Under", "Over 2,5", None, "OU:2.5:over"),
    ("DOBLE OPORTUNIDAD", "Cruz Azul o empate", None, "DC:1X"), ("Doble oportunidad", "Empate o América", None, "DC:X2"), ("Doble oportunidad", "Cruz Azul o América", None, "DC:12"),
    ("DOBLE OPORTUNIDAD", "1X", None, "DC:1X"),
    ("ANOTA", "Cruz Azul Sí", None, "ANOTA:local:si"),
])
def test_normalizar_a_catalogo(mercado, seleccion, linea, esperado):
    assert bo.normalizar(mercado, seleccion, linea, "Cruz Azul", "América") == (esperado, "")


@pytest.mark.parametrize("mercado,seleccion,linea,fragmento", [
    ("TIROS DE ESQUINA", "Más 7.5", 7.5, "no cubre"), ("Tarjetas totales", "Menos 4.5", 4.5, "no cubre"), ("Goles del jugador", "Más", 0.5, "no cubre"),
    ("PRIMER GOL", "Cruz Azul", None, "no entra"), ("TOTAL DE GOLES", "Más 4.5", 4.5, "fuera del catálogo"), ("TOTAL DE GOLES", "Más", None, "línea"),
    ("GANA", "Pumas", None, "equipo"), ("Marcador exacto", "2-1", None, "no reconocido"), ("AMBOS ANOTAN", "tal vez", None, "No entendí"),
])
def test_normalizar_rechaza_con_motivo_lo_que_no_cubre(mercado, seleccion, linea, fragmento):
    codigo, motivo = bo.normalizar(mercado, seleccion, linea, "Cruz Azul", "América")
    assert codigo is None and fragmento.lower() in motivo.lower()


# ------------------------------------------------------------------ lectura con Anthropic (simulada)
def _respuesta(texto):
    return {"content": [{"type": "text", "text": texto}]}


LECTURA_OK = '{"partidos":[{"local":"A","visita":"B","selecciones":[{"mercado_texto":"GANA","seleccion_texto":"A","linea":null,"cuota":1.8}]}],"monto":100,"cuota_total":1.8}'


def test_leer_captura_manda_la_imagen_y_el_prompt_con_la_llave_del_entorno_y_parsea_el_json():
    visto = {}

    def falso(payload, llave):
        visto.update(payload=payload, llave=llave)
        return _respuesta("Aquí tienes:\n" + LECTURA_OK + "\nlisto")                       # texto alrededor: se extrae el JSON
    r = bo.leer_captura(PNG, "image/png", enviar=falso, env={"ANTHROPIC_API_KEY": "  sk-prueba  "})
    assert r["partidos"][0]["local"] == "A" and r["cuota_total"] == 1.8
    assert visto["llave"] == "sk-prueba" and visto["payload"]["model"] == bo.MODELO_DEFECTO
    c = visto["payload"]["messages"][0]["content"]
    assert c[0]["type"] == "image" and c[0]["source"]["media_type"] == "image/png" and base64.b64decode(c[0]["source"]["data"]) == PNG and "SOLO un JSON" in c[1]["text"]
    assert bo.leer_captura(PNG, "image/png", enviar=falso, env={"ANTHROPIC_API_KEY": "k", "ANTHROPIC_MODEL": "otro-modelo"}) and visto["payload"]["model"] == "otro-modelo"


@pytest.mark.parametrize("imagen,tipo,env,texto,codigo,fragmento", [
    (PNG, "image/png", {}, LECTURA_OK, 503, "ANTHROPIC_API_KEY"),                                    # sin llave: se explica qué falta, no se llama a nadie
    (PNG, "image/bmp", {"ANTHROPIC_API_KEY": "k"}, LECTURA_OK, 422, "Formato"),
    (b"", "image/png", {"ANTHROPIC_API_KEY": "k"}, LECTURA_OK, 422, "vacía"),
    (b"x" * (bo.MAX_BYTES + 1), "image/png", {"ANTHROPIC_API_KEY": "k"}, LECTURA_OK, 422, "pesa"),
    (PNG, "image/png", {"ANTHROPIC_API_KEY": "k"}, "no veo nada", 422, "No pude leer"),
    (PNG, "image/png", {"ANTHROPIC_API_KEY": "k"}, '{"partidos": [', 422, "No pude leer"),
    (PNG, "image/png", {"ANTHROPIC_API_KEY": "k"}, '{"partidos": []}', 422, "No vi partidos"),
])
def test_leer_captura_falla_con_mensaje_claro(imagen, tipo, env, texto, codigo, fragmento):
    llamadas = []
    with pytest.raises(bo.ErrorBoleto) as e:
        bo.leer_captura(imagen, tipo, enviar=lambda p, k: llamadas.append(1) or _respuesta(texto), env=env)
    assert e.value.codigo == codigo and fragmento in str(e.value)
    if codigo in (503, 422) and ("Formato" in fragmento or "vacía" in fragmento or "pesa" in fragmento or "ANTHROPIC" in fragmento):
        assert not llamadas                                                                       # validaciones ANTES de gastar una llamada a la API


def test_la_llave_nunca_aparece_en_los_mensajes_de_error():
    with pytest.raises(bo.ErrorBoleto) as e:
        bo.leer_captura(PNG, "image/png", enviar=lambda p, k: _respuesta("nada"), env={"ANTHROPIC_API_KEY": "sk-secreta-123"})
    assert "sk-secreta-123" not in str(e.value)


# ------------------------------------------------------------------ con PostgreSQL real
def _momento():
    """El instante en que el mundo sintético hizo sus predicciones (antes del inicio de los partidos del día)."""
    return AHORA_BD


def _partidos(con, dia):
    return con.execute("""SELECT p.id, p.estado, el.nombre AS local, ev.nombre AS visitante, l.codigo AS liga FROM partidos p JOIN ligas l ON l.id = p.liga_id
                          JOIN equipos el ON el.id = p.local_id JOIN equipos ev ON ev.id = p.visitante_id WHERE dia_mx(p.inicio) = %s AND p.estado = 'programado'
                          ORDER BY p.id""", (dia,)).fetchall()


def _lectura(ps, extra=None):
    return {"partidos": [{"local": p["local"], "visita": p["visitante"], "selecciones": [{"mercado_texto": "TOTAL DE GOLES", "seleccion_texto": "Más 1.5", "linea": 1.5, "cuota": 1.3}] + (extra or [])}
                         for p in ps], "monto": 50, "cuota_total": 2.9}


def test_resolver_empareja_los_partidos_reales_y_marca_lo_que_no(con, cfg):  # noqa: F811
    from zoneinfo import ZoneInfo
    dia = _dia(con)
    ps = _partidos(con, dia)[:3]
    lect = _lectura(ps, [{"mercado_texto": "TIROS DE ESQUINA", "seleccion_texto": "Más", "linea": 7.5}])
    lect["partidos"].append({"local": "Equipo Fantasma", "visita": "Otro Inventado", "selecciones": [{"mercado_texto": "GANA", "seleccion_texto": "Equipo Fantasma"}]})
    r = bo.resolver(con, _momento(), ZoneInfo(cfg["zona_horaria"]), lect)
    estados = [(g["partido_id"], g["estado"]) for g in r["legs"]]
    assert [e for e in estados if e[1] == "ok"] == [(p["id"], "ok") for p in ps]                       # los 3 reales con su selección
    assert sum(1 for g in r["legs"] if g["estado"] == "no_soportado") == 3 and sum(1 for g in r["legs"] if g["estado"] == "sin_partido") == 1
    assert all(g["seleccion"] == "OU:1.5:over" and g["cuota"] == 1.3 for g in r["legs"] if g["estado"] == "ok")
    assert r["n_ok"] == 3 and r["n_total"] == 7 and r["cuota_total"] == 2.9 and r["monto"] == 50
    fantasma = next(g for g in r["legs"] if g["estado"] == "sin_partido")
    assert fantasma["partido_id"] is None and "No encontré" in fantasma["motivo"]


def _legs(ps, n, sel="OU:1.5:over", cuota=1.3):
    return [{"partido_id": p["id"], "seleccion": sel, "cuota": cuota} for p in ps[:n]]


def test_analizar_coincide_con_el_motor_de_dependencia_llamado_aparte(con, cfg):  # noqa: F811
    from golmodel.integracion import benchmark as bm
    from golmodel.integracion import candidatas as cd
    dia = _dia(con)
    ps = _partidos(con, dia)
    v3, ahora = _cfg(), _momento()
    r = bo.analizar(con, cfg, v3, ahora, _legs(ps, 3), cuota_total=2.2, monto=50)
    ok = [f for f in r["piernas"] if f["estado"] == "ok"]
    assert len(ok) == 3 and r["calibrado_con"] in ("no_calibrado", "sinteticos", "oos_real")
    # recomputo INDEPENDIENTE con las mismas primitivas
    ver = bm.resolver_version(con, ahora, v3)
    piernas = []
    for f in ok:
        p = con.execute("SELECT * FROM partidos WHERE id = %s", (f["partido_id"],)).fetchone()
        pred = cd._prediccion(con, p["id"], ahora)
        pesos = cd._pesos(con, pred["version_id"], [p["local_id"], p["visitante_id"]])
        piernas.append(dep.Pierna(p["id"], f["liga"], float(pred["lambda_local"]), float(pred["lambda_visitante"]), float(pred["rho"]), ("OU:1.5:over",),
                                  inc.tau_idiosincratico(ver.c_global, pesos[p["local_id"]]), inc.tau_idiosincratico(ver.c_global, pesos[p["visitante_id"]])))
    ref = dep.probabilidad_conjunta(piernas, inc.FactorSamplerJerarquico(sigma_g=float(ver.sigma_g), sigma_liga=ver.sigma_liga), v3, int(ahora.strftime("%Y%m%d")))
    c = r["conjunta"]
    assert c["p_media"] == ref.p_media and c["p_conservadora"] == ref.p_conservadora and c["p_independencia"] == ref.p_independencia and c["lift"] == ref.lift   # bit a bit
    assert c["p_conservadora"] <= c["p_media"] and c["n_partidos"] == 3
    assert c["ev_conservador"] == pytest.approx(c["p_conservadora"] * 2.2 - 1) and c["ev_media"] == pytest.approx(c["p_media"] * 2.2 - 1)
    assert c["cuota_justa_conservadora"] == pytest.approx(1 / c["p_conservadora"]) and c["monto"] == 50
    assert all(0 < f["p_mostrar"] < 1 and f["p_modelo"] > 0 and f["cuota_justa"] > 1 for f in ok)
    assert any("PROVISIONAL" in a for a in r["avisos"]) == (r["calibrado_con"] != "oos_real") and any("garantía" in a for a in r["avisos"])


def test_analizar_sin_cada_partido_y_stake_de_referencia(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))
    r = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 4, cuota=1.6), cuota_total=6.5)
    c = r["conjunta"]
    assert len(c["sin_cada_partido"]) == 4
    for s in c["sin_cada_partido"]:
        assert s["p_conservadora"] > 0 and s["sube_x"] > 0.9 and s["cuota"] == pytest.approx(6.5 / 1.6)         # sin ese partido: cuota total / su cuota
    assert c["stake_referencia_u"] >= 0 and c["stake_referencia_u"] <= _cfg()["bankroll"]["stake_max_parlay_u"]
    uno = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 1))
    assert "sin_cada_partido" not in uno["conjunta"] and "ev_media" not in uno["conjunta"]                    # sin cuota total no hay EV; con un partido no hay «sin cada partido»


def test_analizar_agrupa_dos_selecciones_del_mismo_partido_como_una_pierna(con, cfg):  # noqa: F811
    p = _partidos(con, _dia(con))[0]
    r = bo.analizar(con, cfg, _cfg(), _momento(), [{"partido_id": p["id"], "seleccion": "OU:1.5:over"}, {"partido_id": p["id"], "seleccion": "BTTS:si"}], cuota_total=3.0)
    assert len(r["piernas"]) == 1 and r["piernas"][0]["selecciones"] == ["OU:1.5:over", "BTTS:si"] and r["conjunta"]["n_partidos"] == 1
    assert r["piernas"][0]["p_mostrar"] == r["piernas"][0]["p_modelo"]                                         # combinada: sin p_adj de mercado, se muestra la del modelo
    with pytest.raises(bo.ErrorBoleto, match="máximo 2"):
        bo.analizar(con, cfg, _cfg(), _momento(), [{"partido_id": p["id"], "seleccion": s} for s in ("OU:1.5:over", "BTTS:si", "1X2:1")])


def test_analizar_distingue_lo_que_no_entra_a_la_conjunta(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))
    con.execute("UPDATE partidos SET estado = 'cancelado' WHERE id = %s", (ps[0]["id"],))
    r = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 3), cuota_total=2.0)
    estados = {f["partido_id"]: f["estado"] for f in r["piernas"]}
    assert estados[ps[0]["id"]] == "ya_empezo" and list(estados.values()).count("ok") == 2 and r["conjunta"]["n_partidos"] == 2
    assert any("NO entran a la probabilidad conjunta" in a for a in r["avisos"])
    from datetime import timedelta
    tarde = bo.analizar(con, cfg, _cfg(), _momento() + timedelta(hours=7), _legs(ps[1:3], 2))                    # ya pasó la hora de inicio de TODOS
    assert tarde["conjunta"] is None and {f["estado"] for f in tarde["piernas"]} == {"ya_empezo"}                # nada que calcular: no se inventa una probabilidad


def test_analizar_valida_la_entrada(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))
    for malo, msg in (([], "No hay selecciones"), ([{"partido_id": ps[0]["id"], "seleccion": "ZZZ"}], "catálogo"), ([{"partido_id": 99999999, "seleccion": "BTTS:si"}], "no existe")):
        with pytest.raises(bo.ErrorBoleto, match=msg):
            bo.analizar(con, cfg, _cfg(), _momento(), malo)


# ------------------------------------------------------------------ API
@pytest.fixture
def api(bd, cfg, mundo, monkeypatch):  # noqa: F811
    for v in ("APP_PASSWORD", "APP_VIEWER_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("UI_BIND", "127.0.0.1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    main._servicio = main.Servicio(cfg, cadenas(mundo, cfg), dsn=bd, reloj=_momento)
    yield TestClient(main.app), bd
    main._servicio = None


def _b64(x=PNG):
    return base64.b64encode(x).decode()


def test_api_leer_sin_llave_explica_que_falta(api):
    cli, _ = api
    r = cli.post("/boleto/leer", json={"imagen_base64": _b64(), "media_type": "image/png"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert cli.post("/boleto/leer", json={"imagen_base64": "%%%no-base64%%%", "media_type": "image/png"}).status_code == 422


def test_api_leer_y_analizar_de_punta_a_punta(api, monkeypatch):
    cli, bd_ = api
    with db.conectar(bd_) as c:
        ps = _partidos(c, _dia(c))[:3]
    lectura = _lectura(ps)
    monkeypatch.setattr(bo, "leer_captura", lambda imagen, media_type, **k: lectura)                           # Anthropic simulado
    r = cli.post("/boleto/leer", json={"imagen_base64": _b64(), "media_type": "image/png"})
    assert r.status_code == 200 and r.json()["n_ok"] == 3 and r.json()["lectura"] == lectura and r.json()["cuota_total"] == 2.9
    legs = [{k: g[k] for k in ("partido_id", "seleccion", "cuota")} for g in r.json()["legs"] if g["estado"] == "ok"]
    a = cli.post("/boleto/analizar", json={"legs": legs, "cuota_total": 2.9, "monto": 100})
    assert a.status_code == 200
    j = a.json()
    assert j["conjunta"]["n_partidos"] == 3 and 0 < j["conjunta"]["p_conservadora"] <= j["conjunta"]["p_media"] < 1 and len(j["piernas"]) == 3 and j["avisos"]
    assert cli.post("/boleto/analizar", json={"legs": []}).status_code == 422
    assert cli.post("/boleto/analizar", json={"legs": [{"partido_id": 1, "seleccion": "ZZZ"}]}).status_code == 422
    assert cli.post("/boleto/analizar", json={"legs": [{"partido_id": 1, "seleccion": "BTTS:si", "cuota": 0.5}]}).status_code == 422


def test_api_errores_de_anthropic_se_muestran_como_502(api, monkeypatch):
    cli, _ = api
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    def falla(payload, llave):
        raise bo.ErrorBoleto("Anthropic rechazó la llave", 502)
    monkeypatch.setattr(bo, "_enviar_anthropic", falla)
    r = cli.post("/boleto/leer", json={"imagen_base64": _b64(), "media_type": "image/png"})
    assert r.status_code == 502 and "rechazó" in r.json()["detail"] and "sk-x" not in r.text


def test_el_lector_no_puede_leer_ni_analizar_boletos(monkeypatch):
    from golmodel import acceso
    from golmodel.api import seguridad
    env = {"APP_PASSWORD": "admin-1", "APP_VIEWER_PASSWORD": "lector-1", "UI_BIND": "0.0.0.0"}
    tok = f"Bearer {acceso.crear_token(acceso.LECTOR, acceso.secreto('admin-1', 'lector-1', {}))}"
    for ruta in ("/boleto/leer", "/boleto/analizar"):
        assert seguridad.decidir("POST", ruta, tok, env)[0] == 403


def test_el_diario_trae_las_tarjetas_de_mercados_sin_tocar_que_meter(api):
    cli, bd_ = api
    with db.conectar(bd_) as c:
        dia = _dia(c)
    pan = cli.get(f"/panel/{dia.isoformat()}").json()
    con_pred = [p for p in pan["partidos"] if p["prediccion"]]
    assert con_pred
    for p in con_pred:
        m = {x["clave"]: x for x in p["mercados"]}
        assert list(m) == ["ganador", "ambos", "mas15", "mas25", "menos35", "primer_gol"]
        for k, x in m.items():
            assert 0 < x["p"] <= 1 and x["cuota_min"] >= 1 / x["p"] and x["nivel"] in ("ALTA", "MEDIA", "BAJA") and x["pick"]
            assert x["nivel"] == ("ALTA" if x["p"] >= 0.80 else "MEDIA" if x["p"] >= 0.55 else "BAJA")
            if k in ("ambos", "mas15", "mas25", "menos35"):
                assert x["p"] >= 0.5 and x["pick"] in ("Sí", "No")                                        # el lado más probable del mercado
        assert m["ganador"]["pick"] == p["gana"]["favorito"] and m["ganador"]["p"] == pytest.approx(p["gana"]["p"]) and m["primer_gol"]["aprox"] is True
    assert set(pan) >= {"mejores", "valor", "rompe", "v3"}                                                      # «Qué meter» sigue igual
