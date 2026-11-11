"""BD -> «Soñadoras» + auditoría de Cerebro 2 + presentación. Adaptador sin lógica de modelo (la lógica vive en `motor/sonadoras.py`).

* Candidatas y versión del calibrador: las MISMAS que usa el ticket del día (`integracion.candidatas`, `benchmark.resolver_version`).
* Cerebro 2 AUDITA la soñadora con su ruta normal (`riesgo.evaluar`), tratándola como un ticket con `modo_experimental=True` y techo `CON_RESERVAS`
  fijados aquí (no dependen de la config): jamás puede salir APROBADA. Si por cualquier razón el auditor devolviera APROBADO, se degrada a
  CON_RESERVAS (defensa en profundidad, con test). Con EV negativo lo normal es RECHAZADO por el gate de EV conservador: es el resultado correcto.
* NO se persiste en `auditoria_decisiones` (su CHECK solo admite 'tickets' y 'cerebro2' y no se toca el esquema de la 004): se calcula al pedirla.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from golmodel.cerebro2 import riesgo
from golmodel.cerebro2.decision import Veredicto
from golmodel.config import hash_config
from golmodel.integracion import benchmark as bm
from golmodel.integracion.candidatas import construir_candidatas
from golmodel.modelos import incertidumbre as inc
from golmodel.motor import dependencia as dep
from golmodel.motor import sonadoras as sn
from golmodel.motor import tickets as tk

TITULO = "🚨 Soñadoras (Alto Riesgo / EV Negativo)"
AVISO = "Esta apuesta es un juego de azar con ventaja para la casa. No forma parte de la estrategia de inversión principal."
ESTADO_MAXIMO = "CON_RESERVAS"


@dataclass(frozen=True)
class DecisionSonadora:
    dia: date
    ahora: datetime
    salida: sn.SalidaSonadoras
    veredicto: Veredicto | None
    estado_cerebro2: str | None          # ya con el tope aplicado (nunca APROBADO)
    stake_u: float


def resolver_stake(cfg_v3: dict, stake_u: float | None = None) -> float:
    """Stake FIJO simbólico (stake_pct_banca / unidad_pct_banca = 0.1 u por defecto) o el monto definido por el usuario (0 < x <= stake_max_u)."""
    sc = cfg_v3["sonadoras"]
    if stake_u is None:
        return float(sc["stake_pct_banca"]) / float(cfg_v3["bankroll"]["unidad_pct_banca"])
    if not (isinstance(stake_u, (int, float)) and math.isfinite(stake_u) and 0 < stake_u <= float(sc["stake_max_u"])):
        raise ValueError(f"stake_u debe estar en (0, {sc['stake_max_u']}] unidades; recibido {stake_u!r}")
    return float(stake_u)


def limitar_estado(estado: str) -> str:
    """Una soñadora NUNCA es APROBADA para inversión: el máximo es CON_RESERVAS."""
    return ESTADO_MAXIMO if estado == "APROBADO" else estado


def como_salida_tickets(s: sn.Sonadora, version, cfg_v3: dict, ahora: datetime, n_candidatas: int) -> tk.SalidaTickets:
    """Envuelve la soñadora en la estructura que Cerebro 2 audita. modo_experimental/techo se FIJAN aquí (la soñadora no es promovible)."""
    ic_rel = (s.ic[1] - s.ic[0]) / s.p_media if s.p_media > 0 else math.inf
    ticket = tk.Ticket(
        tipo="sonadora", n=s.n, partidos=tuple(sorted(p.partido_id for p in s.piernas)), piernas=s.piernas, p_media=s.p_media,
        p_conservadora=s.p_conservadora, p_independencia=s.p_independencia, lift=s.lift, ic=s.ic, ic_rel=float(ic_rel),
        error_mc=s.error_mc, metodo="montecarlo_jerarquico", ev_cons=s.ev_cons, cuota_combinada=s.cuota_combinada,
        cuota_combinada_tipo=tk.CUOTA_COMBINADA_TIPO, stake_u=s.stake_u, log_crecimiento_sin_tope=0.0, log_crecimiento_con_tope=0.0,
        techo_estado=ESTADO_MAXIMO, promovible=False, modo_experimental=True, semilla=s.semilla, n_draws=s.n_draws)
    aud = tk.RegistroAuditoria(
        version_tickets=sn.VERSION, version_calibracion_mercado=tuple(sorted({p.calibrador_version for p in s.piernas})),
        version_incertidumbre=inc.VERSION, version_dependencia=dep.VERSION,
        version_calibracion_tickets=tk._id_calibracion_tickets(version), calibrado_con=version.calibrado_con,
        c_seg={"c_global": float(version.c_global)}, sigma_g=float(version.sigma_g), sigma_liga=version.sigma_liga,
        modo_experimental=True, techo_estado=ESTADO_MAXIMO, semilla=s.semilla, n_draws=s.n_draws, ahora=ahora.isoformat(),
        constraints_aplicados=dict(cfg_v3["sonadoras"]), config_hash_restricciones=hash_config(cfg_v3["sonadoras"]),
        n_candidatas=n_candidatas, n_piernas_sin_calibrar=sum(1 for p in s.piernas if not p.calibrado),
        n_piernas_elegibles=s.n, n_piernas_shortlist=s.n, n_cestas_evaluadas=1, n_cestas_factibles=1, bloqueos_piernas={},
        bloqueos_cestas={}, motivo_abstencion=None, categoria_abstencion=None, rechazos_piernas=(), cestas=())
    return tk.SalidaTickets(estado="PROPUESTO", ticket=ticket, por_tamano={}, auditoria=aud)


def generar_sonadoras_dia(conn, dia: date, ahora: datetime, cfg_v2: dict, cfg_v3: dict, *, stake_u: float | None = None,
                          version_cal=None, semilla: int | None = None) -> DecisionSonadora:
    stake = resolver_stake(cfg_v3, stake_u)
    cands = construir_candidatas(conn, dia, ahora, cfg_v2, cfg_v3)
    version_cal = version_cal or bm.resolver_version(conn, ahora, cfg_v3)
    semilla = int(dia.strftime("%Y%m%d")) if semilla is None else int(semilla)
    salida = sn.generar_sonadoras(list(cands.candidatas), version_cal, cfg_v3, semilla=semilla, ahora=ahora, stake_u=stake)
    if salida.sonadora is None:
        return DecisionSonadora(dia, ahora, salida, None, None, stake)
    mercado = bm.mercado_de_candidatas(conn, cands.candidatas, ahora, cfg_v2, cfg_v3)
    v = riesgo.evaluar(como_salida_tickets(salida.sonadora, version_cal, cfg_v3, ahora, len(cands.candidatas)), mercado, cfg_v3)
    return DecisionSonadora(dia, ahora, salida, v, limitar_estado(v.estado), stake)


def _prob_texto(p: float) -> str:
    return f"{100 * p:.1f}%" if p >= 0.001 else f"{100 * p:.3f}%" if p >= 0.00001 else f"{p:.2e}"


def presentar(conn, d: DecisionSonadora, zona: ZoneInfo) -> dict:
    """Payload para la API/UI: título y aviso fijos, probabilidad real, EV, stake, piernas legibles, estado de Cerebro 2 y, si no hay, el motivo."""
    from golmodel.servicios.sugerencias import texto_jugada
    s = d.salida
    base = {"titulo": TITULO, "aviso": AVISO, "dia": d.dia.isoformat(), "estado": s.estado, "motivo": s.motivo, "diagnostico": s.diagnostico,
            "stake_u": d.stake_u, "stake_tipo": "fijo simbólico (sin Kelly)", "promovible": False, "estado_cerebro2": d.estado_cerebro2,
            "cerebro2": None if d.veredicto is None else {"categoria": d.veredicto.categoria, "razon_principal": d.veredicto.razon_principal},
            "sonadora": None}
    if s.sonadora is None:
        return base
    x = s.sonadora
    piernas = []
    for p in x.piernas:
        n = conn.execute("""SELECT el.nombre AS local, ev.nombre AS visitante, pa.inicio FROM partidos pa
                            JOIN equipos el ON el.id = pa.local_id JOIN equipos ev ON ev.id = pa.visitante_id WHERE pa.id = %s""",
                         (p.partido_id,)).fetchone() or {"local": "?", "visitante": "?", "inicio": None}
        piernas.append({"partido_id": p.partido_id, "local": n["local"], "visitante": n["visitante"],
                        "hora": n["inicio"].astimezone(zona).strftime("%H:%M") if n["inicio"] else "—",
                        "jugada": texto_jugada(p.selecciones[0], n["local"], n["visitante"])[0], "cuota": p.cuota,
                        "p_calibrada": p.p_calibrada, "p_conservadora": p.p_conservadora, "crs_total": p.crs_total})
    base["sonadora"] = {"n": x.n, "cuota_combinada": x.cuota_combinada, "probabilidad_real": x.p_media,
                        "probabilidad_real_texto": _prob_texto(x.p_media), "p_conservadora": x.p_conservadora,
                        "p_independencia": x.p_independencia, "lift": x.lift, "ev_real": x.ev_real, "ev_cons": x.ev_cons, "piernas": piernas,
                        "uno_de_cada": (1.0 / x.p_media) if x.p_media > 0 else None}
    return base
