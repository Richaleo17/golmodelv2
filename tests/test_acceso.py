"""Acceso con contraseña: roles, modo seguro por defecto, tokens y freno de intentos."""
from golmodel import acceso as a


def test_modo_de_acceso_es_seguro_por_defecto():
    assert a.modo_acceso("", "", "127.0.0.1") == "abierto"          # solo en tu computadora
    assert a.modo_acceso("", "", "0.0.0.0") == "sin_clave"          # abierta a la red: no muestra nada
    assert a.modo_acceso("", "", "192.168.1.5") == "sin_clave"
    assert a.modo_acceso("abc", "", "0.0.0.0") == "login"
    assert a.modo_acceso("abc", "xyz", "0.0.0.0") == "login"
    assert a.modo_acceso("", "xyz", "0.0.0.0") == "config_invalida"  # lector sin administrador


def test_verificar_roles_y_claves_vacias():
    assert a.verificar("admin1", "admin1", "lector1") == a.ADMIN
    assert a.verificar("lector1", "admin1", "lector1") == a.LECTOR
    assert a.verificar("otra", "admin1", "lector1") is None
    assert a.verificar("", "admin1", "") is None                     # una clave vacía nunca entra
    assert a.verificar("", "", "") is None


def test_token_ida_y_vuelta_expira_y_no_se_falsifica():
    sec = a.secreto("admin1", "lector1", {})
    t = a.crear_token(a.LECTOR, sec, ahora=1000.0, dias=30)
    assert a.validar_token(t, sec, ahora=1000.0 + 86400) == a.LECTOR
    assert a.validar_token(t, sec, ahora=1000.0 + 31 * 86400) is None          # vencido
    rol, exp, firma = t.split(".")
    assert a.validar_token(f"{a.ADMIN}.{exp}.{firma}", sec, ahora=1001.0) is None  # subir de rol no funciona
    assert a.validar_token(f"{rol}.{int(exp) + 999999}.{firma}", sec, ahora=1001.0) is None
    assert a.validar_token("basura", sec) is None and a.validar_token(None, sec) is None
    otro = a.secreto("admin1", "cambiada", {})                                  # cambiar una clave invalida tokens
    assert a.validar_token(t, otro, ahora=1001.0) is None
    assert a.secreto("x", "y", {"APP_SECRET": "fija"}) == a.secreto("z", "w", {"APP_SECRET": "fija"})


def test_limitador_bloquea_tras_5_fallos_y_se_libera():
    lim = a.Limitador()
    for k in range(4):
        lim.fallo(ahora=100.0 + k)
    assert lim.bloqueado(ahora=105.0) == 0
    lim.fallo(ahora=104.0)                                   # quinto fallo
    assert 290 < lim.bloqueado(ahora=105.0) <= 300
    assert lim.bloqueado(ahora=104.0 + 301) == 0
    lim2 = a.Limitador()
    lim2.fallo(ahora=0.0)
    lim2.fallo(ahora=700.0)                                  # el primero ya salió de la ventana de 10 min
    assert len(lim2._fallos) == 1
    lim2.exito()
    assert lim2._fallos == []
