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

### 2026-11-18 (c) · Segundo modelo: Elo de clubes en ensamble · Boletos de 7
* `modelos/elo_clubes.py`: Elo con nuestro histórico (K 20, diferencia de goles, ventaja local aprendida por liga, regresión 20%
  entre temporadas) → 1X2 con logit ordenado. Ensamble p = w·DC + (1−w)·Elo, decidido en la calibración semanal (w en el primer
  60% de la ventana walk-forward, validación en el último 40%; se activa solo si mejora ≥ 0.002 de log loss y solo en las ligas
  donde mejora). α contra el mercado se calibra DESPUÉS sobre el ensamble.
  Experimento (football-data 2016-2026, 8 ligas europeas, validación 2025, 1,338 partidos): DC 0.9773 → ensamble (w = 0.6) 0.9731;
  mejora en EPL, LaLiga, Bundesliga, Ligue 1, Eredivisie y Bélgica; no en Serie A ni Portugal. Pinnacle (cierre): 0.9507, y
  mezclarlo con DC o ensamble no lo mejora (α = 0 óptimo): el mercado sigue mandando en el 1X2 donde hay cuota.
* Boletos de 7 (Parlays → 🏆): una pierna por partido de cualquier mercado (Gana, PA(1), goles, ambos anotan, el otro anota,
  NFL, tenis) en tres bandas de probabilidad por pierna (seguro 75-92%, balanceado 66-85%, paga más 58-75%), sin repetir partidos.

### 2026-11-18 (d) · xG por tiros, tarjetas y córners en el Diario
* xG: Understat bloquea a Render; FBref y Sofascore tienen protección anti-bots (frágil e insostenible). Se usa un «xG por tiros»
  con las estadísticas de football-data que ya se descargan: xG ≈ 0.33 · tiros a puerta (los tiros fuera no aportan: b ≈ −0.01 → 0;
  ajustado con 35,806 partidos de 8 ligas 2016-2023). Walk-forward 2024/25 (2,669 partidos): w_xg 0 → 0.3 baja el log loss 1X2
  0.9817 → 0.9800 y O/U 2.5 0.6761 → 0.6747. `repo.historial` usa el de Understat si existe y si no el de tiros; la calibración
  semanal elige w_xg (0 si no ayuda). Solo ligas con estadísticas (europeas); Liga MX, MLS y Brasil siguen sin xG.
* Tarjetas: HY/AY/HR/AR de football-data → `estadisticas_partido` (migración 012; se recarga el histórico de la liga la primera vez).
  Modelo = el mismo NB de córners (equipo + árbitro; el árbitro se encoge menos: l2 5 vs 20). Líneas 2.5-5.5 amarillas.
* Córners y tarjetas salen del desplegable: mosaico en el Diario (la línea más cercana a 72% de probabilidad), en el Ranking
  (🚩 / 🟨) y como piernas posibles de los Boletos de 7.
* Corrección: `llamadas_api` no aceptaba el proveedor `tennis_data` (CheckViolation): toda descarga de tenis habría fallado.

### 2026-11-20 · Ligas fuera, equipos duplicados, acceso por persona, alineaciones, NBA
* **Ligas excluidas** (decisión del responsable: precisión sobre cobertura): EPL, Serie A, Bundesliga, Eredivisie, Pro League (Bélgica) y
  la J League. `config.ligas_excluidas` → `ligas.activa = false` (`repositorio.sincronizar_ligas`, al arrancar la API). Fuera del panel,
  sugerencias, parlays, Dashboard, calibración (`historial`, `calibracion_kpi`), córners/tarjetas/props, cierres y créditos. NO se
  borraron filas: cuotas, predicciones y picks son inmutables por diseño (triggers = auditoría); borrarlas exigiría desactivarlos.
  Quitar un código de la lista reactiva la liga. `japan` sale de las competiciones solo-mercado.
