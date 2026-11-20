"""Backtest walk-forward NBA (2026-11-20): ratings ORtg/DRtg/Pace desde box scores + localía + descanso, contra el spread y el total de CIERRE.

Datos (públicos, mirror de hoopR/ESPN; ~110 MB con clon parcial):
    git clone --depth 1 --filter=blob:none --sparse https://github.com/sportsdataverse/hoopR-nba-data.git hoopr
    cd hoopr && git sparse-checkout set nba/team_box nba/schedules nba/betting_lines
Uso:  python scripts/backtest_nba.py hoopr/nba salida.pkl
Resultado (docs/decisiones.md): el modelo NO aporta nada sobre la línea de cierre; ver la entrada «NBA».
"""
import sys
from math import erf, sqrt

import numpy as np
import pandas as pd

RAIZ = sys.argv[1]
SEMS = list(range(2019, 2028))                      # 2026 = temporada 2025-26; 2027 = la actual (parcial)
Phi = np.vectorize(lambda x: 0.5 * (1 + erf(x / sqrt(2))))

tb = pd.concat([pd.read_parquet(f"{RAIZ}/team_box/parquet/team_box_{s}.parquet") for s in SEMS], ignore_index=True)
tb = tb[tb.season_type.isin([2, 3])].copy()           # regular + playoffs (play-in cae en 2/3 según la fuente)
for c in ("field_goals_attempted", "offensive_rebounds", "total_turnovers", "free_throws_attempted", "team_score", "opponent_team_score"):
    tb[c] = pd.to_numeric(tb[c], errors="coerce")
tb["tov"] = tb["total_turnovers"].fillna(tb["turnovers"])
tb["poss"] = tb.field_goals_attempted - tb.offensive_rebounds + tb.tov + 0.44 * tb.free_throws_attempted
h = tb[tb.team_home_away == "home"].set_index("game_id")
a = tb[tb.team_home_away == "away"].set_index("game_id")
g = h[["season", "season_type", "game_date_time", "team_id", "team_score", "poss", "opponent_team_id"]].join(
    a[["team_id", "team_score", "poss"]], rsuffix="_a", how="inner")
g.columns = ["season", "tipo", "fecha", "h", "pts_h", "poss_h", "_x", "a", "pts_a", "poss_a"]
g = g.drop(columns="_x").reset_index().dropna(subset=["pts_h", "pts_a", "poss_h", "poss_a"])
g["fecha"] = pd.to_datetime(g["fecha"], utc=True)
g = g.sort_values(["fecha", "game_id"]).reset_index(drop=True)
g["poss"] = (g.poss_h + g.poss_a) / 2
bl = pd.read_parquet(f"{RAIZ}/betting_lines/closing_lines_odds_api.parquet").drop_duplicates("game_id")
g = g.merge(bl[["game_id", "home_point", "over_under"]], on="game_id", how="left")
g["mk_margin"] = -g["home_point"]
print("juegos", len(g), "con línea", int(g.mk_margin.notna().sum()), "temporadas", sorted(g.season.unique()))

# ---------------------------------------------------------------- ratings en línea
HL, K0, REG, HFA = 14.0, 6.0, 0.30, 2.6               # media vida (juegos), prior (juegos), regresión de inicio de temporada, localía (pts)
dec = 0.5 ** (1 / HL)
est = {}                                              # equipo -> dict(sw, o, d, p, ult, n)
lg = {"o": 112.0, "p": 99.0}
def estado(t):
    return est.setdefault(t, {"sw": 0.0, "o": 0.0, "d": 0.0, "p": 0.0, "ult": None})
def rating(t, k):
    e = estado(t)
    prior = {"o": lg["o"], "d": lg["o"], "p": lg["p"]}[k]
    return (e[k] + K0 * prior) / (e["sw"] + K0)
def descanso(t, f):
    u = estado(t)["ult"]
    return 7 if u is None else min((f - u).days, 7)

