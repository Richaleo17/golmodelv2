"""Parlays de «Qué meter» (3-5), «Soñadoras» (6-10) y Boletos: menú abierto, Cerebro 2 por pierna, hasta 3 días, sin repetir partidos."""
from datetime import date, datetime, timedelta, timezone

import pytest

from golmodel.config import cargar
from golmodel.servicios import parlays_usuario as pu

CFG = cargar()
AHORA = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)      # 06:00 en CDMX
DIA = date(2026, 11, 14)


def _p(pid, gana, pa1, conf=8.0, hora="15:00", estado="programado", pred=True):
    return {"partido_id": pid, "liga": "EPL", "liga_nombre": "Premier", "local": f"L{pid}", "visitante": f"V{pid}", "hora": hora,
            "estado": estado, "prediccion": pred, "confianza": conf, "fuente": "mercado + modelo",
            "gana": {"favorito": f"L{pid}", "p": gana}, "primer_gol": {"equipo": f"L{pid}", "p": pa1}}


def test_elige_la_mas_probable_y_filtra():
    pan = {"partidos": [_p(1, 0.70, 0.74), _p(2, 0.80, 0.60), _p(3, 0.50, 0.52), _p(4, 0.9, 0.8, conf=2.0),
                        _p(5, 0.9, 0.8, hora="05:00"), _p(6, 0.9, 0.8, estado="finalizado"), _p(7, 0.9, 0.8)],
           "rompe": [{"partido_id": 7}]}
    pi = pu.piernas_de_panel(pan, DIA, AHORA, CFG)
    assert {x["partido_id"] for x in pi} == {1, 2}                 # 3 bajo p_min, 4 confianza baja, 5 ya empezó, 6 jugado, 7 rompe
    por = {x["partido_id"]: x for x in pi}
    assert por[1]["tipo"] == "primer_gol" and por[1]["jugada"].startswith("PA(1)") and por[2]["tipo"] == "ganador"
    assert por[1]["sel"] == "PA1:local" and por[2]["sel"] == "1X2:1"
    assert [a["jugada"] for a in por[1]["alternativas"]] == ["Gana L1"]          # la otra jugada del partido, para cambiarla en la casa
    assert por[1]["dia_texto"] == "sáb 14/11"


def test_opciones_sin_repetir_y_probabilidad():
    piernas = [{"partido_id": k, "p": 0.9 - k * 0.02, "dia": "2026-11-14", "inicio": f"2026-11-14T{10 + k:02d}:00"} for k in range(12)]
    ops, rep = pu.armar_opciones(piernas, [3, 4, 5])
    assert [o["n"] for o in ops] == [3, 4, 5] and not rep
    ids = [x["partido_id"] for o in ops for x in o["piernas"]]
    assert len(ids) == len(set(ids))
    assert abs(ops[0]["p"] - 0.9 * 0.88 * 0.86) < 1e-9 and ops[0]["p"] > ops[1]["p"]
    son, rep2 = pu.armar_opciones(piernas, [6, 8, 10])
    assert [o["n"] for o in son] == [6, 8, 10] and rep2              # 24 piernas pedidas con 12 partidos: repite y avisa
    assert pu.armar_opciones(piernas[:2], [3])[0] == []              # ni repitiendo alcanza


def test_mismo_equipo_a_la_misma_hora_se_omite():
    a = {"partido_id": 1, "partido": "Austria vs Iceland", "inicio": "t", "p": 0.64, "dia_texto": "sáb", "hora": "13:45"}
    b = {"partido_id": 2, "partido": "Austria vs Republic of Ireland", "inicio": "t", "p": 0.63, "dia_texto": "sáb", "hora": "13:45"}
    out, av = pu.quitar_duplicados([b, a])
    assert [x["partido_id"] for x in out] == [1] and len(av) == 1


def test_boletos_de_7_por_banda_sin_repetir_partidos():
    from golmodel.servicios.parlays_usuario import armar_boletos
    cands = []
    for i in range(30):
        for tipo, p in (("ganador", 0.6 + (i % 10) * 0.035), ("mas15", 0.72 + (i % 5) * 0.03)):
            cands.append({"partido_id": i, "p": p, "jugada": f"{tipo} {i}", "inicio": f"2026-11-18T{10 + i % 10}:00", "dia": "2026-11-18"})
    bandas = [{"titulo": "seguro", "p_min": 0.75, "p_max": 0.92}, {"titulo": "balanceado", "p_min": 0.66, "p_max": 0.85},
              {"titulo": "paga", "p_min": 0.58, "p_max": 0.75}]
    b = armar_boletos(cands, bandas, 7)
    assert [x["titulo"] for x in b] == ["seguro", "balanceado", "paga"]
    assert all(x["n"] == 7 for x in b)
    ids = [p["partido_id"] for x in b for p in x["piernas"]]
    assert len(ids) == len(set(ids))                                   # ningún partido en dos boletos
    for x, band in zip(b, bandas):
        assert all(band["p_min"] <= p["p"] <= band["p_max"] for p in x["piernas"])
        assert x["p"] == pytest.approx(__import__("math").prod(p["p"] for p in x["piernas"]))
    assert b[0]["p"] > b[2]["p"]                                       # el seguro es más probable que el que paga más
    assert armar_boletos(cands[:6], bandas, 7) == []                  # menos de 7 partidos: no se arma


