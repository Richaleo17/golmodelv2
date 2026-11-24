"""Parlays de «Qué meter» (3-5) y «Soñadoras» (6-10): piernas Gana o PA(1), hasta 3 días, sin repetir partidos."""
from datetime import date, datetime, timezone

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
    assert por[1]["tipo"] == "pa1" and por[1]["jugada"].startswith("PA(1)") and por[2]["tipo"] == "gana"
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
