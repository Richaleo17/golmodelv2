"""Panel «Diario» y «Qué meter»: textos, primer gol, confianza, riesgo, parlays por perfil y α agrupado."""
import math

import pytest

from golmodel.config import cargar
from golmodel.servicios import sugerencias as s
from golmodel.servicios.modelo import alpha_para

CFG = cargar()
CS = CFG["sugerencias"]


def test_texto_jugada_en_lenguaje_de_apuestas():
    assert s.texto_jugada("DC:1X", "Napoli", "Lazio")[0] == "Napoli o empate"
    assert s.texto_jugada("AH:visitante:+1.5", "Napoli", "Lazio") == (
        "Lazio +1.5", "gana, empata o pierde por 1 gol (no pierde por 2 o más)")
    assert s.texto_jugada("OU:2.5:over", "A", "B")[0] == "Más de 2.5 goles"
    assert s.texto_jugada("OU:1.5:under", "A", "B")[0] == "Menos de 1.5 goles"
    assert s.texto_jugada("BTTS:si", "A", "B")[0] == "Ambos anotan"
    assert all(s.texto_jugada(k, "A", "B")[0] != k for k in s.SUGERIBLES)           # todas tienen texto


def test_primer_gol_es_coherente():
    pl, pv, p0 = s.primer_gol(1.6, 1.1)
    assert pl + pv + p0 == pytest.approx(1.0) and pl > pv
    a, b, _ = s.primer_gol(1.3, 1.3)
    assert a == pytest.approx(b)
    assert s.primer_gol(2.5, 1.1)[0] > s.primer_gol(1.6, 1.1)[0]                      # más λ, más probable
    assert s.primer_gol(0.4, 0.3)[2] > s.primer_gol(2.0, 2.0)[2]                      # pocos goles → más 0-0


def _conf(**kw):
    base = dict(peso_min=30, mercado_ok=True, consenso=False, p_fav_modelo=0.60, p_fav_mercado=0.60,
                alpha_calibrado=True, crs_alerta="BAJA", ajuste_bajas=0.0)
    return s.calcular_confianza(**{**base, **kw})


def test_confianza_sube_con_respaldo_y_baja_con_dudas():
    buena, _ = _conf()
    assert buena >= 8
    assert _conf(mercado_ok=False)[0] < buena - 1.5                    # sin cuotas de referencia
    assert _conf(p_fav_modelo=0.45)[0] < buena                           # modelo y mercado discrepan
    assert _conf(crs_alerta="ALTA")[0] < _conf(crs_alerta="BAJA")[0]
    assert _conf(peso_min=2)[0] < buena
    peor, razones = _conf(peso_min=2, mercado_ok=False, crs_alerta="ALTA", alpha_calibrado=False, ajuste_bajas=-0.1)
    assert 0 <= peor <= 10 and peor < 3 and razones
    assert _conf(peso_min=100, p_fav_modelo=0.6, p_fav_mercado=0.6, crs_alerta="BAJA")[0] <= 10


def _p(pid, p, conf=8.0, hora="09:00", sel="DC:1X", umbral=0.04):
    return s.Pierna(partido_id=pid, liga="EPL", partido=f"L{pid} vs V{pid}", hora=hora, seleccion=sel, jugada=f"J{pid}",
                    detalle="", p=p, cuota_justa=1 / p, cuota_min=(1 + umbral) / p, umbral=umbral, derivada=False,
                    confianza=conf, categoria="segura", puntaje=s._puntaje(p, conf))


def test_armar_perfil_elige_la_combinacion_mas_probable_en_el_rango():
    piernas = [_p(1, 0.80), _p(2, 0.78), _p(3, 0.76), _p(4, 0.74), _p(5, 0.72)]
    par = s.armar_perfil(piernas, CS["perfiles"]["seguro"], CS)
    assert par is not None and par["n"] in (3, 4)
    assert len({x["partido_id"] for x in par["piernas"]}) == par["n"]
    assert par["p"] == pytest.approx(math.prod(x["p"] for x in par["piernas"]))
    assert 1.8 <= par["cuota_justa"] <= 3.5
    assert par["cuota_min"] == pytest.approx((1 + 0.04) / par["p"])
    # Debe ser la MÁS probable entre todas las combinaciones válidas
    import itertools
    validas = [math.prod(x.p for x in c) for k in (3, 4) for c in itertools.combinations(piernas, k)
               if 1.8 <= 1 / math.prod(x.p for x in c) <= 3.5]
    assert par["p"] == pytest.approx(max(validas))


