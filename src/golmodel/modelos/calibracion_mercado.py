"""Calibración post-hoc del NIVEL de la probabilidad (`p_calibrada`), por segmento.

Diseño: docs/v3_incertidumbre_diseno.md (separación nivel vs ancho),
docs/v3_umbrales_prerregistro.md (gate de cobertura) y README_implementacion.md.

Contratos de implementación (los cinco, fijados por el responsable del proyecto):
  1. Este módulo calibra el NIVEL (`p_calibrada`). El ANCHO (τ/σ) es de
     `calibracion_tickets.py`/incertidumbre. Módulos, versiones y protocolos
     separados; no se unifican por eficiencia.
  2. Ajuste y evaluación DISJUNTOS: el calibrador en t se ajusta solo con datos
     < t y se evalúa en [t, t+paso). La orquestación walk-forward vive en
     `backtest/` y pasa aquí ventanas ya separadas; este módulo nunca mezcla.
  3. Pooling seg→liga→global con `n_eff` REAL (partidos/días distintos, no
     selecciones) y regularización `w = n_eff/(n_eff+n0)` documentada.
  4. Versionado reproducible: cada calibrador registra calibrador_version,
     datos_hasta, segmento, metodo, params y config_hash (ver VersionCalibrador).
  5. Gate de cobertura: un calibrador NO se declara apto hasta pasar la fiabilidad
     OOS (ver `gate_despliegue`). Si falla, no se despliega y NO se relaja el criterio.

[Diseño] Método por defecto según el régimen de datos del segmento (control de
complejidad ligado a n_eff): fino → temperature (1 parámetro), moderado → Platt
(2), amplio → isotónica (no paramétrica). Entre varios viables se elige por
log-loss OOS con parsimonia (un método más complejo solo si supera al simple de
forma significativa) y nunca por debajo de la probabilidad cruda (identidad).

Módulo de LÓGICA PURA (numpy/scipy + cfg). La carga de datos y la persistencia de
versiones (tabla versiones_calibrador) son de `servicios/`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from golmodel.backtest.metricas import log_loss

EPS = 1e-12
METODOS_POR_COMPLEJIDAD = ("identidad", "temperature", "platt", "isotonica")


# --------------------------------------------------------------------- helpers

def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def _como_matriz(p: np.ndarray) -> tuple[np.ndarray, bool]:
    """Devuelve (P (n,k), binario). Para binario (n,) → columnas [1-p, p]."""
    p = np.asarray(p, float)
    if p.ndim == 1:
        return np.column_stack([1 - p, p]), True
    return p, False


def _pava(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Pool-Adjacent-Violators: ajuste monótono NO decreciente por mínimos
    cuadrados ponderados. y, w ya ordenados por x ascendente. [Literatura]"""
    val: list[float] = []
    wt: list[float] = []
    ln: list[int] = []
    for i in range(len(y)):
        v, cw, cl = float(y[i]), float(w[i]), 1
        while val and val[-1] >= v:
            pv, pw, pl = val.pop(), wt.pop(), ln.pop()
            v = (pv * pw + v * cw) / (pw + cw)
            cw += pw
            cl += pl
        val.append(v); wt.append(cw); ln.append(cl)
    out: list[float] = []
    for v, l in zip(val, ln):
        out += [v] * l
    return np.asarray(out, float)


# --------------------------------------------------------------------- modelo

@dataclass(frozen=True)
class Calibrador:
    """Calibrador ajustado, serializable (params → jsonb). `clases` = 2 binario."""
    metodo: str                       # 'identidad'|'temperature'|'platt'|'isotonica'
    clases: int
    params: dict = field(default_factory=dict)

    def aplicar(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, float)
        if self.metodo == "identidad":
            return p
        if self.metodo == "temperature":
            T = float(self.params["T"])
            P, binario = _como_matriz(p)
            z = np.log(np.clip(P, EPS, 1.0)) / T
            z -= z.max(axis=1, keepdims=True)
            e = np.exp(z)
            cal = e / e.sum(axis=1, keepdims=True)
            return cal[:, 1] if binario else cal
        if self.metodo == "platt":                      # binario
            a, b = float(self.params["a"]), float(self.params["b"])
            return _sigmoid(a * _logit(p) + b)
        if self.metodo == "isotonica":                  # binario
            x = np.asarray(self.params["x"], float)
            y = np.asarray(self.params["y"], float)
            return np.interp(np.clip(p, 0, 1), x, y, left=y[0], right=y[-1])
        raise ValueError(f"método desconocido: {self.metodo}")