filas, season_prev, n_lg, s_o, s_p = [], None, 0, 0.0, 0.0
for r in g.itertuples(index=False):
    if season_prev is not None and r.season != season_prev:           # nueva temporada: regresa a la media
        for e in est.values():
            for k in ("sw", "o", "d", "p"):
                e[k] *= (1 - REG)
    season_prev = r.season
    pace = rating(r.h, "p") + rating(r.a, "p") - lg["p"]
    o_h = rating(r.h, "o") + rating(r.a, "d") - lg["o"]
    o_a = rating(r.a, "o") + rating(r.h, "d") - lg["o"]
    pts_h, pts_a = pace * o_h / 100, pace * o_a / 100
    rd_h, rd_a = descanso(r.h, r.fecha), descanso(r.a, r.fecha)
    filas.append((r.game_id, r.season, r.tipo, pts_h - pts_a + HFA, pts_h + pts_a, rd_h, rd_a))
    # actualizar con el resultado
    for t, ptsf, ptsc in ((r.h, r.pts_h, r.pts_a), (r.a, r.pts_a, r.pts_h)):
        e = estado(t)
        e["sw"] = e["sw"] * dec + 1
        e["o"] = e["o"] * dec + 100 * ptsf / r.poss
        e["d"] = e["d"] * dec + 100 * ptsc / r.poss
        e["p"] = e["p"] * dec + r.poss
        e["ult"] = r.fecha
    n_lg += 1
    s_o = 0.995 * s_o + 100 * (r.pts_h + r.pts_a) / (2 * r.poss)
    s_p = 0.995 * s_p + r.poss
    w = (1 - 0.995 ** n_lg) / (1 - 0.995)
    lg["o"], lg["p"] = s_o / w, s_p / w
pr = pd.DataFrame(filas, columns=["game_id", "season", "tipo", "m_mod", "t_mod", "rd_h", "rd_a"])
d = g.merge(pr[["game_id", "m_mod", "t_mod", "rd_h", "rd_a"]], on="game_id")
d["margen"] = d.pts_h - d.pts_a
d["total"] = d.pts_h + d.pts_a
d["b2b_h"] = (d.rd_h <= 1).astype(int)
d["b2b_a"] = (d.rd_a <= 1).astype(int)
d["gano_h"] = (d.margen > 0).astype(int)
d = d[d.season >= 2021]                                                # primeras temporadas = calentamiento
print("evaluables", len(d))

# ---------------------------------------------------------------- evaluación por temporada (blend ajustado SOLO con temporadas anteriores)
def ols(X, y):
    return np.linalg.lstsq(X, y, rcond=None)[0]

