"""Diagnóstico: qué responde Understat hoy, para ajustar el lector con datos reales.

    docker compose run --rm api python scripts/diagnostico_understat.py

No guarda nada. Hace 5 consultas con pausas de 5 s. Pega la salida completa en el chat.
"""
import re
import time

import httpx

BASE = "https://understat.com"
PAG = "/league/EPL/2025"
DATOS = "/getLeagueData/EPL/2025"
AJAX = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": BASE + PAG}
UA_NAVEGADOR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def mostrar(nombre, r):
    cuerpo = " ".join(r.text[:200].split())
    print(f"\n[{nombre}] HTTP {r.status_code} · {r.headers.get('content-type')} · {len(r.content)} bytes")
    print(f"  cookies recibidas: {list(r.cookies.keys()) or '-'} · servidor: {r.headers.get('server')}")
    print(f"  inicio: «{cuerpo}»")
    return r


def variante(nombre, ua, con_pagina):
    with httpx.Client(base_url=BASE, headers={"User-Agent": ua}, follow_redirects=True, timeout=20) as c:
        if con_pagina:
            p = mostrar(f"{nombre} · página", c.get(PAG))
            t = p.text
            print(f"  ¿datesData en HTML? {'datesData' in t} · ¿menciona getLeagueData? {'getLeagueData' in t}")
            print(f"  scripts: {re.findall(r'<script[^>]+src=[\"\\']([^\"\\']+)', t)[:6]}")
            time.sleep(5)
        r = mostrar(f"{nombre} · datos", c.get(DATOS, headers=AJAX))
        try:
            j = r.json()
            print(f"  JSON OK · claves: {list(j)[:6]} · partidos: {len(j.get('dates', []))}")
        except ValueError:
            print("  no es JSON")
    time.sleep(5)


variante("A sin cookie", "golmodelv2/0.1", con_pagina=False)
variante("B con cookie", "golmodelv2/0.1", con_pagina=True)
variante("C con cookie y navegador", UA_NAVEGADOR, con_pagina=True)
