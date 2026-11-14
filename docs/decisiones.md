# golmodelv2 · Decisiones de la versión 1 (costo $0)

## Stack gratuito

| Necesidad | Herramienta | Límite gratis | Riesgo |
|---|---|---|---|
| Resultados y cuotas históricas (incluido el cierre) | football-data.co.uk (CSV) | Sin límite | Ligas extra (MX, USA, BRA): solo 1X2. Verificar que siga la columna de Pinnacle de cierre (PSCH) |
| xG histórico y en vivo | Understat (JSON público) | Sin API oficial | Solo cubre EPL y Serie A. No tiene términos de uso formales; el módulo es reemplazable |
| Fixtures, lesiones, alineaciones | API-Football Free | 100 requests/día | Verificar si el plan gratis da acceso a la temporada actual |
| Cuotas actuales y de cierre | The Odds API Free | 500 créditos/mes, costo = mercados × regiones | Pinnacle podría estar limitada. Respaldo: mediana sin margen de las casas disponibles |
| BTTS, equipo anota, SGP | Captura manual en la UI | — | — |
| BD, backend, UI | PostgreSQL 16, FastAPI y Streamlit en docker-compose local | — | El job de cierre necesita la PC encendida; si no, respaldo con el CSV |
| Modelo | SciPy: máxima verosimilitud con penalización L2 (pooling parcial) | — | PyMC queda fuera de la v1 |

## Ligas
- **Con xG:** EPL y Serie A, usando goles + xG de Understat (la misma fuente en backtest y producción). Ajuste por bajas con xG + xA por jugador.
- **Solo goles:** Liga MX, MLS y Brasileirão. Sin ajuste de λ por bajas; las bajas se muestran solo como aviso. En el backtest solo se puede evaluar 1X2.

## Decisiones estadísticas (de la Fase 0)
A1–A10, tal como se propusieron, con sus valores por defecto. Detalle en la conversación de la Fase 0.

## Pendientes `# VERIFICAR` (se resuelven con la primera llamada real)
- API-Football: `/status` y `/leagues?id=…` para confirmar el acceso a la temporada actual y los ids de las ligas.
- The Odds API: `/sports` para confirmar las claves de cada liga; `pinnacle` en la región `eu`.
- football-data: `notes.txt` para confirmar las columnas de cierre por liga.

## 2026-11-13 · Selecciones con modelo propio, amistosos y factores de partido

**Problema.** Las selecciones eran «solo mercado» (α = 0): sin historial, sin modelo y sin resultados (los partidos se quedaban en
«programado»). El sistema no podía ver un 7-0 de Países Bajos o un 0-5 de Islandia, y no había amistosos.

**Decisión.**
* `modelos/selecciones_modelo.py`: Poisson con ataque/defensa por selección, ventaja de local apagada en sede neutral,
  decaimiento (vida media ≈ 2 años), peso por torneo (amistoso 0.6) y goles topados a 5. Histórico público martj42/international_results
  (incluye amistosos) + `config/selecciones_resultados_extra.csv` + resultados de The Odds API `/scores` (tope propio de créditos).
* `modelos/factores_partido.py` + `config/sedes.yaml`: altitud (McSharry 2007), clima del día vs lo que acostumbra cada equipo
  (Open-Meteo), motivación (`config/motivacion.yaml`: necesita ganar / le sirve el empate / nada en juego; amistosos automáticos),
  ambiente del estadio (solo lo que se aparta del promedio) y sede neutral. Tope ±30% por λ.
* Mezcla: p = 0.35 · p(modelo × factores) + 0.65 · p(mercado × factores^0.5). Sin cuotas (amistosos fuera de The Odds API):
  solo modelo. Todo PROVISIONAL hasta el walk-forward; las selecciones siguen fuera de los tickets V3.

**Glosario fijo.** **PA(1)** = «ese equipo anota el PRIMER gol del partido». La tarjeta del Diario lo muestra como «PA(1) · PRIMER GOL».

