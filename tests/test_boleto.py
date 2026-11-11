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
    # Formato de Draftea: «GOLES TOTALES 90'» con flechas ↑ (más) y ↓ (menos), incluidas las líneas 0.5 y 4.5 que el motor V3 no cubre
    ("GOLES TOTALES 90'", "↑1.5", None, "OU:1.5:over"), ("GOLES TOTALES 90'", "↓ 3.5", None, "OU:3.5:under"), ("GOLES TOTALES 90'", "↑ 2.5", None, "OU:2.5:over"),
    ("GOLES TOTALES 90'", "↑ 0.5", None, "OU:0.5:over"), ("GOLES TOTALES 90'", "↓ 4.5", None, "OU:4.5:under"), ("GOLES TOTALES", "Menos", 4.5, "OU:4.5:under"),
])
def test_normalizar_a_catalogo(mercado, seleccion, linea, esperado):
    assert bo.normalizar(mercado, seleccion, linea, "Cruz Azul", "América") == (esperado, "")


@pytest.mark.parametrize("mercado,seleccion,linea,fragmento", [
    ("TIROS DE ESQUINA", "Más 7.5", 7.5, "no cubre"), ("Tarjetas totales", "Menos 4.5", 4.5, "no cubre"), ("Goles del jugador", "Más", 0.5, "no cubre"),
    ("PRIMER GOL", "Cruz Azul", None, "no entra"), ("TOTAL DE GOLES", "Más 5.5", 5.5, "fuera del catálogo"), ("TOTAL DE GOLES", "Más", None, "línea"),
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
    assert any("NO entran al cálculo" in a and "no del boleto completo" in a for a in r["avisos"])
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


# ------------------------------------------------------------------ boleto LARGO (Draftea: ~24 selecciones): lectura en tramos, sin cortarse
def _lectura_json(n, desde=0):
    return '{"partidos":[' + ",".join(
        f'{{"local":"E{i}","visita":"F{i}","selecciones":[{{"mercado_texto":"GOLES TOTALES 90\'","seleccion_texto":"Más","linea":1.5,"cuota":1.2}}]}}' for i in range(desde, desde + n)) + '],"monto":20,"cuota_total":90.51}'


def test_leer_captura_en_tramos_manda_todas_las_imagenes_en_una_peticion_y_pide_espacio_de_sobra():
    visto = {}

    def falso(payload, llave):
        visto["p"] = payload
        return _respuesta(_lectura_json(24))
    r = bo.leer_captura([PNG, PNG, PNG], "image/png", enviar=falso, env={"ANTHROPIC_API_KEY": "k"})
    c = visto["p"]["messages"][0]["content"]
    assert [x["type"] for x in c] == ["image", "image", "image", "text"]                      # los 3 tramos, en orden, y el texto al final
    assert "tramos CONSECUTIVOS" in c[-1]["text"] and "↑" in c[-1]["text"] and "Momio" in c[-1]["text"]
    assert visto["p"]["max_tokens"] == bo.MAX_TOKENS and bo.MAX_TOKENS >= 6000                  # con 1800 un boleto de 24 selecciones se cortaba a la mitad
    assert len(r["partidos"]) == 24 and r["cuota_total"] == 90.51
    with pytest.raises(bo.ErrorBoleto, match="máximo"):
        bo.leer_captura([PNG] * (bo.MAX_IMAGENES + 1), "image/png", enviar=falso, env={"ANTHROPIC_API_KEY": "k"})
    with pytest.raises(bo.ErrorBoleto, match="vacía"):
        bo.leer_captura([], "image/png", enviar=falso, env={"ANTHROPIC_API_KEY": "k"})


def test_si_la_respuesta_se_corta_por_longitud_se_dice_la_causa_real_no_intenta_de_nuevo():
    cortada = {"content": [{"type": "text", "text": _lectura_json(24)[:900]}], "stop_reason": "max_tokens"}
    with pytest.raises(bo.ErrorBoleto, match="dos capturas"):
        bo.leer_captura(PNG, "image/png", enviar=lambda p, k: cortada, env={"ANTHROPIC_API_KEY": "k"})
    rota = {"content": [{"type": "text", "text": _lectura_json(3)[:200] + "}"}], "stop_reason": "end_turn"}
    with pytest.raises(bo.ErrorBoleto, match="incompleta"):                                     # un JSON roto SIN corte por longitud: el mensaje de siempre
        bo.leer_captura(PNG, "image/png", enviar=lambda p, k: rota, env={"ANTHROPIC_API_KEY": "k"})


def test_los_tramos_con_solapamiento_no_duplican_selecciones_ni_partidos():
    sel = lambda m, s, ln=None: {"mercado_texto": m, "seleccion_texto": s, "linea": ln, "cuota": 1.2}      # noqa: E731
    lect = {"partidos": [
        {"local": "TUR", "visita": "BEL", "selecciones": [sel("GOLES TOTALES 90'", "Más", 1.5)]},
        {"local": "CZE", "visita": "ESP", "selecciones": [sel("GOLES TOTALES 90'", "Más", 1.5)]},
        {"local": "CZE", "visita": "ESP", "selecciones": [sel("GOLES TOTALES 90'", "Más", 1.5), sel("AMBOS ANOTAN", "Sí")], "local_completo": "Czech Republic"},   # el mismo partido en el tramo siguiente
        {"local": "tur", "visita": "bel", "selecciones": [sel("GOLES TOTALES 90'", "Más", 1.5)]}]}                                              # repetido tal cual (otra capitalización)
    r = bo._fusionar(lect)
    assert [(b["local"], b["visita"]) for b in r["partidos"]] == [("TUR", "BEL"), ("CZE", "ESP")]
    assert len(r["partidos"][0]["selecciones"]) == 1 and len(r["partidos"][1]["selecciones"]) == 2           # la repetida se fue; la nueva (ambos anotan) se conserva
    assert r["partidos"][1]["local_completo"] == "Czech Republic"
    distintas = bo._fusionar({"partidos": [{"local": "A", "visita": "B", "selecciones": [sel("GOLES TOTALES", "Más", 1.5), sel("GOLES TOTALES", "Más", 2.5), sel("GOLES TOTALES", "Menos", 1.5)]}]})
    assert len(distintas["partidos"][0]["selecciones"]) == 3                                                # líneas o lados distintos NO se confunden con repetidas


def test_api_acepta_tramos_y_rechaza_lo_vacio(api, monkeypatch):
    cli, _ = api
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    visto = {}

    def falso(payload, llave):
        visto["n"] = sum(1 for x in payload["messages"][0]["content"] if x["type"] == "image")
        return _respuesta(LECTURA_OK)
    monkeypatch.setattr(bo, "_enviar_anthropic", falso)
    r = cli.post("/boleto/leer", json={"imagenes_base64": [_b64(), _b64(), _b64()], "media_type": "image/png"})
    assert r.status_code == 200 and visto["n"] == 3
    assert cli.post("/boleto/leer", json={"media_type": "image/png"}).status_code == 422                     # sin ninguna imagen
    assert cli.post("/boleto/leer", json={"imagenes_base64": [], "media_type": "image/png"}).status_code == 422
    assert cli.post("/boleto/leer", json={"imagenes_base64": [_b64()] * (bo.MAX_IMAGENES + 1), "media_type": "image/png"}).status_code == 422
    assert cli.post("/boleto/leer", json={"imagenes_base64": ["%%%no%%%"], "media_type": "image/png"}).status_code == 422


# ------------------------------------------------------------------ bloque independiente: partidos SOLO MERCADO y líneas 0.5 / 4.5
def _matriz(con, pid):
    from golmodel.integracion import candidatas as cd
    return cd._matriz_decision(cd._prediccion(con, pid, _momento()))


def _clonar_a_mercado(con, p, n):
    """Partido nuevo en una competición SOLO MERCADO (`MK:`) con la predicción de `p` copiada (en producción esa matriz sale de las cuotas del mercado)."""
    from golmodel import repositorio as repo
    liga = repo.asegurar_liga_mercado(con, "test_selecciones", "Selecciones de prueba")
    l = con.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, '') RETURNING id", (f"Mercado Local {n}",)).fetchone()["id"]
    v = con.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, '') RETURNING id", (f"Mercado Visita {n}",)).fetchone()["id"]
    pid = con.execute("INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id, estado) SELECT %s, temporada, inicio, %s, %s, 'programado' FROM partidos WHERE id = %s RETURNING id",
                      (liga["id"], l, v, p["id"])).fetchone()["id"]

    def cols(tabla, excluir):
        return [r["column_name"] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position", (tabla,))
                if r["column_name"] not in excluir]
    c1 = ", ".join(cols("predicciones", {"id", "partido_id"}))
    fuente = con.execute("SELECT id FROM predicciones WHERE partido_id = %s AND origen = 'produccion' ORDER BY creada_en DESC, id DESC LIMIT 1", (p["id"],)).fetchone()["id"]
    nuevo = con.execute(f"INSERT INTO predicciones (partido_id, {c1}) SELECT %s, {c1} FROM predicciones WHERE id = %s RETURNING id", (pid, fuente)).fetchone()["id"]
    c2 = ", ".join(cols("predicciones_seleccion", {"prediccion_id"}))
    con.execute(f"INSERT INTO predicciones_seleccion (prediccion_id, {c2}) SELECT %s, {c2} FROM predicciones_seleccion WHERE prediccion_id = %s", (nuevo, fuente))
    return pid