def _pm(pid, mercados, gana=0.6, empate=0.25, lado="local", fuente="mercado + modelo"):
    x = _p(pid, gana, 0.5)
    x.update(fuente=fuente, mercados=mercados)
    x["gana"].update(empate=empate, lado=lado)
    return x


def _m(clave, pick, p):
    return {"clave": clave, "titulo": {"mas15": "Más de 1.5 goles", "menos35": "Menos de 3.5 goles", "ambos": "Ambos anotan",
                                       "corners": "Córners"}.get(clave, clave), "pick": pick, "p": p}


def test_menu_abierto_toma_la_mas_segura_que_pague():
    pan = {"partidos": [
        _pm(1, [_m("ganador", "L1", 0.60), _m("mas15", "Sí", 0.84), _m("corners", "Más de 7.5", 0.90)]),   # DC 0.85 > Más 1.5; córners no entra
        _pm(2, [_m("ganador", "L2", 0.90), _m("mas15", "Sí", 0.96)], empate=0.07),                         # DC 0.97 y Más 1.5 0.96 > p_max
        _pm(3, [_m("ganador", "V3", 0.55), _m("menos35", "Sí", 0.70)], empate=0.25, lado="visitante")]}
    por = {x["partido_id"]: x for x in pu.piernas_de_panel(pan, DIA, AHORA, CFG)}
    assert por[1]["tipo"] == "dc" and por[1]["sel"] == "DC:1X" and por[1]["p"] == pytest.approx(0.85)
    assert por[2]["tipo"] == "ganador" and por[2]["p"] == 0.90                                              # la que todavía paga (≥ 1.08)
    assert por[3]["tipo"] == "dc" and por[3]["sel"] == "DC:X2" and por[3]["jugada"] == "Doble oportunidad: L3 o empate"
    assert all(a["jugada"] != por[1]["jugada"] for a in por[1]["alternativas"]) and len(por[1]["alternativas"]) == 2


def test_cerebro2_veta_y_ordena_por_la_conservadora():
    c1 = {"partido_id": 1, "tipo": "ganador", "jugada": "Gana A", "p": 0.80, "c2": {"estado": "vetada", "p": 0.70}}
    c2 = {"partido_id": 1, "tipo": "mas15", "jugada": "Más de 1.5 goles: Sí", "p": 0.78, "c2": {"estado": "confirmada", "p": 0.79}}
    c3 = {"partido_id": 2, "tipo": "ganador", "jugada": "Gana B", "p": 0.84, "c2": {"estado": "con_reservas", "p": 0.78}}
    c4 = {"partido_id": 2, "tipo": "mas15", "jugada": "Más de 1.5 goles: Sí", "p": 0.82, "c2": {"estado": "confirmada", "p": 0.82}}
    por = {x["partido_id"]: x for x in pu.elegir_por_partido([c1, c2, c3, c4], CFG)}
    assert por[1]["jugada"] == "Más de 1.5 goles: Sí" and all(a["jugada"] != "Gana A" for a in por[1]["alternativas"])   # la vetada no sale ni de alternativa
    assert por[2]["jugada"] == "Más de 1.5 goles: Sí"                     # min(0.84, 0.78) = 0.78 < 0.82: gana lo que ambos cerebros ven
    assert pu.p_conservadora(c3) == 0.78 and pu.p_conservadora({"p": 0.7}) == 0.7


def _cuotas(casas, filas):
    import pandas as pd
    t = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    return pd.DataFrame([{"casa": c, "seleccion": s, "cuota": q, "capturada_en": t} for c in casas for s, q in filas])