res = []
for s in (2024, 2025, 2026):
    tr = d[(d.season < s) & d.mk_margin.notna() & (d.tipo == 2)]
    te = d[(d.season == s)]
    Xtr = np.column_stack([np.ones(len(tr)), tr.mk_margin, tr.m_mod, tr.b2b_h - tr.b2b_a])
    beta = ols(Xtr, tr.margen)
    sig = float(np.std(tr.margen - Xtr @ beta))
    sig_mk = float(np.std(tr.margen - tr.mk_margin))
    sig_mod = float(np.std(tr.margen - tr.m_mod))
    tm = te[te.mk_margin.notna()].copy()
    tm["m_blend"] = np.column_stack([np.ones(len(tm)), tm.mk_margin, tm.m_mod, tm.b2b_h - tm.b2b_a]) @ beta
    # total: mismo esquema
    tt = d[(d.season < s) & d.over_under.notna() & (d.tipo == 2)]
    Xt = np.column_stack([np.ones(len(tt)), tt.over_under, tt.t_mod]); bt = ols(Xt, tt.total)
    tm["t_blend"] = np.column_stack([np.ones(len(tm)), tm.over_under, tm.t_mod]) @ bt
    p = lambda m, sg: np.clip(Phi(m / sg), 1e-4, 1 - 1e-4)
    ll = lambda pp: float(-np.mean(np.where(tm.gano_h == 1, np.log(pp), np.log(1 - pp))))
    acc = lambda m: float(np.mean((m > 0) == (tm.gano_h == 1)))
    print(f"\n=== Temporada {s-1}-{str(s)[2:]}: {len(tm)} juegos con línea de cierre (blend ajustado con {len(tr)} juegos previos) ===")
    print(f"pesos blend margen: const {beta[0]:.2f}  mercado {beta[1]:.3f}  modelo {beta[2]:.3f}  b2b {beta[3]:.2f}   (sigma mercado {sig_mk:.2f}, modelo {sig_mod:.2f}, blend {sig:.2f})")
    print(f"MAE margen:  mercado {np.mean(abs(tm.margen - tm.mk_margin)):.3f}  modelo {np.mean(abs(tm.margen - tm.m_mod)):.3f}  blend {np.mean(abs(tm.margen - tm.m_blend)):.3f}")
    print(f"MAE total:   mercado {np.mean(abs(tm.total - tm.over_under)):.3f}  modelo {np.mean(abs(tm.total - tm.t_mod)):.3f}  blend {np.mean(abs(tm.total - tm.t_blend)):.3f}")
    print(f"Ganador: acierto mercado {100*acc(tm.mk_margin):.1f}%  modelo {100*acc(tm.m_mod):.1f}%  blend {100*acc(tm.m_blend):.1f}%   log loss mercado {ll(p(tm.mk_margin, sig_mk)):.4f}  modelo {ll(p(tm.m_mod, sig_mod)):.4f}  blend {ll(p(tm.m_blend, sig)):.4f}")
    # calibración del favorito (blend)
    pb = p(tm.m_blend, sig); pf = np.maximum(pb, 1 - pb); okf = np.where(pb >= .5, tm.gano_h == 1, tm.gano_h == 0)
    for lo, hi in ((0.5, 0.65), (0.65, 0.8), (0.8, 0.9), (0.9, 1.01)):
        m_ = (pf >= lo) & (pf < hi)
        if m_.sum() >= 15:
            print(f"   favorito {lo:.0%}-{min(hi,1):.0%}: n={int(m_.sum())} prometido {100*pf[m_].mean():.1f}% real {100*okf[m_].mean():.1f}%")
    # ATS y totales cuando el blend/modelo discrepa del mercado
    for nombre, col in (("modelo puro", "m_mod"), ("blend", "m_blend")):
        for t in (1.5, 3.0):
            dif = tm[col] - tm.mk_margin
            ap = tm[abs(dif) >= t]
            if len(ap) < 25: continue
            lado_h = ap[col] - ap.mk_margin > 0
            cubre = np.where(lado_h, ap.margen > ap.mk_margin, ap.margen < ap.mk_margin)
            push = ap.margen == ap.mk_margin
            gan = float(np.sum(cubre & ~push)); perd = float(np.sum(~cubre & ~push))
            wr = gan / max(gan + perd, 1); roi = (gan * 0.9091 - perd) / max(gan + perd, 1)
            print(f"   ATS {nombre} |dif|>={t}: n={len(ap)} cubre {100*wr:.1f}%  ROI a -110: {100*roi:+.1f}%")
    for t in (3.0, 6.0):
        dif = tm.t_blend - tm.over_under
        ap = tm[abs(dif) >= t]
        if len(ap) < 25: continue
        over = dif[ap.index] > 0
        gan_ = np.where(over, ap.total > ap.over_under, ap.total < ap.over_under); push = ap.total == ap.over_under
        gan = float(np.sum(gan_ & ~push)); perd = float(np.sum(~gan_ & ~push))
        print(f"   Total blend |dif|>={t}: n={len(ap)} acierta {100*gan/max(gan+perd,1):.1f}%  ROI a -110: {100*(gan*0.9091-perd)/max(gan+perd,1):+.1f}%")
    res.append((s, len(tm)))
d.to_pickle(sys.argv[2])