### 2026-11-13 (b) · Calibración walk-forward, factores en clubes y marcador de aciertos
* **Calibración** (`backtest/selecciones_wf.py`, `scripts/calibrar_selecciones.py`): ajuste 2022-01→2024-07, prueba 2024-07→2026-10
  (2,405 partidos, sin look-ahead). Modelo: ξ 0.0006, l2 0.1, amistoso 1.0, tope 6, 16 años. Factores: altitud 0.20/1000 m (propio),
  0 (rival); ambiente 0.06 local / 0.15 visitante; amistoso +4%.
  Prueba: log-loss 0.8783 → 0.8498 (−3.2%); Ganador 60.0% → 60.5%; Ambos anotan 57.0% → 57.5%; +2.5 58.1% → 59.2%; El otro anota 60.0% → 61.7%.
  Ojo: es modelo vs modelo (no hay cuotas históricas de selecciones). Contra el MERCADO, en los 16 partidos del 12-13/11 el mercado
  ganó (Ganador 12/16 vs 11/16). α = 0.35 sigue SIN calibrar: se medirá con el marcador de aciertos.
* **Clubes**: los factores también aplican a clubes de `config/sedes.yaml` (Liga MX: altitud), a la mitad (`escala_clubes`), porque su
  Dixon-Coles ya absorbe parte del efecto de su estadio. Las bajas siguen en `ajuste_*` (la base exige <= 0); los factores solo entran a la matriz.
* **Marcador de aciertos** (`servicios/aciertos.py`, `/aciertos`, `/diag/aciertos`, Diario): por KPI (Ganador 5/6, Ambos anotan 3/5…),
  con la predicción previa al partido, decisión de la app vs mercado solo. PA(1) solo se cuenta cuando se puede saber sin el minuto del gol.

### 2026-11-13 (c) · «Qué meter» y «Soñadoras» = parlays del responsable
Decisión del responsable: se quitan de esas pantallas el ticket V3, «Apuesta por esto hoy», «Con valor ahora», «Rompen parlays»,
la soñadora 10-15 (>100x) y las soñadoras analíticas. En su lugar (`servicios/parlays_usuario.py`, `/parlays_usuario/{fecha}`):
* Qué meter: 3 parlays de 3, 4 y 5 partidos. Soñadoras: 3 parlays de 6, 8 y 10.
* Pierna = «Gana» o «PA(1)» (la más probable del partido), p ≥ 0.55, confianza ≥ 3.5, sin partidos que «rompen parlays» ni ya empezados.
* Hasta 3 días y cualquier liga; cada pierna muestra día y hora. Las opciones de una sección no comparten partidos mientras alcancen.
* Probabilidad = producto de las piernas (partidos distintos). Un equipo duplicado a la misma hora (dato malo de la fuente) se omite y se avisa.
El backend del ticket V3 y las soñadoras sigue existiendo (API y Streamlit); solo dejó de mostrarse en la PWA.

### 2026-11-13 (d) · Dashboard de aprendizaje
Nueva pestaña «Dashboard» en la PWA (`/dashboard`, `/diag/dashboard`, `aciertos.construir_dashboard`): por KPI (Gana, PA(1), Ambos anotan,
+1.5, +2.5, −3.5) el % de acierto acumulado con la predicción previa al partido, el cambio vs ayer y vs el primer día, últimos 7 días,
la curva diaria (app vs mercado vs línea base histórica) y la «seguridad promedio». Línea base = walk-forward de selecciones (`dashboard.linea_base`).
Con pocos partidos los cambios diarios son ruido: un «+1 pt» sobre 40 partidos no es significativo todavía.

### 2026-11-14 · Dashboard: cierre diario guardado y aciertos por rango de probabilidad
* `dashboard_registro` (migración 008): por partido, KPI y fuente (app/mercado) se GUARDA el pick que mostró la app antes del partido,
  su probabilidad y si se cumplió. Se llena al final de cada recálculo (`pipeline._cierre_dashboard`) y con `POST /dashboard/cierre`,
  desde `dashboard.desde` (2026-11-06, arranque de la app). Idempotente.
* Cada KPI del Dashboard muestra aciertos por rango de probabilidad (30-50, 51-60, 61-70, 71-80, 81-90, 91-100%) contra lo esperado:
  si en el rango 71-80% acierta ~75%, ese rango es confiable (calibración).
* Corrección: la probabilidad de PA(1) en el marcador ahora incluye P(hay al menos un gol): λ_e/(λ_l+λ_v)·(1−e^−(λ_l+λ_v)).
