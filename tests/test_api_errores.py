"""La API explica los errores de conexión a la base en lugar de responder 500 sin contexto."""
from fastapi.testclient import TestClient

from golmodel.api import main
from golmodel.config import cargar


def test_base_inaccesible_responde_503_con_pista():
    main._servicio = main.Servicio(cargar(), cadenas={},
                                   dsn="postgresql://golmodel:mala@/golmodel?host=/nonexistent&port=1")
    r = TestClient(main.app).get("/estado")
    assert r.status_code == 503
    assert "No se pudo conectar a la base de datos" in r.json()["detail"]
    main._servicio = None
