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

### 2026-11-14 (b) · Bajas confirmadas en el modelo
`config/bajas.yaml` (por partido: jugador, rol y peso) → factor «bajas» en `factores_partido.calcular`, aplicado a selecciones
(`pronostico_selecciones`) y a clubes (`prediccion._factores_club`). Reglas en `factores_partido.bajas`: un delantero/extremo menos
baja los goles propios; un defensa/lateral/portero menos sube los del rival; peso estrella 1.6 / titular 1.0 / rotación 0.4; tope 15%.
PROVISIONAL (sin calibrar). Cargadas las bajas de la jornada del 14-11-2026 y la motivación de Portugal (le basta el empate).

### 2026-11-14 (c) · Histórico completo y laboratorio por KPI («subramas»)
* Resultados: `/scores` ahora se pide para TODAS las competiciones solo-mercado con partidos pendientes de cerrar (Serie B, Segunda,
  Copa del Rey…), no solo selecciones (`max_creditos_resultados: 16`). Antes esos partidos nunca se cerraban y el Dashboard evaluaba
  solo una parte del Diario.
* Laboratorio por KPI (`servicios/calibracion_kpi.py`, migración 009): por KPI, p' = σ(a + b·logit p) ajustado con los días viejos y
  probado con los recientes (30%), penalizado hacia la identidad; se activa solo con ≥ 80 partidos y si mejora el Brier ≥ 0.002.
  Cada corrida guarda una versión (auditable/revertible). Las subramas activas se aplican a las tarjetas del Diario (🧪 vN).
* Dashboard sin filtro de fecha (es histórico acumulado).

### 2026-11-16 · NFL v2: lo que se tomó de nfl_probability_engine (sebastianBP26) y lo que no
Revisión del repo público `sebastianBP26/nfl_probability_engine` (Elo calibrado 1999-2025, regresión logística y Gradient Boosting como
comparación, Monte Carlo de temporada y seguimiento semanal en reels). Walk-forward con `scripts/calibrar_nfl.py` sobre nflverse
`games.csv` (ajuste 2002-19, prueba 2020-25, reserva 2026 a la fecha; Brier del moneyline, solo temporada regular):

| Configuración | Ajuste | Prueba 2020-25 | 2026 |
|---|---|---|---|
| Sus parámetros (K 40, regresión .40, local 42.2, sin margen de victoria) | 0.2225 | 0.2243 | 0.2211 |
| Nuestro v1 (K 20, regresión 1/3, local 48, con margen de victoria) | 0.2190 | 0.2226 | 0.2166 |
| **v2: K 24 + ventaja local aprendida (memoria 400 juegos)** | **0.2186** | **0.2218** | **0.2152** |
| Las Vegas (moneyline sin margen) | 0.2112 | 0.2096 | 0.2094 |

* **Se adopta:** seguimiento de la temporada con la predicción previa a cada juego (récord, Brier, log loss, calibración por rango,
  vs «siempre el local» y vs Las Vegas, batacazo), ranking Elo con cambio semanal y de temporada («la memoria»), destacados
  (el más seguro / la moneda al aire / la sorpresa contra Las Vegas) y Monte Carlo del resto de la temporada y playoffs
  (`motor_nfl/evaluacion.py`, `motor_nfl/temporada.py`). K de playoffs configurable (`k_playoffs_mult`, queda en 1.0: 1.2 no mejoró).
* **Se mejora sobre su modelo:** conservamos el multiplicador por margen de victoria (su ausencia explica casi toda la diferencia) y la
  ventaja de local deja de ser fija: la NFL pasó de ~57% de victorias locales (2010-19) a ~53% (2020-25) y la ventaja se aprende
  con promedio exponencial (`hfa_adaptativa`). Franquicias mudadas unificadas (OAK→LV, SD→LAC, STL→LA). Histórico de 20 temporadas
  (solo marcadores; la EPA sigue con 4).
* **No se adopta:** descanso, partido divisional y semana (sus propios coeficientes son 12-44 veces menores que el Elo y no mejoran el
  Brier); regresión logística / Gradient Boosting (pierden contra el Elo puro en su prueba).
* **Mezcla con el mercado:** Las Vegas sigue siendo claramente más precisa. En prueba, la mejor mezcla es casi todo mercado
  (α = 0 → 0.2096; α = 0.1 → 0.2101). `nfl.peso_modelo: 0.15` define `p_final` y el «valor» se mide con ella: antes cualquier
  diferencia de 4 pts del Elo contra Las Vegas se marcaba como valor, y el walk-forward dice que esas diferencias son mayormente ruido.
