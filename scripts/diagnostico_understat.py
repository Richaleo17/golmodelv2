"""Diagnóstico: cómo entrega Understat sus datos hoy, para ajustar el lector con datos reales.

    docker compose run --rm api python scripts/diagnostico_understat.py

No guarda nada. Hace unas 6-8 consultas con pausas. Pega la salida completa en el chat.
"""
import hashlib
import os
import re
import time

import httpx

BASE = os.environ.get("UNDERSTAT_BASE", "https://understat.com")
PAG = "/league/EPL/2025"
DATOS = "/getLeagueData/EPL/2025"
PAUSA = float(os.environ.get("UNDERSTAT_PAUSA", "4"))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
RE_SRC = re.compile(r"""<script[^>]*\ssrc=["']([^"']+)["']""", re.I)
RE_RUTA = re.compile(r"""["'`](/?[A-Za-z0-9_\-./]*(?:api|get|data|league|match|Data)[A-Za-z0-9_\-./{}$]*)["'`]""")


def huella(r: httpx.Response) -> str:
    return hashlib.md5(r.content).hexdigest()[:10]


def mostrar(nombre: str, r: httpx.Response) -> None:
    cuerpo = " ".join(r.text[:160].split())
    print(f"\n[{nombre}] HTTP {r.status_code} · {r.headers.get('content-type')} · {len(r.content)} bytes · "
          f"huella {huella(r)}")
    print(f"  cookies: {list(r.cookies.keys()) or '-'} · servidor: {r.headers.get('server')}")
    print(f"  inicio: «{cuerpo}»")


def main() -> None:
    with httpx.Client(base_url=BASE, headers={"User-Agent": UA}, follow_redirects=True, timeout=20) as c:
        pag = c.get(PAG)
        mostrar("1 página de la liga", pag)
        html = pag.text
        print(f"  ¿datesData? {'datesData' in html} · ¿getLeagueData? {'getLeagueData' in html}")
        time.sleep(PAUSA)

        otra = c.get("/ruta-que-no-existe-xyz")
        mostrar("2 ruta inexistente (¿la web responde lo mismo a todo?)", otra)
        print(f"  ¿igual a la página de la liga? {huella(otra) == huella(pag)}")
        time.sleep(PAUSA)

        datos = c.get(DATOS, headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE + PAG,
                                      "Accept": "application/json, text/javascript, */*; q=0.01"})
        mostrar("3 getLeagueData (AJAX)", datos)

        scripts = RE_SRC.findall(html)
        print(f"\n[4] scripts de la página ({len(scripts)}): {scripts[:12]}")
        inline = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
        for k, s in enumerate(t for t in inline if t.strip()):
            print(f"  inline {k}: «{' '.join(s.split())[:300]}»")

        propios = [s for s in scripts if s.startswith("/") or "understat" in s][:4]
        rutas: set[str] = set()
        for src in propios:
            time.sleep(PAUSA)
            js = c.get(src)
            print(f"\n[5] {src}: HTTP {js.status_code}, {len(js.content)} bytes")
            for m in RE_RUTA.findall(js.text):
                if 3 < len(m) < 80 and not m.endswith((".png", ".svg", ".css", ".woff2")):
                    rutas.add(m)
            for ctx in re.findall(r".{0,80}(?:fetch\(|axios|XMLHttpRequest|\.ajax\().{0,120}", js.text)[:8]:
                print(f"  llamada: «{' '.join(ctx.split())}»")
        print(f"\n[6] rutas candidatas en el JavaScript ({len(rutas)}):")
        for r in sorted(rutas)[:60]:
            print("  ", r)


if __name__ == "__main__":
    main()
