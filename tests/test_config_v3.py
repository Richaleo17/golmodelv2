"""config.cargar_v3: carga la política V3 y falla cerrado si está incompleta o la bandera no es un booleano explícito."""
import shutil

import pytest
import yaml

from golmodel.config import RUTA_V3, SECCIONES_V3, cargar_v3


def test_carga_la_politica_real_con_modo_experimental_activo():
    cfg = cargar_v3()
    assert cfg["modo_experimental"] is True and set(SECCIONES_V3) <= set(cfg)
    assert cfg["calibracion_tickets"]["version_provisional"]["c_global"] > 0


@pytest.mark.parametrize("seccion", SECCIONES_V3)
def test_falla_si_falta_cualquier_seccion(tmp_path, seccion):
    cfg = yaml.safe_load(RUTA_V3.read_text(encoding="utf-8"))
    del cfg[seccion]
    ruta = tmp_path / "v3.yaml"
    ruta.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match=seccion):
        cargar_v3(ruta)


@pytest.mark.parametrize("valor", ["true", 1, None, "si"])
def test_la_bandera_debe_ser_booleano_explicito(tmp_path, valor):
    cfg = yaml.safe_load(RUTA_V3.read_text(encoding="utf-8"))
    cfg["modo_experimental"] = valor
    ruta = tmp_path / "v3.yaml"
    ruta.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="booleano"):
        cargar_v3(ruta)


def test_archivo_inexistente_falla_en_voz_alta(tmp_path):
    with pytest.raises(FileNotFoundError):
        cargar_v3(tmp_path / "no_existe.yaml")