* **Equipos duplicados (causa de los «juegos que no aparecen»)**: las competiciones solo-mercado (Champions, Copa, Segunda…) creaban
  «Real Madrid», «Barcelona», «Lyon»… con `pais` vacío y un alias de The Odds API; el resolvedor de la liga devolvía ese duplicado y
  las cuotas de LaLiga/Ligue 1 no encontraban su partido (en silencio: solo un contador `sin_partido`; hoy 6 de 10 partidos de LaLiga y
  3 de 9 de Ligue 1 sin una cuota). ~33 duplicados medidos en producción, más los de nombre distinto (Celta Vigo/Celta, Real Betis/Betis).
  Arreglo: (1) el resolvedor da prioridad a la tabla explícita `alias_equipos.yaml`, ignora los alias hacia duplicados de pais vacío y
  los funde (`fusionar_equipo`); (2) `equipo_mercado` ya no crea un equipo si existe en una liga con modelo; (3) `reparar_duplicados_mercado`
  corre al arrancar; (4) el resumen de la etapa de cuotas lista `sin_partido_detalle`. El historial del modelo de clubes excluye las
  competiciones solo-mercado (siguen fuera de la calibración, como dice su sección de config).
  También se corrige el alias automático equivocado «Paris Saint-Germain FC → Paris FC» (y «RCD Espanyol de Barcelona → Barcelona»): lo
  corrige la tabla explícita. Los partidos YA guardados con el PSG como «Paris FC» (p. ej. el del 7-nov contra Troyes) NO se tocaron:
  hace falta el calendario autoritativo para reasignarlos; quedan señalados en el resumen.
* **Acceso por persona** (`usuarios.py`, migración 013): cada persona entra con su código (solo se guarda su huella), el token lleva su
  id, bloquear es inmediato (caché de 15 s) y se registra quién entra (login, fallidos, bloqueos y un latido cada 10 min con ruta, IP
  real detrás del proxy y dispositivo). `/admin/*` solo para el administrador; panel en Datos → Accesos. La contraseña compartida de
  lector sigue funcionando (no se puede identificar ni bloquear por separado): quitar `APP_VIEWER_PASSWORD` fuerza el uso de códigos.
* **Alineaciones**: la etapa existía pero nunca guardó nada (0 filas): el plan gratis de API-Football no cubre la temporada actual,
  así que ningún partido vigente tiene `api_football_id` (lesiones tampoco: 0). `jobs/automatico` programa ahora `solo_alineaciones`
  cada 15 min en la ventana de 90 min previa a cada partido con id y sin alineación; no cuesta nada mientras no haya ids. Siguen sin
  modificar λ (fase posterior). Requiere un plan de API-Football con temporada actual (o una fuente que se pueda probar).
* **NBA — backtest walk-forward** (`scripts/backtest_nba.py`; box scores de hoopR/ESPN y líneas de CIERRE de 9 casas, 2020-2026;
  temporadas de prueba 2023-24, 2024-25 y 2025-26, ~1,315 juegos con línea cada una; pesos del blend ajustados solo con temporadas
  anteriores). Modelo: ORtg/DRtg/Pace con media vida de 14 juegos, regresión 30% entre temporadas, localía 2.6 y descanso.
  Resultado: el modelo NO le gana a la línea de cierre. MAE margen: mercado 10.63 / 10.63 / 10.95, modelo 11.17 / 11.19 / 11.49; el
  peso del modelo en la regresión sobre el mercado es −0.06 / −0.07 / −0.02 (el mercado ya lo contiene todo). Contra el spread, el modelo
  cubre 49.1-49.9% (ROI −5 a −8% a −110; el punto de equilibrio es 52.4%). Total: igual (MAE 14.1 mercado vs 14.8 modelo).
  Ganador: mercado 68.9-69.1%, modelo 64.8-69.2%. Lo que SÍ sirve es el mercado bien calibrado: favorito por spread 0-3: 52.7%, 3-5: 59.6%,
  5-7: 65.8%, 7-9: 76.9%, 9-12: 78.5%, 12+: 89.3%; las líneas alternativas siguen una normal centrada en la línea (σ margen 13.3, σ total
  17.8) con error < 2 puntos en todo el rango. Conclusión: si se agrega la NBA, va «mercado primero» (como el fútbol con α = 0): probabilidades
  de la línea de cierre + normal para líneas alternativas y totales; el modelo propio solo como contexto (descanso, ritmo).
  Falta el dato crítico de lesiones: no está en esta fuente y es donde un modelo podría adelantarse a la línea.