def test_solo_mercado_y_lineas_extra_se_evaluan_aparte_con_su_matriz_y_se_combinan_con_la_conjunta(con, cfg):  # noqa: F811
    import numpy as np
    ps = _partidos(con, _dia(con))
    mod_cat, mod_extra = ps[0], ps[1]                                                                        # modelados: uno con línea del catálogo, otro con 0.5
    m1, m2 = _clonar_a_mercado(con, ps[2], 1), _clonar_a_mercado(con, ps[3], 2)                              # dos partidos solo mercado
    legs = [{"partido_id": mod_cat["id"], "seleccion": "OU:1.5:over", "cuota": 1.3}, {"partido_id": mod_extra["id"], "seleccion": "OU:0.5:over", "cuota": 1.05},
            {"partido_id": m1, "seleccion": "1X2:1", "cuota": 1.8}, {"partido_id": m2, "seleccion": "OU:4.5:under", "cuota": 1.1}]
    r = bo.analizar(con, cfg, _cfg(), _momento(), legs, cuota_total=4.0)
    por = {f["partido_id"]: f for f in r["piernas"]}
    assert por[mod_cat["id"]].get("bloque") is None and por[mod_cat["id"]]["estado"] == "ok"                # catálogo + modelado: motor V3
    for pid in (mod_extra["id"], m1, m2):
        assert por[pid]["estado"] == "ok" and por[pid]["bloque"] == "independiente" and "independencia" in por[pid]["nota"]
    mx = _matriz(con, mod_extra["id"])
    i, j = np.indices(mx.shape)
    assert por[mod_extra["id"]]["p_mostrar"] == pytest.approx(float(mx[(i + j) > 0.5].sum()))                # «más de 0.5» = 1 − P(0-0), recomputado aparte
    assert por[m2]["p_mostrar"] == pytest.approx(float(_matriz(con, m2)[(i + j) < 4.5].sum()))
    assert "solo mercado" in por[m1]["nota"].lower() and por[mod_extra["id"]]["nota"].startswith("Línea fuera del catálogo")
    assert por[m1]["estado"] == "ok" and por[m1]["p_modelo"] is None                                          # sin modelo propio: no se presenta como probabilidad del modelo
    prod = por[mod_extra["id"]]["p_mostrar"] * por[m1]["p_mostrar"] * por[m2]["p_mostrar"]
    assert r["independiente"]["n_partidos"] == 3 and r["independiente"]["p_producto"] == pytest.approx(prod)
    c, cm = r["conjunta"], r["completo"]
    assert c["n_partidos"] == 1 and cm["n_partidos"] == 4
    assert cm["p_media"] == pytest.approx(c["p_media"] * prod) and cm["p_conservadora"] == pytest.approx(c["p_conservadora"] * prod)
    assert cm["ev_media"] == pytest.approx(cm["p_media"] * 4.0 - 1) and cm["ev_conservador"] == pytest.approx(cm["p_conservadora"] * 4.0 - 1)
    assert cm["cuota_justa"] == pytest.approx(1 / cm["p_media"])
    assert any("SUPONIENDO que son independientes" in a for a in r["avisos"]) and not any("NO entran al cálculo" in a for a in r["avisos"])