# --------------------------------------------------------------------- ajuste

def ajustar_identidad(clases: int = 2) -> Calibrador:
    return Calibrador("identidad", clases)


def ajustar_temperature(p: np.ndarray, y: np.ndarray) -> Calibrador:
    """Un escalar T>0: cal = softmax(log p / T). Sirve binario y multiclase.
    Preserva el orden; máxima eficiencia de datos. [Diseño]"""
    P, _ = _como_matriz(p)
    y = np.asarray(y, int)
    logp = np.log(np.clip(P, EPS, 1.0))

    def perdida(logT: float) -> float:
        T = np.exp(logT)
        z = logp / T
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        cal = e / e.sum(axis=1, keepdims=True)
        return log_loss(cal, y)

    r = minimize_scalar(perdida, bounds=(np.log(0.2), np.log(5.0)), method="bounded")
    return Calibrador("temperature", P.shape[1], {"T": float(np.exp(r.x))})


def ajustar_platt(p: np.ndarray, y: np.ndarray) -> Calibrador:
    """Binario: cal = sigmoid(a·logit(p) + b). Captura sesgo y sobre/infra-confianza."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    x = _logit(p)

    def perdida(ab: np.ndarray) -> float:
        q = np.clip(_sigmoid(ab[0] * x + ab[1]), EPS, 1 - EPS)
        return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))

    r = minimize(perdida, np.array([1.0, 0.0]), method="Nelder-Mead",
                 options={"xatol": 1e-6, "fatol": 1e-9})
    return Calibrador("platt", 2, {"a": float(r.x[0]), "b": float(r.x[1])})


def ajustar_isotonica(p: np.ndarray, y: np.ndarray) -> Calibrador:
    """Binario, no paramétrica y monótona. Requiere más datos (ver régimen)."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    orden = np.argsort(p, kind="mergesort")
    ps, ys = p[orden], y[orden]
    fit = _pava(ys, np.ones_like(ys))
    # Colapsar a x únicos (np.interp exige x estrictamente creciente)
    xs, inv = np.unique(ps, return_inverse=True)
    yk = np.array([fit[inv == k].mean() for k in range(len(xs))])
    return Calibrador("isotonica", 2, {"x": xs.tolist(), "y": yk.tolist()})


_AJUSTE = {"temperature": ajustar_temperature, "platt": ajustar_platt, "isotonica": ajustar_isotonica}


# --------------------------------------------------------------------- n_eff

def n_efectivo(ids_partido: np.ndarray) -> int:
    """n_eff = nº de partidos/días DISTINTOS, no de selecciones. [Diseño]
    Las selecciones del mismo partido están correlacionadas; el tamaño efectivo
    es el de unidades independientes."""
    return int(np.unique(np.asarray(ids_partido)).size)


# --------------------------------------------------------------------- selección

@dataclass
class ResultadoSeleccion:
    calibrador: Calibrador
    metodo: str
    logloss_oos: dict          # por método evaluado (incluida 'identidad'/cruda)
    razon: str                 # por qué se eligió


def _candidatos(n_eff: int, clases: int, cfg: dict) -> list[str]:
    """Métodos viables según el régimen de datos. Binario puede usar los tres;
    multiclase (1X2) solo temperature (isotónica/Platt binarios → OvR, fuera de v1)."""
    c = cfg.get("calibracion_mercado", {})
    cand = ["temperature"]
    if clases == 2:
        if n_eff >= c.get("n_min_platt", 200):
            cand.append("platt")
        if n_eff >= c.get("n_min_isotonica", 1000):
            cand.append("isotonica")
    return cand


def _logloss(cal: Calibrador, p: np.ndarray, y: np.ndarray) -> float:
    q = cal.aplicar(p)
    P, _ = _como_matriz(q)
    return log_loss(P, np.asarray(y, int))