def test_armar_perfil_no_inventa_si_no_alcanza_y_no_repite_partido():
    solo_uno = [_p(1, 0.8, sel="DC:1X"), _p(1, 0.78, sel="OU:1.5:over"), _p(1, 0.75, sel="BTTS:si")]
    assert s.armar_perfil(solo_uno, CS["perfiles"]["seguro"], CS) is None          # 3 jugadas, 1 solo partido
    assert s.armar_perfil([_p(i, 0.95) for i in range(5)], CS["perfiles"]["seguro"], CS) is None  # fuera de banda
    amb = s.armar_perfil([_p(i, 0.55) for i in range(8)], CS["perfiles"]["ambicioso"], CS)
    assert amb is None or (amb["cuota_justa"] >= 8.0 and amb["n"] >= 4)


def _info(**kw):
    base = {"partido_id": 1, "liga": "EPL", "hora": "09:00", "local": "A", "visitante": "B",
            "gana": {"favorito": "A", "p": 0.60, "empate": 0.22, "rival": "B", "p_rival": 0.18, "margen": 0.42},
            "crs": {"total": 20, "alerta": "BAJA", "veto": False}, "confianza": 8.0,
            "p_fav_modelo": 0.60, "p_fav_mercado": 0.61, "_piernas": [_p(1, 0.8)]}
    base.update(kw)
    return base


def test_rompe_parlays_solo_con_senales_reales():
    assert s.riesgo_del_favorito(_info(), CFG) is None                                   # partido sano
    r = s.riesgo_del_favorito(_info(crs={"total": 80, "alerta": "ALTA", "veto": True}), CFG)
    assert r and any("CRS" in x for x in r["razones"])
    r = s.riesgo_del_favorito(_info(p_fav_modelo=0.50, p_fav_mercado=0.62), CFG)          # modelo ≪ mercado
    assert r and any("más débil que el mercado" in x for x in r["razones"])
    r = s.riesgo_del_favorito(_info(gana={"favorito": "A", "p": 0.42, "empate": 0.33, "rival": "B", "p_rival": 0.25,
                                          "margen": 0.17}), CFG)
    assert r and any("cerrado" in x for x in r["razones"])
    r = s.riesgo_del_favorito(_info(p_fav_modelo=0.50, p_fav_mercado=0.62,
                                    _piernas=[_p(1, 0.80, sel="DC:1X"), _p(1, 0.40, sel="AH:visitante:+1.5")]), CFG)
    assert r["alternativa"]["jugada"] == "J1"                                              # alternativa probable
    r = s.riesgo_del_favorito(_info(p_fav_modelo=0.50, p_fav_mercado=0.62, _piernas=[_p(1, 0.40, sel="DC:1X")]), CFG)
    assert r["alternativa"] is None                                                       # no se ofrece una de 40%


def test_categoria_depende_de_probabilidad_y_confianza():
    assert s._categoria(0.80, 8.0, CS) == "segura"
    assert s._categoria(0.80, 3.0, CS) == "equilibrada"          # poca confianza: no se llama «segura»
    assert s._categoria(0.60, 9.0, CS) == "equilibrada"
    assert s._categoria(0.40, 9.0, CS) == "arriesgada"


def test_alpha_con_respaldo_agrupado_para_ligas_chicas():
    hp = {"alpha": {"EPL": {"1X2": {"valor": 0.10}, "OU:2.5": {"valor": 0.0}},
                    "_global": {"1X2": {"valor": 0.20}, "OU:2.5": {"valor": 0.05}}}}
    assert alpha_para(hp, CFG, "EPL", "1X2") == (0.10, True)                              # propio y calibrado
    assert alpha_para(hp, CFG, "MX1", "1X2") == (0.20, False)                             # agrupado, no propio
    assert alpha_para(hp, CFG, "MX1", "OU:2.5") == (0.05, False)
    f = CFG["mercado"]["factor_alpha_no_calibrado"]
    assert alpha_para(hp, CFG, "MX1", "BTTS") == (pytest.approx(f * 0.20, abs=1e-4), False)
    assert alpha_para({"alpha": {}}, CFG, "MX1", "1X2") == (CFG["mercado"]["alpha_por_defecto"], False)
    assert CFG["mercado"]["alpha_por_defecto"] <= 0.25                                    # el mercado manda por defecto


