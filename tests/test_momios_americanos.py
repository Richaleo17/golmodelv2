"""Momios americanos (Caliente: «−600», «+350») en captura por texto, lectura de boletos y conversión."""
import pytest

from golmodel.momios import americano_a_decimal, decimal_a_americano
from golmodel.servicios import boleto
from golmodel.servicios.captura import parsear_linea


def test_conversion_ida_y_vuelta():
    assert americano_a_decimal(350) == pytest.approx(4.5) and americano_a_decimal(-600) == pytest.approx(7 / 6)
    assert americano_a_decimal(100) == americano_a_decimal(-100) == pytest.approx(2.0) and americano_a_decimal(50) is None
    assert decimal_a_americano(4.5) == 350 and decimal_a_americano(7 / 6) == -600 and decimal_a_americano(1.0) is None


def test_captura_por_texto_acepta_americanos():
    l = parsear_linea("Toluca - América −150 +250 +400")
    assert not l.error and l.valores["1X2:1"] == pytest.approx(1 + 100 / 150) and l.valores["1X2:2"] == pytest.approx(5.0)
    assert parsear_linea("Toluca - América 1.85 3.40 4.20").valores["1X2:X"] == pytest.approx(3.4)      # el decimal sigue igual


def test_boleto_convierte_la_cuota_americana_leida():
    assert boleto._cuota_leida(None, -600) == pytest.approx(7 / 6) and boleto._cuota_leida(1.9, -600) == pytest.approx(1.9)
    assert boleto._cuota_leida(None, None) is None and boleto._cuota_leida(None, 20) is None and boleto._cuota_leida(True, None) is None
    assert "cuota_americana" in boleto.PROMPT