def seleccionar(p_tr: np.ndarray, y_tr: np.ndarray, p_val: np.ndarray, y_val: np.ndarray,
                clases: int, n_eff: int, cfg: dict) -> ResultadoSeleccion:
    """Elige el calibrador por log-loss OOS con parsimonia y 'no peor que crudo'.

    Ajuste en (p_tr, y_tr); evaluación en (p_val, y_val) — ventanas DISJUNTAS que
    provee el llamador walk-forward (contrato 2). Un método más complejo solo gana
    si mejora al más simple por un margen mínimo (anti-sobreajuste); si nadie bate
    a la probabilidad cruda, se devuelve identidad (no calibrar por calibrar).
    """
    c = cfg.get("calibracion_mercado", {})
    margen = float(c.get("margen_logloss", 0.002))     # mejora mínima para subir de complejidad

    crudo = ajustar_identidad(clases)
    ll = {"identidad": _logloss(crudo, p_val, y_val)}
    ajustados = {"identidad": crudo}
    for m in _candidatos(n_eff, clases, cfg):
        try:
            cal = _AJUSTE[m](p_tr, y_tr)
            ajustados[m] = cal
            ll[m] = _logloss(cal, p_val, y_val)
        except Exception:
            continue

    # Parsimonia: recorrer de simple a complejo; subir solo si mejora > margen.
    elegido = "identidad"
    for m in METODOS_POR_COMPLEJIDAD:
        if m in ll and ll[m] < ll[elegido] - margen:
            elegido = m
    razon = (f"log-loss OOS {ll[elegido]:.4f} (crudo {ll['identidad']:.4f}); "
             f"parsimonia margen {margen}")
    if elegido == "identidad":
        razon = f"ningún método bate al crudo por > {margen}: se deja sin calibrar"
    return ResultadoSeleccion(ajustados[elegido], elegido, ll, razon)


# --------------------------------------------------------------------- pooling

def poolear(hijo: Calibrador, padre: Calibrador, n_eff: int, cfg: dict) -> Calibrador:
    """Shrinkage de parámetros hacia el padre (liga, y liga→global). [Diseño]
    w = n_eff/(n_eff+n0). Solo para métodos paramétricos (temperature/platt); el
    isotónico no es poolable, por eso solo se usa con n_eff grande (sin pooling)."""
    n0 = float(cfg.get("calibracion_mercado", {}).get("n0", 150))
    w = n_eff / (n_eff + n0)
    if hijo.metodo != padre.metodo:
        return hijo                                     # no se mezclan métodos distintos
    if hijo.metodo == "temperature":                    # shrink en log (T>0)
        lt = w * np.log(hijo.params["T"]) + (1 - w) * np.log(padre.params["T"])
        return Calibrador("temperature", hijo.clases, {"T": float(np.exp(lt))})
    if hijo.metodo == "platt":
        a = w * hijo.params["a"] + (1 - w) * padre.params["a"]
        b = w * hijo.params["b"] + (1 - w) * padre.params["b"]
        return Calibrador("platt", 2, {"a": float(a), "b": float(b)})
    return hijo                                         # identidad / isotónica: sin pooling


# --------------------------------------------------------------------- versionado

@dataclass(frozen=True)
class VersionCalibrador:
    """Registro inmutable para reproducir cada predicción histórica (contrato 4).
    `servicios/` lo persiste en la tabla versiones_calibrador."""
    calibrador_version: str
    datos_hasta: datetime
    segmento: tuple                   # (liga, mercado, rango_cuota)
    metodo: str
    params: dict
    n_eff: int
    config_hash: str

    def a_json(self) -> dict:
        d = asdict(self)
        d["datos_hasta"] = self.datos_hasta.isoformat()
        d["segmento"] = list(self.segmento)
        return d


# --------------------------------------------------------------------- gate

# --------------------------------------------------------------------- contrato de salida

SENTINEL_SIN_CALIBRADOR = "identidad:sin_calibrador"


@dataclass(frozen=True)
class SeleccionCalibrada:
    """Salida de calibracion_mercado, consumida por incertidumbre (contrato §2.5).

    Fallback explícito: si no hay calibrador para el segmento, p_calibrada = p_cruda con
    calibrado=False y la versión sentinel; NUNCA None ni exclusión. Versión ausente = fail-closed.
    """
    p_calibrada: float          # = p_cruda si calibrado=False (fallback identidad); nunca None
    calibrador_version: str     # SIEMPRE presente; SENTINEL_SIN_CALIBRADOR en fallback
    calibrado: bool
    segmento: tuple

    def __post_init__(self):
        if not self.calibrador_version:
            raise ValueError(
                "calibrador_version ausente: fail-closed. El caso legítimo 'sin calibrador' usa "
                f"el sentinel {SENTINEL_SIN_CALIBRADOR!r}, no ausencia (ausencia = bug de pipeline).")