def test_boleto_solo_de_mercado_no_inventa_cota_conservadora_ni_conjunta_calibrada(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))
    ids = [_clonar_a_mercado(con, ps[k], k) for k in range(3)]
    r = bo.analizar(con, cfg, _cfg(), _momento(), [{"partido_id": i, "seleccion": "OU:1.5:over", "cuota": 1.3} for i in ids], cuota_total=2.2)
    assert r["conjunta"] is None                                                                           # no hay conjunta calibrada: no se simula una
    cm = r["completo"]
    assert cm["p_conservadora"] is None and cm["ev_conservador"] is None and cm["p_media"] == pytest.approx(r["independiente"]["p_producto"])
    assert cm["ev_media"] == pytest.approx(cm["p_media"] * 2.2 - 1)


def test_sugerencias_del_bloque_independiente_son_numeros_de_la_matriz_ordenados(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))
    ids = [_clonar_a_mercado(con, ps[k], k) for k in range(3)]
    r = bo.analizar(con, cfg, _cfg(), _momento(), [{"partido_id": i, "seleccion": "OU:3.5:over", "cuota": 2.2} for i in ids], cuota_total=10.0)
    sug = r["independiente"]["sugerencias"]
    assert sug and len(sug) <= bo.MAX_SUGERENCIAS and [s["sube_x"] for s in sug] == sorted((s["sube_x"] for s in sug), reverse=True) and all(s["sube_x"] > 1 for s in sug)
    pp = {f["partido"]: f["p_mostrar"] for f in r["piernas"]}
    for s in sug:
        if s["tipo"] == "quitar":
            assert s["sube_x"] == pytest.approx(1 / pp[s["partido"]])                                          # sin ese partido el producto sube 1/p (independencia)
        else:
            assert s["de"] == "OU:3.5:over" and s["a"] in ("OU:2.5:over", "OU:1.5:over", "OU:0.5:over") and s["p_pierna_despues"] > s["p_pierna_antes"]
            assert s["sube_x"] == pytest.approx(s["p_pierna_despues"] / s["p_pierna_antes"]) and s["cuota_justa_nueva"] == pytest.approx(1 / s["p_pierna_despues"])