### 2026-11-20 (b) · Ligas menores fuera, alineaciones con ESPN, correcciones del PSG/Espanyol, NBA «mercado primero»
* **Ligas menores fuera**: `ligas_excluidas_patrones` (config) desactiva las competiciones solo-mercado cuya clave de The Odds API contenga
  efl_champ, bundesliga2, ligue_two, serie_b, segunda, spl, turkey, argentina, saudi o japan, y salen de `competiciones_mercado.patrones`
  (ya no se descubren ni gastan créditos). Quedan Champions, Europa, Conference, selecciones, copas, Libertadores, Sudamericana y Concacaf.
* **Alineaciones: la fuente es ESPN** (API pública, sin llave; `ingesta/proveedores/espn.py`). Medido: ESPN NO publica lesiones de fútbol
  (0 de 1,169 jugadores de LaLiga y Ligue 1; `/injuries` de cada liga vacío; todos «Active»). Sí publica la alineación oficial desde ~1 h
  antes (11 titulares, banca, formación) y por equipo las estadísticas de cada jugador (apariciones, suplencias, goles, asistencias).
  Por eso «principal» = ≥ 60% de titularidades del equipo (mín. 4 partidos) y «ausente» = no está en la convocatoria o está en la banca;
  el motivo (lesión, sanción, descanso) no se conoce y no se inventa. Nivel: fuera pesa el doble que banca; alto = arquero habitual no
  titular, falta quien más aporta (≥ 25% de goles+asistencias) o suma ≥ 2. **No mueve probabilidades** (en el 1X2 manda el mercado, α = 0, y
  el mercado ya incorpora la alineación publicada; un ajuste propio sin calibrar solo añade ruido): avisa en el panel y baja la confianza
  (−0.5 / −1.0), con lo que el partido deja de entrar solo a los boletos (exigen confianza mínima). Verificado contra ESPN en vivo
  (Athletic–Espanyol del 20-nov: Espanyol sin Edu Expósito, 3 principales del Athletic en la banca).
  `solo_alineaciones` corre cada 10 min en la ventana de 100 min previa a cada partido sin alineación (ligas mapeadas: LL, L1, POR, MX1,
  MLS, BR1 y Champions/Europa/Conference/Copa del Rey) y ya NO recalcula predicciones (llenaría `predicciones` de filas idénticas).
  Migración 014 (alias `espn`, tabla `plantilla_jugadores`, columnas en `alineaciones`, `partidos.espn_id`).
* **Correcciones de partidos** (migración 015, guardada por id): el alias «Paris Saint-Germain FC → Paris FC» y «RCD Espanyol de Barcelona →
  Barcelona» dejaron 4 partidos con el equipo equivocado (PSG–Troyes del 7-nov, Nice–PSG del 21-nov, Espanyol–Deportivo del 7-nov y
  Athletic–Espanyol del 20-nov), verificados contra el calendario de ESPN. Se detectaron buscando equipos con dos partidos en < 40 h.
* **NBA «mercado primero»** (`motor_nba/`, migración 016, `/nba/semana`, `/nba/dashboard`, `/nba/actualizar`, vista 🏀, piernas en Boletos):
  fuente única ESPN y gratis (calendario con spread, total y moneyline de DraftKings; lesiones Out/Day-To-Day; promedios por jugador). Sin
  The Odds API a propósito: cero créditos; si se quiere consenso de varias casas se puede sumar después. Probabilidades = línea con
  σ margen 13.3 y σ total 17.8 (backtest); moneyline, spreads y totales alternativos; lesiones y descanso avisan, no mueven el número
  (el backtest no tiene lesiones: no se puede validar un ajuste). Un jugador es clave si promedia ≥ 25 min o ≥ 15 pts; el nivel de bajas es
  alto si sale una estrella (≥ 20 pts o ≥ 32 min) o 2 clave. Los juegos con baja importante no entran a los boletos. La línea se
  actualiza solo mientras el juego está programado: la última es la de cierre y con ella el Dashboard mide al mercado con sus propios juegos.