def test_ticket_de_cinco_busca_pago_alto_y_dice_la_probabilidad_real():
    t = CS["ticket"]
    piernas = [_p(i, p) for i, p in enumerate([0.62, 0.58, 0.55, 0.52, 0.50, 0.48, 0.45], 1)]
    tickets = s.armar_tickets(piernas, CS)
    assert tickets and len(tickets) <= t["cuantos"]
    for k in tickets:
        assert k["n"] == 5 and len({x["partido_id"] for x in k["piernas"]}) == 5
        assert t["cuota_min"] <= k["cuota_justa"] <= t["cuota_max"]
        assert k["p"] == pytest.approx(math.prod(x["p"] for x in k["piernas"]))     # P real, sin maquillaje
        assert k["uno_de_cada"] == pytest.approx(1 / k["p"])
    assert s.armar_tickets([_p(i, 0.95) for i in range(6)], CS) == []               # no hay pago alto → no inventa


def test_credibilidad_crs_reordena_pero_no_cambia_la_probabilidad():
    base = [_p(i, 0.55) for i in range(1, 8)]
    for x in base[:5]:
        x.crs_factor, x.crs_alerta = 0.88, "ALTA"                                   # los 5 primeros: favorito en riesgo
    k = s.armar_tickets(base, CS)[0]
    ids = {x["partido_id"] for x in k["piernas"]}
    assert len(ids & {6, 7}) == 2                                                      # prefiere partidos sin riesgo CRS
    assert all(x["p"] == 0.55 for x in k["piernas"])                                   # p mostrada intacta
    assert "Ticket 1" in s.texto_panel({"fecha": "x", "mejores": [], "rompe": [], "parlays": {}, "tickets": [k]})


def test_porque_dice_lo_que_los_datos_realmente_dicen():
    r = s._porque("Gana A", 0.62, 0.50, 0.68, False, 7.0, [], "ALTA", 75.0, 0.88, {"local": "GGPEG"}, {"local": "A"}, "local")
    txt = " | ".join(r)
    assert "Modelo 50%" in txt and "mercado sin margen 68%" in txt and "α por defecto" in txt
    assert "menos que el mercado" in txt and "CRS 75.0" in txt and "GGPEG" in txt


def test_discrepancia_por_pocos_datos_no_acusa_al_favorito():
    """Caso real (Hoffenheim–Schalke): el modelo dudaba porque el rival casi no tenía historial; eso no es
    fragilidad del favorito, así que no debe marcarlo como «rompe parlays»."""
    dudoso = _info(p_fav_modelo=0.57, p_fav_mercado=0.78, gana={**_info()["gana"], "p": 0.78}, peso_min=9.5)
    assert s.riesgo_del_favorito(dudoso, CFG) is None
    con_datos = _info(p_fav_modelo=0.57, p_fav_mercado=0.78, gana={**_info()["gana"], "p": 0.78}, peso_min=40.0)
    assert "más débil que el mercado" in " ".join(s.riesgo_del_favorito(con_datos, CFG)["razones"])


def test_listas_de_parlays_mas_seguros_traen_tasa_real():
    piernas = [_p(i, p) for i, p in enumerate([0.90, 0.88, 0.85, 0.83, 0.80, 0.79, 0.74, 0.72], 1)]
    for nombre, perfil in CS["mas_seguros"].items():
        lst = s._combinaciones(piernas, perfil, CS, perfil["cuantos"])
        assert lst, nombre
        for P, combo in lst:
            k = s._ficha(P, combo)
            assert perfil["piernas_min"] <= k["n"] <= perfil["piernas_max"]
            assert k["p"] == pytest.approx(math.prod(x["p"] for x in k["piernas"]))
            assert perfil["cuota_min"] <= k["cuota_justa"] <= perfil["cuota_max"]
        ps = [P for P, _ in lst]