def test_cerebro2_recalcula_con_el_consenso_y_decide():
    from golmodel.cerebro2 import parlays as c2
    from golmodel.config import cargar_v3
    v3 = cargar_v3()
    casas = ["pinnacle", "williamhill", "marathonbet", "nordicbet"]
    df = _cuotas(casas, [("1X2:1", 1.50), ("1X2:X", 4.20), ("1X2:2", 6.50), ("OU:2.5:over", 1.80), ("OU:2.5:under", 2.05)])
    cons = c2.consenso(_cuotas(["pinnacle", "betfair_ex_eu"], [("1X2:1", 1.5), ("1X2:X", 4.2), ("1X2:2", 6.5)]), v3)
    assert cons["p"] == {}                                           # 1 casa elegible (betfair no lo es): sin consenso, nunca se inventa
    cons = c2.consenso(df, v3)
    assert cons["casas"]["1X2"] == 4 and sum(cons["p"][s] for s in ("1X2:1", "1X2:X", "1X2:2")) == pytest.approx(1)
    pr = c2.probabilidades(cons)
    assert pr["DC:1X"] == pytest.approx(pr["1X2:1"] + pr["1X2:X"]) and 0.5 < pr["PA1:local"] < pr["1X2:1"] + 0.1
    assert 0.0 < pr["BTTS:si"] < 1.0 and pr["OU:1.5:over"] > pr["OU:2.5:over"]
    ahora = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    ok = c2.veredicto(pr["1X2:1"] + 0.01, "1X2:1", cons, pr, ahora, confirmado=True, cfg=CFG)
    assert ok["estado"] == "confirmada" and ok["casas"] == 4
    assert c2.veredicto(pr["1X2:1"] + 0.06, "1X2:1", cons, pr, ahora, confirmado=True, cfg=CFG)["estado"] == "con_reservas"
    assert c2.veredicto(pr["1X2:1"] + 0.12, "1X2:1", cons, pr, ahora, confirmado=True, cfg=CFG)["estado"] == "vetada"
    assert c2.veredicto(0.6, "1X2:1", cons, pr, ahora + timedelta(hours=20), confirmado=True, cfg=CFG)["estado"] == "con_reservas"  # cuotas viejas
    assert c2.veredicto(0.9, None, cons, pr, ahora, confirmado=True, cfg=CFG)["estado"] == "con_reservas"                        # córners: sin mercado
    vacio = {"p": {}, "casas": {}, "capturada": None}
    assert c2.veredicto(0.7, "1X2:1", vacio, {}, ahora, confirmado=True, cfg=CFG)["estado"] == "con_reservas"                   # solo modelo, partido confirmado
    v = c2.veredicto(0.7, "1X2:1", vacio, {}, ahora, confirmado=False, cfg=CFG)
    assert v["estado"] == "vetada" and "calendario" in v["nota"]                    # sin cuotas ni ESPN/FotMob: Cruz Azul-León del 27-nov


def test_auditar_marca_cada_pierna_y_deja_fuera_el_calendario_dudoso(monkeypatch):
    """Partido 1: con cuotas de 4 casas. Partido 2: sin cuotas ni ESPN/FotMob (como el Cruz Azul-León fantasma). NBA: sin auditoría multicasa."""
    from golmodel import repositorio as repo
    casas = ["pinnacle", "williamhill", "marathonbet", "nordicbet"]
    df = _cuotas(casas, [("1X2:1", 1.50), ("1X2:X", 4.20), ("1X2:2", 6.50), ("OU:2.5:over", 1.80), ("OU:2.5:under", 2.05)])
    vacio = df.iloc[0:0]
    monkeypatch.setattr(repo, "cuotas_vigentes", lambda conn, pid: df if pid == 1 else vacio)

    class Conn:
        def execute(self, sql, params=None):
            if "FROM partidos" in sql:
                return [{"id": 1, "espn_id": "401", "fotmob_id": None}, {"id": 2, "espn_id": None, "fotmob_id": None}]
            return None
    base = {"dia": "2026-11-14", "dia_texto": "sáb 14/11", "hora": "15:00", "inicio": "2026-11-14T15:00:00-06:00", "partido": "A vs B"}
    cands = [{**base, "partido_id": 1, "tipo": "ganador", "jugada": "Gana A", "p": 0.64, "sel": "1X2:1"},
             {**base, "partido_id": 1, "tipo": "primer_gol", "jugada": "PA(1) A", "p": 0.80, "sel": "PA1:local"},
             {**base, "partido_id": 2, "partido": "C vs D", "tipo": "ganador", "jugada": "Gana C", "p": 0.75, "sel": "1X2:1"},
             {**base, "partido_id": "nba:9", "partido": "E vs F", "tipo": "nba_ml", "jugada": "Gana E", "p": 0.70, "sel": None}]
    res = pu.auditar(Conn(), CFG, cands, datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc))
    est = {(c["partido_id"], c["tipo"]): c["c2"]["estado"] for c in cands}
    assert est[(1, "ganador")] == "confirmada" and est[(1, "primer_gol")] == "vetada"          # PA(1) muy por encima del consenso
    assert est[(2, "ganador")] == "vetada" and "calendario" in cands[2]["c2"]["nota"]
    assert est[("nba:9", "nba_ml")] == "sin_auditar" and res["piernas"]["vetada"] == 2
    cfg = {**CFG, "parlays_usuario": {**CFG["parlays_usuario"], "p_min": 0.60}}                 # «Gana A» (64%) dentro del rango
    elegidas = {x["partido_id"]: x for x in pu.elegir_por_partido(cands, cfg)}
    assert set(elegidas) == {1, "nba:9"} and elegidas[1]["jugada"] == "Gana A"               # el partido dudoso no entra