* Pruebas: la suite corrió 1,082 pruebas con 3 fallos, todos de este cambio de ligas/mensajes y ya corregidos (la prueba de tarjetas pedía
  mosaicos de EPL; la de catálogo usaba Argentina; el e2e esperaba «incorrecta» en el login).

### 2026-11-21 · Lesiones reales (FotMob), bajas probables (ESPN), mercados derivados de NBA y por set en tenis
* **Lesiones de fútbol: FotMob** (`proveedores/fotmob.py`). ESPN no las publica; FotMob trae por partido los jugadores no disponibles con
  motivo (lesión, sanción, con su selección, enfermedad) y regreso estimado. Ligas: LaLiga 87, Ligue 1 53, Portugal 61, Liga MX 230,
  MLS 130, Brasil 268, Champions 42, Europa 73, Conference 10216, Copa del Rey 138. Se pide cada 3 h (30 min cerca del partido).
  Se cruza con los titulares habituales (ESPN): solo los habituales pesan en el nivel; el resto se lista como «otras bajas».
  Cuando sale la alineación, un habitual fuera de la convocatoria muestra el motivo real («lesión (regreso: …)»).
  Sofascore y Transfermarkt no se integraron (no se pudieron probar); FotMob es una API web no documentada: si cambia, la etapa lo
  cuenta como error y siguen funcionando las bajas inferidas.
* **Bajas probables sin fuente de lesiones** (ESPN, quién jugó cada partido): medido en 211 partidos de LaLiga y Ligue 1 2026-27, un
  titular habitual que no jugó los 2 partidos anteriores NO fue titular en el siguiente el 80.6% de las veces (72 casos); uno expulsado en el
  último, el 100% (20 casos); sin señal, 25.3%. Avisa antes de la alineación y baja la confianza igual que una ausencia confirmada.
  Migración 017 (equipos_espn, participaciones_espn, bajas_partido, partidos.fotmob_id).
* **NBA: mercados derivados medidos** (temporada 2025-26, 1,231 juegos con marcador por cuarto de ESPN y línea de cierre de 9 casas):
  1.ª mitad: total = 0.504·T (σ 12.1), margen = 0.604·μ (σ 11.1), empate 2.4%; 1.er cuarto: total = 0.255·T (σ 8.2), margen = 0.337·μ (σ 8.2),
  empate 4.5%; puntos por equipo = (T ± μ)/2 (σ 11.5). La normal acierta con error ~1 pt en totales alternativos de cada tramo y por equipo.
  Nuevas sugerencias: puntos del favorito ≈ 85% y spread de 1.ª mitad ≈ 85%; en la tarjeta, ganador, 1x2 con empate, spreads y totales de
  1.ª mitad y 1.er cuarto. Las piernas de un mismo juego comparten partido_id: Qué meter/Soñadoras usan la más probable; los Boletos eligen.
* **Tenis: mercados por set** (ganador del 1.er set, 2-0, hándicap ±1.5 sets, 3 sets). Los sets NO son independientes: la fórmula ingenua
  subestima el 2-0 ~9 pts (favoritos 70-80%: 44.9% calculado vs 53.6% real). Se usan curvas logísticas sobre logit(p del favorito) ajustadas
  con 2024 y probadas en 2025 (12,337 partidos a 3 sets): error ≤ 4 pts por tramo. Solo partidos a 3 sets (a 5 sets no hay datos para medir).
* NFL «puntos por equipo / total de TDs / par-impar»: no se agregaron; no hay histórico local con líneas y marcador para medirlos.