def test_solo_mercado_sin_prediccion_dice_que_faltan_cuotas_y_no_calcula(con, cfg):  # noqa: F811
    from datetime import timedelta
    ps = _partidos(con, _dia(con))
    pid = _clonar_a_mercado(con, ps[0], 9)
    antes = _momento() - timedelta(days=60)                                                                  # antes de que existiera cualquier predicción
    r = bo.analizar(con, cfg, _cfg(), antes, [{"partido_id": pid, "seleccion": "OU:1.5:over"}])
    f = r["piernas"][0]
    assert f["estado"] == "sin_modelo" and "Actualizar solo cuotas" in f["motivo"] and f["p_mostrar"] is None
    assert r["conjunta"] is None and r["independiente"] is None and r["completo"] is None


# ------------------------------------------------------------------ fallas halladas en auditoría (cada prueba FALLA con el código anterior)
def test_el_ev_usa_la_cuota_de_los_partidos_que_si_entraron_no_la_total_del_boleto(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))
    con.execute("UPDATE partidos SET estado = 'cancelado' WHERE id = %s", (ps[0]["id"],))
    r = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 3, cuota=1.3), cuota_total=2.0)              # 3 × 1.3 ≈ 2.2 pagaría 2.0; uno quedó fuera
    c = r["conjunta"]
    assert c["n_partidos"] == 2 and c["cuota_parcial"] is True and c["cuota_total"] == pytest.approx(1.3 * 1.3)
    assert c["ev_media"] == pytest.approx(c["p_media"] * 1.69 - 1) and c["ev_conservador"] == pytest.approx(c["p_conservadora"] * 1.69 - 1)   # antes: × 2.0 (EV inflado)
    assert all(x["cuota"] == pytest.approx(1.3) for x in c["sin_cada_partido"])                          # quitar uno de los dos deja la cuota del otro
    assert any("no con la cuota total del boleto" in a for a in r["avisos"])
    sin_cuotas = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps, 3, cuota=None), cuota_total=2.0)    # sin cuota por selección no se puede saber la del subconjunto
    assert "ev_media" not in sin_cuotas["conjunta"] and "cuota_total" not in sin_cuotas["conjunta"]
    assert any("Falta la cuota de algún partido" in a for a in sin_cuotas["avisos"])
    completo = bo.analizar(con, cfg, _cfg(), _momento(), _legs(ps[1:], 3, cuota=1.3), cuota_total=2.0)   # todo entró: se usa la cuota total tal cual
    assert completo["conjunta"]["cuota_total"] == 2.0 and completo["conjunta"]["cuota_parcial"] is False
    assert not any("no con la cuota total" in a for a in completo["avisos"])


