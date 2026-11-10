"""Orquestador del backtest OOS de `calibracion_tickets` (integración: encadena módulos existentes; NO contiene lógica de modelo).

Encadena: cestas ex-post (`cestas_historicas`) -> walk-forward estricto por folds -> ajuste de `c` en el tramo de ENTRENAMIENTO
(`calibracion_tickets.ajustar`) -> gate de cobertura BILATERAL en el tramo de TEST (`gate_cobertura_tickets`) -> protocolo de σ_g
pre-registrado (decisión 13) -> versión final + almacén (`versiones_calibrador_tickets`) -> reporte.

Alcance REAL (v1 de `calibracion_tickets`, sin cambiarlo): se ajusta UN `c` global y se marca cada segmento como calibrado o fallback; σ_ℓ NO se
ajusta (se conserva el valor de la config, documentado como extensión pendiente); σ_g se mantiene en 0 salvo que el protocolo de la decisión 13
se cumpla. El orquestador no inventa ajustes que el módulo no hace.

Walk-forward estricto: el eje es `t_dia` (instante de decisión de cada cesta). Los días se parten en `n_folds + 1` bloques; el primero solo
entrena; el fold k prueba el bloque k y entrena SOLO con cestas cuyo `t_dia <= inicio_del_test - embargo` (ya resueltas). El resultado de las
cestas de test nunca interviene en el ajuste de su fold (se verifica con un test que lo altera).

`calibrado_con`: 'oos_real' solo si los gates pasan Y el llamador declara que los datos son reales (`datos_reales=True`); con gates OK y sin esa
declaración queda 'sinteticos'; si fallan, 'no_calibrado'. NADA aquí apaga `modo_experimental`: eso es una decisión humana sobre el reporte.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from golmodel.backtest import calibracion_tickets as ct
from golmodel.config import hash_config
from golmodel.integracion import almacen_calibradores as almacen
from golmodel.integracion.cestas_historicas import ResultadoCestas, construir_cestas_historicas

EPS = 1e-12


@dataclass
class ResultadoFold:
    k: int
    test_desde: str | None
    test_hasta: str | None
    n_train: int
    n_test: int
    c_train: float | None = None
    gate_modo: str | None = None          # ok | estrecho | ancho | insuficiente | None (fold omitido)
    gate_aprobado: bool | None = None
    gate_cobertura: float | None = None
    gate_detalle: str | None = None
    logloss_test: float | None = None
    omitido: str | None = None


@dataclass
class ResultadoBacktest:
    version: ct.VersionCalibradorTickets
    gates_ok: bool
    calibrado_con: str
    motivo: str
    folds: list[ResultadoFold]
    sigma_g: float
    protocolo_sigma_g: dict
    dataset: dict
    ajuste_final: dict
    resultado_json: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- decisiones puras (probadas por separado)

def decidir_gates(folds: list[ResultadoFold], folds_min_validos: int) -> tuple[bool, str]:
    """gates_ok = al menos `folds_min_validos` folds en 'ok' Y NINGÚN fold estrecho/ancho. Los 'insuficiente' no cuentan ni a favor ni en contra
    (no hay muestra para juzgar), pero tampoco rescatan: si no hay suficientes 'ok', no pasa."""
    ok = sum(1 for f in folds if f.gate_modo == "ok")
    malos = [f for f in folds if f.gate_modo in ("estrecho", "ancho")]
    insuf = sum(1 for f in folds if f.gate_modo == "insuficiente")
    if malos:
        return False, "gate de cobertura FALLA en fold(s) " + ", ".join(f"{f.k} ({f.gate_modo})" for f in malos)
    if ok < folds_min_validos:
        return False, f"solo {ok} fold(s) con gate 'ok' (mínimo {folds_min_validos}); {insuf} sin muestra suficiente"
    return True, f"{ok} fold(s) con gate 'ok', ninguno estrecho/ancho ({insuf} sin muestra suficiente)"


def decidir_calibrado_con(gates_ok: bool, datos_reales: bool) -> str:
    if not gates_ok:
        return "no_calibrado"
    return "oos_real" if datos_reales else "sinteticos"


def _logloss_por_cesta(res: list, cestas: list[ct.CestaHistorica]) -> np.ndarray:
    y = np.array([1.0 if c.gano else 0.0 for c in cestas])
    p = np.clip(np.array([r.p_media for r in res]), EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def evaluar_sigma_g(perdidas_base: list[np.ndarray], perdidas_cand: list[np.ndarray], proto: dict, n_candidatos: int,
                    semilla: int = 0) -> dict:
    """Protocolo de σ_g (decisión 13) sobre pérdidas por cesta de test, por fold (misma cesta en base y candidato; `base` = σ_g=0).
    Pasa SOLO si: (i) reducción relativa del log-loss medio >= `reduccion_logloss_min`; (ii) IC de bootstrap PAREADO de la diferencia media
    excluye 0 por el lado de la mejora, al nivel `ic_nivel` corregido por Bonferroni sobre los `n_candidatos` niveles (nunca más laxo); (iii) mejora
    (diferencia media > 0) en >= `folds_estables_min` folds."""
    d_folds = [b - c for b, c in zip(perdidas_base, perdidas_cand) if len(b)]
    if not d_folds:
        return {"pasa": False, "motivo": "sin cestas de test"}
    d = np.concatenate(d_folds)
    base = float(np.concatenate([b for b in perdidas_base if len(b)]).mean())
    red = float(d.mean() / base) if base > 0 else 0.0
    alfa = (1.0 - float(proto["ic_nivel"])) / max(1, int(n_candidatos))                       # unilateral (mejora), Bonferroni
    rng = np.random.default_rng(int(semilla))
    n = len(d)
    medias = np.array([d[rng.integers(0, n, n)].mean() for _ in range(int(proto["bootstrap_n"]))])
    lo = float(np.quantile(medias, alfa))
    estables = int(sum(1 for x in d_folds if x.mean() > 0))
    cond = {"reduccion": red >= float(proto["reduccion_logloss_min"]), "ic_excluye_0": lo > 0.0,
            "folds_estables": estables >= int(proto["folds_estables_min"])}
    return {"pasa": all(cond.values()), "reduccion_relativa": red, "ic_inferior_dif": lo, "alfa_unilateral": alfa,
            "folds_con_mejora": estables, "n_cestas": int(n), "condiciones": cond}


# ----------------------------------------------------------------------------- construcción de folds

def partir_en_folds(t_dia: list[datetime], n_folds: int, embargo_dias: int) -> list[dict]:
    """[{k, test_desde, test_hasta, idx_train, idx_test}] — bloques de días consecutivos; el bloque 0 solo entrena (expanding window)."""
    dias = sorted({t for t in t_dia})
    if n_folds < 1 or len(dias) < n_folds + 1:
        return []
    cortes = np.array_split(np.arange(len(dias)), n_folds + 1)
    folds = []
    for k in range(1, n_folds + 1):
        d_test = [dias[i] for i in cortes[k]]
        desde, hasta = d_test[0], d_test[-1]
        limite = desde - timedelta(days=embargo_dias)
        folds.append({"k": k, "test_desde": desde, "test_hasta": hasta,
                      "idx_train": [i for i, t in enumerate(t_dia) if t <= limite],
                      "idx_test": [i for i, t in enumerate(t_dia) if desde <= t <= hasta]})
    return folds


def _cfg_con_sigma(cfg_v3: dict, sigma_g: float, n_draws: int | None) -> dict:
    c = copy.deepcopy(cfg_v3)
    c["calibracion_tickets"]["sigma_g"] = float(sigma_g)
    if n_draws is not None:
        c["calibracion_tickets"]["n_draws"] = int(n_draws)
    return c


def _entre_ligas(c: ct.CestaHistorica) -> bool:
    return len({p.liga for p in c.piernas}) >= 2


# ----------------------------------------------------------------------------- orquestación

def ejecutar_con_cestas(res: ResultadoCestas, cfg_v3: dict, *, desde: datetime, hasta: datetime, semilla: int = 0,
                        datos_reales: bool = False, n_draws: int | None = None, progreso=lambda s: None) -> ResultadoBacktest:
    bo = cfg_v3["backtest_oos"]
    proto = bo["protocolo_sigma_g"]
    cestas = list(res.cestas)
    if not cestas:
        raise ValueError("sin cestas resueltas en el rango: no hay nada que calibrar (¿histórico cargado? ¿cuotas de la casa elegida? "
                         f"omisiones: {dict(res.omisiones)})")
    t_dia = [m.t_dia for m in res.meta]
    cfg0 = _cfg_con_sigma(cfg_v3, cfg_v3["calibracion_tickets"].get("sigma_g", 0.0), n_draws)
    folds_idx = partir_en_folds(t_dia, int(bo["n_folds"]), int(bo["embargo_dias"]))
    entre = [i for i, c in enumerate(cestas) if _entre_ligas(c)]
    dias_cob = (max(t_dia) - min(t_dia)).days if t_dia else 0
    dataset = {"cestas_resueltas": len(cestas), "cestas_excluidas": len(res.excluidas),
               "fraccion_excluida": (len(res.excluidas) / (len(cestas) + len(res.excluidas))) if (cestas or res.excluidas) else None,
               "omisiones": dict(res.omisiones), "cestas_entre_ligas": len(entre), "dias_cobertura": dias_cob,
               "ligas": sorted({p.liga for c in cestas for p in c.piernas}),
               "tasa_gano": float(np.mean([c.gano for c in cestas])) if cestas else None,
               "excluidas_por_tipo": {k: sum(1 for m in res.excluidas if m.resultado == k) for k in ("anulada", "pendiente")}}

    # 1) σ_g: protocolo pre-registrado (solo si hay evidencia suficiente; si no, σ_g = 0 y se dice por qué)
    sigma_g, proto_res = 0.0, {"ejecutado": False}
    grid = [float(g) for g in proto["grid"]]
    if len(entre) < int(proto["min_cestas_entre_ligas"]):
        proto_res["motivo"] = f"solo {len(entre)} cestas entre-ligas (< {proto['min_cestas_entre_ligas']}): σ_g = 0"
    elif dias_cob < int(proto["min_dias_cobertura"]):
        proto_res["motivo"] = f"cobertura de {dias_cob} días (< {proto['min_dias_cobertura']}): σ_g = 0"
    elif not folds_idx:
        proto_res["motivo"] = "sin folds: σ_g = 0"
    else:
        proto_res = {"ejecutado": True, "candidatos": {}}
        base_losses, ent = [], set(entre)
        fits = {}                                     # (σ, k) -> c_train, reutilizable
        for g in [0.0] + grid:
            perd = []
            for f in folds_idx:
                tr = [cestas[i] for i in f["idx_train"]]
                te = [cestas[i] for i in f["idx_test"] if i in ent]
                if len(tr) < int(bo["min_cestas_entrenamiento"]) or not te:
                    perd.append(np.array([]))
                    continue
                cfg_g = _cfg_con_sigma(cfg_v3, g, n_draws)
                progreso(f"σ_g={g} fold {f['k']}: ajustando c con {len(tr)} cestas")
                c_k = ct.ajustar(tr, cfg_g, semilla).c_global
                perd.append(_logloss_por_cesta(ct._resultados(te, c_k, cfg_g, semilla), te))
            if g == 0.0:
                base_losses = perd
            else:
                proto_res["candidatos"][str(g)] = evaluar_sigma_g(base_losses, perd, proto, len(grid), semilla)
        buenos = [(float(g), r) for g, r in proto_res["candidatos"].items() if r["pasa"]]
        if buenos:
            sigma_g = max(buenos, key=lambda x: x[1]["reduccion_relativa"])[0]
        proto_res["sigma_g_elegido"] = sigma_g

    # 2) folds OOS con el σ_g elegido: ajuste en train, gate en test
    cfg_s = _cfg_con_sigma(cfg_v3, sigma_g, n_draws)
    folds: list[ResultadoFold] = []
    for f in folds_idx:
        tr = [cestas[i] for i in f["idx_train"]]
        te = [cestas[i] for i in f["idx_test"]]
        rf = ResultadoFold(f["k"], f["test_desde"].isoformat(), f["test_hasta"].isoformat(), len(tr), len(te))
        if len(tr) < int(bo["min_cestas_entrenamiento"]):
            rf.omitido = f"entrenamiento insuficiente ({len(tr)} < {bo['min_cestas_entrenamiento']})"
        elif not te:
            rf.omitido = "sin cestas de test"
        else:
            progreso(f"fold {f['k']}: ajustando c con {len(tr)} cestas, probando en {len(te)}")
            rf.c_train = float(ct.ajustar(tr, cfg_s, semilla).c_global)
            g = ct.gate_cobertura_tickets(te, rf.c_train, cfg_s, semilla)
            rf.gate_modo, rf.gate_aprobado, rf.gate_cobertura, rf.gate_detalle = g.modo, bool(g.aprobado), float(g.cobertura), g.detalle
            rf.logloss_test = float(_logloss_por_cesta(ct._resultados(te, rf.c_train, cfg_s, semilla), te).mean())
        folds.append(rf)
    gates_ok, motivo = decidir_gates([f for f in folds if f.omitido is None], int(bo["folds_min_validos"]))
    if not [f for f in folds if f.omitido is None]:
        gates_ok, motivo = False, "ningún fold evaluable: " + "; ".join(f"fold {f.k}: {f.omitido}" for f in folds) if folds else "sin folds (muy pocos días)"

    # 3) versión final: c sobre TODAS las cestas (la que se desplegaría) — su validez OOS es la de los folds de arriba
    final = ct.ajustar(cestas, cfg_s, semilla) if cestas else None
    cal = decidir_calibrado_con(gates_ok, datos_reales)
    cfg_ct = cfg_s["calibracion_tickets"]
    version = ct.VersionCalibradorTickets(
        datos_hasta=hasta, config_hash=hash_config({"calibracion_tickets": cfg_ct, "backtest_oos": bo,
                                                    "rangos_cuota": cfg_v3["calibracion_mercado"]["rangos_cuota"]}),
        n_draws=int(cfg_ct["n_draws"]), semilla=int(semilla), metodo="global_walkforward_oos",
        segmentos_calibrados=final.segmentos_calibrados if final else [], segmentos_fallback=final.segmentos_fallback if final else [],
        c_global=float(final.c_global) if final else float("nan"), sigma_liga=cfg_ct.get("sigma_liga", 0.0), sigma_g=float(sigma_g),
        calibrado_con=cal)
    out = ResultadoBacktest(version, gates_ok, cal, motivo, folds, float(sigma_g), proto_res, dataset,
                            {"c_global": version.c_global, "coste": final.coste if final else None,
                             "n_segmentos_calibrados": len(version.segmentos_calibrados),
                             "n_segmentos_fallback": len(version.segmentos_fallback),
                             "sigma_liga": "NO ajustado (extensión de calibracion_tickets pendiente): valor de la config"})
    out.resultado_json = {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "datos_reales_declarado": bool(datos_reales),
                          "motivo": motivo, "folds": [asdict(f) for f in folds], "protocolo_sigma_g": proto_res,
                          "dataset": dataset, "ajuste_final": out.ajuste_final, "backtest_oos": bo}
    return out


def ejecutar(conn, cfg_v2: dict, cfg_v3: dict, *, desde: datetime, hasta: datetime, casa: str | None = None, semilla: int = 0,
             datos_reales: bool = False, n_draws: int | None = None, guardar: bool = True, progreso=lambda s: None) -> tuple[ResultadoBacktest, int | None]:
    """Lee la BD, ejecuta el backtest y (si `guardar`) escribe la versión en `versiones_calibrador_tickets`. Devuelve (resultado, id)."""
    casa = casa or cfg_v3["backtest_oos"]["casa"]
    progreso(f"construyendo cestas ex-post {desde.date()} → {hasta.date()} (casa {casa})")
    cestas = construir_cestas_historicas(conn, desde, hasta, cfg_v2, cfg_v3, casa=casa, semilla=semilla)
    r = ejecutar_con_cestas(cestas, cfg_v3, desde=desde, hasta=hasta, semilla=semilla, datos_reales=datos_reales, n_draws=n_draws,
                            progreso=progreso)
    vid = almacen.guardar_version(conn, r.version, gates_ok=r.gates_ok, resultado=r.resultado_json) if guardar else None
    if guardar:
        conn.commit()
    return r, vid


# ----------------------------------------------------------------------------- reporte

def reporte_md(r: ResultadoBacktest, *, version_id: int | None, generado: datetime | None = None) -> str:
    d, v = r.dataset, r.version
    L = ["# Backtest OOS · calibración de tickets", "",
         f"- Generado: {(generado or datetime.now(timezone.utc)).strftime('%Y-%m-%d %H:%M UTC')}",
         f"- Datos hasta: {v.datos_hasta.isoformat()} · semilla {v.semilla} · n_draws {v.n_draws} · config_hash `{v.config_hash}`",
         f"- Versión guardada: {'id ' + str(version_id) if version_id else '**NO guardada** (--sin-guardar)'}", "",
         "## Veredicto", "",
         f"- **Gates de cobertura bilateral: {'SUPERADOS' if r.gates_ok else 'NO SUPERADOS'}** — {r.motivo}",
         f"- `calibrado_con`: **{r.calibrado_con}**" + ("" if r.calibrado_con != "sinteticos" else
                                                   " (gates OK pero NO se declararon datos reales: no sirve para apagar `modo_experimental`)"),
         f"- `c_global` = **{v.c_global:.4f}** · σ_g = **{v.sigma_g}** · σ_ℓ = {v.sigma_liga} (NO ajustado)",
         "- `modo_experimental` NO se modifica aquí: apagarlo es una decisión humana, solo si los gates pasan con `oos_real`.", "",
         "## Datos", "",
         f"- Cestas resueltas: {d['cestas_resueltas']} · excluidas: {d['cestas_excluidas']} "
         f"(**{'n/d' if d['fraccion_excluida'] is None else f'{100 * d['fraccion_excluida']:.1f}%'} del total**; anuladas {d['excluidas_por_tipo']['anulada']}, "
         f"pendientes {d['excluidas_por_tipo']['pendiente']})",
         f"- Entre-ligas: {d['cestas_entre_ligas']} · cobertura {d['dias_cobertura']} días · ligas: {', '.join(d['ligas']) or '—'}",
         f"- Tasa de 'ganan todas' observada: {'n/d' if d['tasa_gano'] is None else f'{d['tasa_gano']:.3%}'}",
         f"- Partidos/días no usados: {d['omisiones'] or 'ninguno'}", "",
         "## Folds walk-forward (ajuste SOLO en train; gate en test)", "",
         "| fold | test | n_train | n_test | c (train) | gate | cobertura | log-loss test | nota |", "|---|---|---|---|---|---|---|---|---|"]
    for f in r.folds:
        if f.omitido:
            L.append(f"| {f.k} | {f.test_desde[:10]} → {f.test_hasta[:10]} | {f.n_train} | {f.n_test} | — | — | — | — | omitido: {f.omitido} |")
        else:
            L.append(f"| {f.k} | {f.test_desde[:10]} → {f.test_hasta[:10]} | {f.n_train} | {f.n_test} | {f.c_train:.4f} | **{f.gate_modo}** | "
                     f"{f.gate_cobertura:.2f} | {f.logloss_test:.4f} | {f.gate_detalle} |")
    p = r.protocolo_sigma_g
    L += ["", "## Protocolo de σ_g (decisión 13)", ""]
    if not p.get("ejecutado"):
        L.append(f"- No se ejecutó: {p.get('motivo')}. σ_g = 0 (valor por defecto del protocolo).")
    else:
        for g, x in p["candidatos"].items():
            L.append(f"- σ_g = {g}: " + ("**PASA**" if x["pasa"] else "no pasa") + f" — reducción relativa {100 * x.get('reduccion_relativa', 0):.2f}% "
                     f"(mín 1%), IC inferior de la mejora {x.get('ic_inferior_dif', float('nan')):.5f} (debe ser > 0), "
                     f"folds con mejora {x.get('folds_con_mejora')} · condiciones {x.get('condiciones')}")
        L.append(f"- σ_g elegido: **{p['sigma_g_elegido']}**")
    L += ["", "## Segmentos", "",
          f"- Calibrados (≥ n_min cestas): {r.ajuste_final['n_segmentos_calibrados']} · fallback global: {r.ajuste_final['n_segmentos_fallback']}",
          "", "## Qué NO cubre este reporte", "",
          "- `σ_ℓ` no se ajusta (extensión pendiente de `calibracion_tickets`); `c` es global con segmentos marcados calibrado/fallback.",
          "- La validez OOS es la de los folds de arriba; `c_global` final se ajusta con TODAS las cestas.",
          "- Las cestas anuladas/pendientes se excluyen del ajuste (revisión de la decisión 5): la fracción excluida está arriba; si es material, es sesgo potencial.",
          "- El histórico no reproduce CRS ni frescura (decisión 5: no se filtra por aprobación); cuotas históricas con marca temporal aproximada (inicio − 2 días).",
          "- Cerebro 2 no interviene en este backtest: validar la ruta `APROBADO` exige además un backtest de tickets+auditor (no existe aún).", ""]
    return "\n".join(L)