* Pendiente: calibrar `peso_epa` (requiere descargar el play-by-play histórico) y un ajuste por cambio de quarterback.

### 2026-11-16 (b) · NFL en el Dashboard y en los parlays; nombres completos
* Dashboard: apartado «🏈 NFL · efectividad del modelo» (`/nfl/dashboard`): récord de la temporada con la predicción previa a
  cada juego, curva acumulada semana a semana, cambio vs la semana anterior, contra el favorito de Las Vegas y contra la temporada
  pasada, calibración por rango y batacazo.
* Qué meter / Soñadoras: suman piernas NFL «Gana <equipo>» (moneyline, `p_final` = 15% modelo + 85% Las Vegas) con la misma regla
  `p ≥ p_min` y la misma ventana de días (`parlays_usuario.incluir_nfl`). Si la NFL falla, siguen los parlays de fútbol con aviso.
* Pestaña NFL: nombre completo de cada equipo junto a la abreviatura. El estado del modelo NFL se cachea hasta que cambian los datos.

### 2026-11-18 · Tenis (ATP/WTA), plan de pago de The Odds API y rediseño multideporte
**Tenis** (`motor_tenis/`, migración 011, `/tenis/semana`, `/tenis/dashboard`, `POST /tenis/actualizar`):
* Elo por jugador con memoria general y por superficie, K = 150/(n+15)^0.3, escala 0.85 y conversión a 5 sets en Slams ATP.
  Walk-forward con el histórico ATP/WTA (copia pública de los datos de Jeff Sackmann, 2000-mayo 2026): ajuste 2005-2019, prueba 2020-2026.
  Prueba: ATP acierto 64.1%, Brier 0.2172, log loss 0.6223 (sin escala 0.6264); WTA 65.1%, 0.2150, 0.6178 (sin escala 0.6205).
  Peso de superficie elegido en ajuste: ATP 0.5, WTA 0.3. La escala corrige el exceso de confianza (decía 84% y acertaba 81%).
* Producción: resultados y momios de cierre de tennis-data.co.uk (Excel por circuito/temporada; Render sí llega, este entorno no);
  momios en vivo de The Odds API (torneos activos de `/v4/sports`, 1 crédito por torneo). Nombres entre fuentes con clave «apellido inicial».
* p_final = 0.3·Elo + 0.7·mercado (PROVISIONAL): sin momios históricos aquí no se pudo medir α; el Dashboard de tenis lo mide contra el
  cierre de Pinnacle. Jugadores con < 15 partidos: solo mercado. Piernas «Gana <jugador>» entran a Qué meter / Soñadoras.
**The Odds API de pago (20K créditos/mes):** `creditos_mensuales` 500 → 20000 (la app se frenaba sola en 500), reserva de cierres 1500,
topes por actualización 60 y de resultados 40.
**Understat:** responde «mantenimiento» a Render desde el 15-11 (bloqueo a servidores): el xG no llega y el modelo calibra con w_xg = 0.
Pendiente buscar otra fuente de xG accesible desde la nube.
**Rediseño:** menú de 5 (Hoy · Dashboard · Parlays · Cuotas · Datos) + selector de deporte (⚽ 🏈 🎾) en Hoy y Dashboard; Parlays junta
Qué meter y Soñadoras en pestañas, con piernas de todos los deportes. Los nombres viejos de vista siguen funcionando (alias).

### 2026-11-18 (b) · Fútbol con el plan de pago: cuotas automáticas y cierre de todos los partidos
* Diagnóstico: en producción α global = 0 (log loss 1X2: mercado 0.961 vs modelo 0.972): las decisiones de fútbol ya son las del
  mercado. Por eso lo que más mejora la precisión hoy es la FRESCURA del mercado, no otro ajuste del modelo: las cuotas solo se
  renovaban al pulsar el botón («Cuotas: hace 276 min»).
* `jobs/automatico.py` (hilo dentro de la API, solo en Render): actualización completa diaria a las 07:10 CDMX (+ NFL y tenis) y
  «solo_cuotas» cada 4 h, o cada hora si algún partido empieza en < 3 h. Nunca encima de otra actualización; el presupuesto de
  créditos de la app sigue mandando.
* `cierre.solo_partidos_con_picks: false`: cuota de cierre de TODOS los partidos. Alimenta el CLV y acelera el α por liga
  (exige 300 partidos con cuota previa por liga; hoy solo alcanza el agrupado).
* Pendiente (siguiente paso): segundo modelo (Elo de clubes) en ensamble con Dixon-Coles, que solo se active si en walk-forward le
  gana al mercado en su liga; y otra fuente de xG (Understat bloquea a Render).