### 2026-11-22 · NFL: puntos por equipo, 1.ª mitad y par/impar (medidos)
* Histórico de nflverse (`nfldata/games.csv`, 2015-2025, 3,179 partidos con marcador, spread y total de cierre) y marcador por cuarto de
  ESPN (2023-2025, 816 partidos). `motor_nfl/derivados.py`, desde la LÍNEA del mercado (el modelo propio pesa 15% en el moneyline):
  puntos por equipo = (T ± μ)/2 con σ 9.16 (error ≤ 1 pt de −10 a +10 del esperado); 1.ª mitad: total 0.5156·T (σ 8.62), margen 0.6634·μ
  (σ 9.89), empate 6.0% (favorito 60-70%: 65.4% prometido, 66.2% real); total impar 53.0% (49.5-55.4% por temporada).
  Sugerencias nuevas: puntos del favorito ≈ 85% y spread de 1.ª mitad ≈ 85% (mismo partido_id que el moneyline).
  Total de touchdowns: no se agrega (la fuente no trae TDs por partido para medirlo).

### 2026-11-22 (b) · Filtros nuevos, escudos y «¿el modelo mejora?» por versión
* **Filtros del Diario rehechos**: antes las ligas se apagaban una por una con botones (para ver una liga había que apagar todas) y la
  vista de ranking usaba otro selector, así que lo elegido no pasaba de una vista a otra. Ahora es UNA barra para tarjetas y ranking:
  liga (lista con el número de partidos), buscar equipo, «Todos / Por jugar», probabilidad mínima del favorito (≥60/70/80%), orden
  (hora, probabilidad, confianza), contador y «Limpiar filtros».
* **Escudos** (ESPN): fútbol con el id de ESPN de cada equipo (`equipos_espn`, se llena al emparejar partidos); NBA y NFL con la abreviatura.
  La URL la arma la app con un patrón fijo y solo un número/abreviatura validados; se pinta como fondo de un `<span>` (ningún `<img>`
  con datos del servidor). Las selecciones nacionales llevan bandera (ver 2026-11-22 c).
* **¿Mejora el modelo al recalibrar?** `aciertos.por_version`: por cada versión del modelo, los partidos que pronosticó (lo guardado al
  cierre diario en `dashboard_registro`), su log loss 1X2 y el del mercado en ESOS mismos partidos. Se juzga la brecha app − mercado
  (los días no son igual de difíciles): baja ≥ 0.005 = mejoró, sube ≥ 0.005 = empeoró; con < 30 partidos no se juzga. Tabla en el Dashboard.

## 2026-11-22 (c) — Banderas de selecciones, tenis desde GitHub y casa Caliente

* **Banderas.** Las selecciones nacionales llevan su bandera (flagcdn.com) en vez de escudo. `selecciones.bandera(nombre)` → código ISO
  (`es`, `gb-eng`, `xk`…) desde la misma tabla de nombres que ya homologa las selecciones; los clubes no traen bandera. La app solo acepta
  `[a-z]{2}(-[a-z]{3})?` y arma la URL con un patrón fijo; se pinta como fondo de un `<span>`, igual que los escudos.
* **Tenis no aparecía.** En producción `tenis_partidos` tenía 0 filas: tennis-data.co.uk responde 403 a Render, así que el Elo nunca se
  entrenó. El histórico ahora sale de los CSV públicos de Jeff Sackmann en GitHub (`parse_sackmann`, misma clave de nombre que los eventos
  de The Odds API); `tenis.fuente_historico: tennis_data` regresa a la fuente anterior. Sin momios de cierre en el histórico: la
  comparación «vs mercado» del tenis se llena con los eventos que sí trae The Odds API. Migración 018: proveedor `sackmann`.
* **Champions.** Verificada en producción: 9 partidos próximos cargados; los del día siguiente entran con la ventana de 2 días de cuotas.
* **Casa de apuestas.** `casa_manual_nombre: Caliente` (antes Draftea). El lector de boletos acepta el formato de ambas.