def test_en_un_boleto_mixto_cada_bloque_usa_su_cuota(con, cfg):  # noqa: F811
    ps = _partidos(con, _dia(con))
    m1 = _clonar_a_mercado(con, ps[2], 1)
    legs = [{"partido_id": ps[0]["id"], "seleccion": "OU:1.5:over", "cuota": 1.3}, {"partido_id": ps[1]["id"], "seleccion": "OU:1.5:over", "cuota": 1.4},
            {"partido_id": m1, "seleccion": "OU:1.5:over", "cuota": 1.25}]
    r = bo.analizar(con, cfg, _cfg(), _momento(), legs, cuota_total=2.2)
    c, cm = r["conjunta"], r["completo"]
    assert c["n_partidos"] == 2 and c["cuota_parcial"] is True and c["cuota_total"] == pytest.approx(1.3 * 1.4)       # el motor V3 solo cubre 2 de 3: NO se usa 2.2
    assert c["ev_media"] == pytest.approx(c["p_media"] * 1.82 - 1)
    assert cm["n_partidos"] == 3 and cm["cuota_parcial"] is False and cm["cuota_total"] == 2.2                       # el total sí cubre los 3
    assert cm["ev_media"] == pytest.approx(cm["p_media"] * 2.2 - 1)


def test_una_respuesta_cortada_nunca_se_da_por_buena_aunque_el_json_sea_valido_o_no_haya_json():
    valida_pero_cortada = {"content": [{"type": "text", "text": _lectura_json(3)}], "stop_reason": "max_tokens"}
    with pytest.raises(bo.ErrorBoleto, match="dos capturas"):                                           # podrían faltar filas: no se analiza un boleto incompleto
        bo.leer_captura(PNG, "image/png", enviar=lambda p, k: valida_pero_cortada, env={"ANTHROPIC_API_KEY": "k"})
    sin_llaves = {"content": [{"type": "text", "text": "Voy a leer el boleto"}], "stop_reason": "max_tokens"}
    with pytest.raises(bo.ErrorBoleto, match="dos capturas"):
        bo.leer_captura(PNG, "image/png", enviar=lambda p, k: sin_llaves, env={"ANTHROPIC_API_KEY": "k"})


def test_la_api_acepta_un_boleto_de_25_selecciones(api):
    cli, _ = api
    legs = [{"partido_id": 90_000_000 + i, "seleccion": "OU:1.5:over", "cuota": 1.2} for i in range(25)]
    r = cli.post("/boleto/analizar", json={"legs": legs, "cuota_total": 90.51})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and "no existe" in r.json()["detail"]      # pasó la validación de tamaño; falla porque esos partidos no existen
    r41 = cli.post("/boleto/analizar", json={"legs": legs + legs[:16]})
    assert r41.status_code == 422 and not isinstance(r41.json()["detail"], str)                                    # 41: rechazado por tamaño
    extra = cli.post("/boleto/analizar", json={"legs": [{"partido_id": 90_000_001, "seleccion": "OU:4.5:under"}]})
    assert "no existe" in extra.json()["detail"]                                                                    # la línea 4.5 se acepta como selección válida
