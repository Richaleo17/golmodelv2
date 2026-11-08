"""Captura de cuotas en lote: lectura de texto, reconocimiento de partidos y reglas de seguridad."""
import pytest

from golmodel.servicios import captura as c

# Partidos del día (formato de repositorio.partidos_de_dias)
PARTIDOS = [
    {"id": 1, "local_id": 10, "visitante_id": 11, "local": "Napoli", "visitante": "Lazio"},
    {"id": 2, "local_id": 12, "visitante_id": 13, "local": "Ipswich", "visitante": "Bournemouth"},
    {"id": 3, "local_id": 14, "visitante_id": 15, "local": "Man United", "visitante": "Man City"},
]
NOMBRES = {10: {"Napoli", "SSC Napoli"}, 11: {"Lazio", "SS Lazio"}, 12: {"Ipswich", "Ipswich Town FC"},
           13: {"Bournemouth", "AFC Bournemouth"}, 14: {"Man United", "Manchester United FC"},
           15: {"Man City", "Manchester City FC"}}


def test_parsear_linea_formatos():
    ln = c.parsear_linea("Napoli - Lazio 2.26 3.64 2.92")
    assert ln.error == "" and (ln.local, ln.visitante) == ("Napoli", "Lazio")
    assert ln.valores == {"1X2:1": 2.26, "1X2:X": 3.64, "1X2:2": 2.92}
    ln = c.parsear_linea("Ipswich Town vs Bournemouth 3,77x 3.71x 1.90x 1.85 1.95")        # coma, sufijo x, 5 cuotas
    assert ln.error == "" and ln.valores["1X2:1"] == 3.77 and ln.valores["OU:2.5:under"] == 1.95
    assert len(c.parsear_linea("A v B 1.5 3.4 5.1 1.9 1.9 1.8 2.0").valores) == 7
    assert "separar" in c.parsear_linea("Napoli Lazio 2.26 3.64 2.92").error
    assert "4 cuotas" in c.parsear_linea("Napoli - Lazio 2.26 3.64 2.92 1.9").error
    assert "rango" in c.parsear_linea("Napoli - Lazio 0.95 3.64 2.92").error
    # Equipos con números en el nombre no se confunden con cuotas (las cuotas llevan punto decimal)
    ln = c.parsear_linea("Mainz 05 - Union Berlin 1.90 3.40 4.00")
    assert ln.error == "" and ln.local == "Mainz 05"


def test_emparejar_reconoce_variantes_y_evita_errores():
    p, err = c.emparejar("Napoli", "Lazio", PARTIDOS, NOMBRES)
    assert p["id"] == 1 and not err
    p, _ = c.emparejar("Ipswich Town", "Bournemouth", PARTIDOS, NOMBRES)
    assert p["id"] == 2
    p, _ = c.emparejar("Manchester United", "Manchester City", PARTIDOS, NOMBRES)
    assert p["id"] == 3
    p, err = c.emparejar("Lazio", "Napoli", PARTIDOS, NOMBRES)                  # invertido: no se adivina
    assert p is None and "invertido" in err.lower()
    p, err = c.emparejar("Roma", "Inter", PARTIDOS, NOMBRES)
    assert p is None and "No encontré" in err
    # 'Manchester' a secas encaja con United y City: ambiguo, no se elige uno
    dos = [PARTIDOS[2], {"id": 4, "local_id": 16, "visitante_id": 17, "local": "Man City", "visitante": "Man United"}]
    nom = {**NOMBRES, 16: NOMBRES[15], 17: NOMBRES[14]}
    p, err = c.emparejar("Manchester", "Manchester", dos, nom)
    assert p is None


def test_guardar_lote_agrupa_por_partido(monkeypatch):
    llamadas = []
    monkeypatch.setattr(c.repo, "casa_id", lambda conn, clave: 99)
    monkeypatch.setattr(c.repo, "insertar_cuotas", lambda conn, pid, casa, filas, tipo, fuente, ts: llamadas.append((pid, casa, filas, tipo, fuente)))
    n = c.guardar_lote(None, [(1, "1X2:1", 2.2), (1, "1X2:X", 3.5), (2, "OU:2.5:over", 1.9)], ahora=None)
    assert n == 3 and len(llamadas) == 2
    assert llamadas[0] == (1, 99, [("1X2:1", 2.2), ("1X2:X", 3.5)], "actual", "manual")


def test_nombre_identico_gana_a_uno_parecido():
    """Con nombres casi iguales (EPL_03 / EPL_13), el idéntico se elige y el orden invertido se explica."""
    ps = [{"id": 1, "local_id": 1, "visitante_id": 2, "local": "EPL_19", "visitante": "EPL_03"},
          {"id": 2, "local_id": 3, "visitante_id": 4, "local": "EPL_13", "visitante": "EPL_09"}]
    nom = {1: {"EPL_19"}, 2: {"EPL_03"}, 3: {"EPL_13"}, 4: {"EPL_09"}}
    p, err = c.emparejar("EPL_19", "EPL_03", ps, nom)
    assert p["id"] == 1 and not err
    p, err = c.emparejar("EPL_03", "EPL_19", ps, nom)                      # invertido de verdad
    assert p is None and "invertido" in err.lower()