def aplicar_por_segmento(p_modelo: np.ndarray, segmentos, registro: dict) -> list[SeleccionCalibrada]:
    """Aplica el calibrador de cada segmento. `incertidumbre` consume ESTO, nunca reimplementa.

    p_modelo: (n,); segmentos: secuencia de tuplas (len n);
    registro: dict segmento -> (Calibrador, calibrador_version). Segmento sin calibrador →
    identidad con marca explícita.
    """
    p_modelo = np.asarray(p_modelo, float)
    salida: list[SeleccionCalibrada] = []
    for p, seg in zip(p_modelo, segmentos):
        seg = tuple(seg)
        entry = registro.get(seg)
        if entry is None:
            salida.append(SeleccionCalibrada(float(p), SENTINEL_SIN_CALIBRADOR, False, seg))
        else:
            cal, ver = entry
            salida.append(SeleccionCalibrada(float(cal.aplicar(np.array([p]))[0]), ver, True, seg))
    return salida


def _wilson(k: int, n: int, z: float) -> tuple[float, float]:
    """Intervalo de Wilson para una proporción (robusto cerca de 0/1)."""
    if n == 0:
        return 0.0, 1.0
    phat = k / n
    d = 1 + z * z / n
    centro = (phat + z * z / (2 * n)) / d
    medio = z * np.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / d
    return max(0.0, centro - medio), min(1.0, centro + medio)


@dataclass
class ResultadoGate:
    aprobado: bool
    ece: float
    umbral_ece: float           # k · suelo de ruido (relativo al tamaño de muestra)
    bins_ok: int
    bins_total: int
    bate_crudo: bool
    detalle: str


def gate_despliegue(p_cal: np.ndarray, y: np.ndarray, p_crudo: np.ndarray, cfg: dict) -> ResultadoGate:
    """Gate de cobertura/fiabilidad OOS para el calibrador de NIVEL (contrato 5).

    Spec enmendada (pre-registro v0.2, §2.4). Binario. Aprueba solo si las tres:
      (i)   PRIMARIO — cobertura por bin: en cada bin con n ≥ n_min, p_media cae en
            el Wilson bilateral de la frecuencia observada; se cumple en ≥ fraccion_ok
            de los bins. (Consciente del tamaño de muestra.)
      (ii)  SECUNDARIO — ECE con umbral RELATIVO al suelo de ruido:
            ECE ≤ k · suelo, suelo = Σ sqrt(p(1−p)·n_b)/Σ n_b. (Estricto en n grande.)
      (iii) TERCIARIO — bate al crudo en log-loss.
    Si falla, NO se despliega y NO se relaja (el llamador rechaza, no ajusta umbrales).
    """
    c = cfg.get("calibracion_mercado", {})
    bins = int(c.get("gate_bins", 10))
    n_min = int(c.get("gate_n_min_bin", 50))
    z = float(c.get("gate_z", 1.96))                    # bilateral 95%
    k_ece = float(c.get("gate_k_ece", 1.5))             # múltiplo del suelo de ruido
    fraccion_ok = float(c.get("gate_fraccion_bins_ok", 0.8))

    p_cal = np.asarray(p_cal, float)
    y = np.asarray(y, float)
    cortes = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p_cal, cortes) - 1, 0, bins - 1)
    bins_ok = bins_total = 0
    ece_num = suelo_num = den = 0.0
    for b in range(bins):
        m = idx == b
        n = int(m.sum())
        if n < n_min:
            continue
        bins_total += 1
        freq = float(y[m].mean())
        pmed = float(p_cal[m].mean())
        lo, hi = _wilson(int(y[m].sum()), n, z)
        if lo <= pmed <= hi:
            bins_ok += 1
        ece_num += n * abs(pmed - freq)
        suelo_num += np.sqrt(max(pmed * (1 - pmed), 1e-12) * n)   # = n · se_bin
        den += n
    ece = float(ece_num / den) if den else 1.0
    suelo = float(suelo_num / den) if den else 1.0
    umbral_ece = k_ece * suelo
    bate = log_loss(_como_matriz(p_cal)[0], y.astype(int)) <= \
        log_loss(_como_matriz(np.asarray(p_crudo, float))[0], y.astype(int))
    cobertura_ok = bins_total > 0 and (bins_ok / bins_total) >= fraccion_ok
    aprobado = bool(cobertura_ok and ece <= umbral_ece and bate)
    detalle = (f"bins_ok {bins_ok}/{bins_total} (≥{fraccion_ok:.0%}), "
               f"ECE {ece:.4f} (≤ k·suelo {umbral_ece:.4f}, k={k_ece}), bate_crudo={bate}")
    if not aprobado:
        detalle += " → NO DESPLEGAR (no se relaja el criterio)"
    return ResultadoGate(aprobado, ece, umbral_ece, bins_ok, bins_total, bate, detalle)
