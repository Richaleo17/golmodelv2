"""Factores de PARTIDO que mueven λ: altitud, clima, motivación, ambiente del estadio y sede neutral.

Cada factor da un multiplicador sobre λ_local y λ_visitante y una razón en texto («porque»).
Son ajustes de DISEÑO, con tope, documentados con su fuente; PROVISIONALES hasta medirlos en walk-forward.

[Literatura / fuentes del diseño]
* Altitud: McSharry (2007, BMJ) — en partidos de selecciones sudamericanas, cada 1000 m de diferencia de altitud
  sube ≈ 0.5 la diferencia de goles a favor del local (más goles del local y menos del visitante). Aquí se reparte
  la mitad del efecto en cada λ, desde un umbral (debajo de ≈ 500 m de diferencia no hay efecto medible).
* Clima: rendimiento físico cae con calor y con cambios bruscos de temperatura respecto a lo acostumbrado
  (Nassis et al., 2015: menos carreras de alta intensidad en calor). Se mide la diferencia entre la temperatura
  prevista en la sede y la de la ciudad del visitante ESE MISMO DÍA (lo que acostumbra), no un promedio genérico.
* Motivación: un equipo al que le basta el empate juega a cerrar (menos goles de ambos); uno que necesita ganar
  arriesga (más goles a favor y en contra); «nada en juego» y amistosos bajan intensidad.
* Ambiente: la ventaja de local promedio YA está en el modelo; solo se ajusta lo que el estadio se aparta del
  promedio (0.5): Estambul, Belgrado o Zenica pesan más que Vaduz o San Marino.
* Sede neutral: sin ventaja de local (lo hace el modelo); aquí solo se anota la razón.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

RUTA_SEDES = Path(__file__).resolve().parents[3] / "config" / "sedes.yaml"
ESTADOS_MOTIVACION = ("necesita_ganar", "le_sirve_empate", "nada_en_juego", "normal")


@dataclass
class Factores:
    mult_local: float = 1.0
    mult_visitante: float = 1.0
    razones: list[str] = field(default_factory=list)
    detalle: dict = field(default_factory=dict)

    def aplicar(self, nombre: str, f_local: float, f_visit: float, razon: str | None) -> None:
        if abs(f_local - 1) < 0.005 and abs(f_visit - 1) < 0.005:      # menos de medio punto: ruido, no se anota
            return
        self.mult_local *= f_local
        self.mult_visitante *= f_visit
        self.detalle[nombre] = {"local": round(f_local, 4), "visitante": round(f_visit, 4)}
        if razon:
            self.razones.append(razon)


def cargar_sedes(ruta: Path | str = RUTA_SEDES) -> dict[str, dict]:
    d = yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for grupo in ("selecciones", "clubes"):
        for nombre, s in (d.get(grupo) or {}).items():
            out[str(nombre)] = s
    return out


def _pct(x: float) -> str:
    return f"{(x - 1) * 100:+.0f}%"


def calcular(local: str, visitante: str, sedes: dict[str, dict], cfg: dict, *, neutral: bool = False,
             sede: dict | None = None, temp_sede: float | None = None, temp_casa_visitante: float | None = None,
             temp_casa_local: float | None = None, motivacion: dict | None = None, amistoso: bool = False,
             bajas: dict | None = None) -> Factores:
    """sede: {alt, lat, lon, ambiente} si se juega fuera de la sede habitual del local (por defecto la del local).
    temp_*: °C previstos a la hora del partido (sede) y ese día en la ciudad de cada equipo (lo que acostumbra).
    motivacion: {'local': estado, 'visitante': estado} con estados de ESTADOS_MOTIVACION."""
    f = Factores()
    c = cfg
    s_loc, s_vis = sedes.get(local), sedes.get(visitante)
    s_partido = sede or (None if neutral else s_loc)

    # ---------------- altitud
    ca = c["altitud"]
    if s_partido is not None and s_partido.get("alt") is not None:
        alt = float(s_partido["alt"])
        for lado, s_eq, nombre in (("visitante", s_vis, visitante), ("local", None if neutral else s_loc, local)):
            if s_eq is None or s_eq.get("alt") is None:
                continue
            dif = alt - float(s_eq["alt"])                   # >0: el equipo sube a jugar más alto de lo que acostumbra
            exceso = max(0.0, dif - ca["umbral_m"]) / 1000.0
            if exceso <= 0:
                continue
            caida = min(ca["por_1000m_propio"] * exceso, ca["tope"])        # el que sube mete menos
            alza = min(ca["por_1000m_rival"] * exceso, ca["tope"])          # y le meten más (se cansa)
            if lado == "visitante":
                f.aplicar(f"altitud_{lado}", 1 + alza, 1 - caida,
                          f"Altitud: {nombre} sube {dif:.0f} m sobre lo que acostumbra ({alt:.0f} m): {nombre} {_pct(1 - caida)} goles, rival {_pct(1 + alza)}")
            else:
                f.aplicar(f"altitud_{lado}", 1 - caida, 1 + alza,
                          f"Altitud: {nombre} juega {dif:.0f} m más alto que en casa: {nombre} {_pct(1 - caida)} goles")

    # ---------------- clima (temperatura del día vs lo que acostumbra cada equipo)
    cc = c["clima"]
    if temp_sede is not None:
        for lado, t_casa, nombre in (("visitante", temp_casa_visitante, visitante), ("local", temp_casa_local if neutral else None, local)):
            if t_casa is None:
                continue
            delta = abs(temp_sede - t_casa)
            exceso = max(0.0, delta - cc["umbral_delta_c"])
            if exceso <= 0:
                continue
            caida = min(cc["por_grado"] * exceso, cc["tope"])
            fl, fv = (1.0, 1 - caida) if lado == "visitante" else (1 - caida, 1.0)
            f.aplicar(f"clima_{lado}", fl, fv,
                      f"Clima: {temp_sede:.0f}°C en la sede vs {t_casa:.0f}°C donde vive {nombre} (Δ{delta:.0f}°C): {nombre} {_pct(1 - caida)} goles")
        if temp_sede >= cc["calor_c"]:
            k = 1 - cc["calor_ambos"]
            f.aplicar("calor", k, k, f"Calor de {temp_sede:.0f}°C: ritmo más bajo, menos goles de los dos ({_pct(k)})")
        elif temp_sede <= cc["frio_c"]:
            k = 1 - cc["frio_ambos"]
            f.aplicar("frio", k, k, f"Frío de {temp_sede:.0f}°C: cancha pesada, menos goles ({_pct(k)})")

    # ---------------- motivación (puntos)
    cm = c["motivacion"]
    mot = dict(motivacion or {})
    if amistoso:
        mot.setdefault("local", "amistoso")
        mot.setdefault("visitante", "amistoso")
    for lado, nombre in (("local", local), ("visitante", visitante)):
        estado = mot.get(lado) or "normal"
        if estado == "normal" or estado not in cm:
            continue
        propio, rival = float(cm[estado]["propio"]), float(cm[estado]["rival"])
        fl, fv = (1 + propio, 1 + rival) if lado == "local" else (1 + rival, 1 + propio)
        texto = {"necesita_ganar": "necesita ganar: sale a buscarlo (mete más, pero también se abre)",
                 "le_sirve_empate": "le sirve el empate: cierra el partido (menos goles)",
                 "nada_en_juego": "no se juega nada: baja la intensidad",
                 "amistoso": "amistoso: rotaciones y defensas mezcladas (algo más de goles, resultado menos predecible)"}[estado]
        f.aplicar(f"motivacion_{lado}", fl, fv, f"Motivación: {nombre} {texto} ({_pct(1 + propio)} sus goles)")

    # ---------------- ambiente / ruido (solo lo que se aparta del promedio; la ventaja de local promedio ya está en el modelo)
    cr = c["ambiente"]
    if not neutral and s_loc is not None and s_loc.get("ambiente") is not None and sede is None:
        dev = float(s_loc["ambiente"]) - 0.5
        if abs(dev) >= 0.1:
            fl, fv = 1 + cr["local"] * dev * 2, 1 - cr["visitante"] * dev * 2
            txt = "estadio muy caliente/ruidoso" if dev > 0 else "estadio frío, poca presión"
            f.aplicar("ambiente", fl, fv, f"Ambiente: {txt} en {s_loc.get('ciudad', local)} ({visitante} {_pct(fv)} goles)")

    if neutral:
        f.razones.append("Sede neutral: sin ventaja de local")

    # ---------------- bajas confirmadas (config/bajas.yaml): un delantero menos baja tus goles; un defensa o portero menos sube los del rival
    cb = c.get("bajas") or {}
    if bajas and cb:
        for lado, nombre in (("local", local), ("visitante", visitante)):
            lista = bajas.get(lado) or []
            if not lista:
                continue
            propio = rival = 0.0
            nombres = []
            for b in lista:
                regla = (cb.get("roles") or {}).get(str(b.get("rol", "")).lower())
                if not regla:
                    continue
                k = float(cb.get("peso", {}).get(str(b.get("peso", "titular")).lower(), 1.0))
                propio += float(regla.get("propio", 0.0)) * k
                rival += float(regla.get("rival", 0.0)) * k
                nombres.append(str(b.get("jugador", "?")))
            t = float(cb.get("tope", 0.15))
            propio, rival = max(propio, -t), min(rival, t)
            fl, fv = (1 + propio, 1 + rival) if lado == "local" else (1 + rival, 1 + propio)
            if nombres:
                otro = visitante if lado == "local" else local
                f.aplicar(f"bajas_{lado}", fl, fv, f"Bajas de {nombre}: {', '.join(nombres)} → {nombre} {_pct(1 + propio)} goles, {otro} {_pct(1 + rival)}")

    # ---------------- tope total
    t = c["tope_total"]
    f.mult_local = min(max(f.mult_local, 1 - t), 1 + t)
    f.mult_visitante = min(max(f.mult_visitante, 1 - t), 1 + t)
    return f
