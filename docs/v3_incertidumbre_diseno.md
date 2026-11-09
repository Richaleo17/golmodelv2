# Muestreador de incertidumbre τ/σ y su calibración · Diseño V3

- Estado: aprobado
- Versión: v0.5
- Fecha: 2026-11-09
- Autor(es): equipo V3
- Depende de: `v3_dependencia_diseno.md`
- Afecta a: `src/golmodel/backtest/calibracion_tickets.py` (nuevo, ajusta τ/σ), `src/golmodel/motor/dependencia.py` (los consume), `db/migraciones/005_calibrador.sql` (tabla `versiones_calibrador_incertidumbre`)

## 1. Contexto

El motor de dependencia necesita, por sorteo, una incertidumbre idiosincrática por partido
(τ_i) y factores compartidos (σ_ℓ, σ_g). Estos parámetros no pueden elegirse a dedo: definen
tanto el ancho de los intervalos como la correlación entre piernas, y de ellos sale
`p_conservadora`, input de la función objetivo de `motor/tickets.py`. Este documento define qué
son, de dónde se calibran, cómo se evita el sobreajuste y el sesgo de selección, cómo nace la
cota conservadora y cómo se versiona todo para reproducibilidad. Incorpora las respuestas al
control de calidad sobre leakage, cuantil q, sesgo de supervivencia y correlación entre ligas.

## 2. Decisiones

1. **Parametrización sobre log-λ**, no sobre p_i. Un partido tiene un estado (λ_local, λ_visit, ρ);
   la incertidumbre sobre λ mantiene coherentes todos los mercados del partido.
2. **Definiciones:**
   - `τ_i`: desv. estándar idiosincrática de `log λ_i` (incertidumbre de estimación de la fuerza
     del equipo). `τ_i² = τ_estimación² + τ_entre-modelos²` (hoy el segundo = 0, un solo modelo).
   - `σ_ℓ`: desv. de un factor log compartido por liga (sesgo sistemático de liga → correlación
     dentro de la liga, que es donde hay causa compartida real).
   - `σ_g`: factor log del nivel más alto de la jerarquía. **Por defecto = 0** (ver decisión 9).
3. **Generación de τ_i** (no se ajusta un τ libre por partido, es inidentificable):
   `τ_i = c_seg(i) · s_i`, con `seg = (liga, familia de mercado, rango de cuota)` y `s_i` = desviación
   estándar de estimación de la fuerza del equipo. **`s_i` tiene fuente principal y proxy, ALTERNATIVAS
   (no aditivas):** si el optimizador guardó la covarianza del MLE penalizado (Hessiano / información de
   Fisher, en `versiones_modelo.convergencia`), `s_i` = ese error estándar; si no, la proxy
   `s_i = 1/√peso_efectivo_i`. **Por qué √:** el error estándar de un estimador escala como
   `1/√(tamaño efectivo)` (teoría de muestreo). **`peso_efectivo`** = campo real del esquema
   `parametros_equipo.peso_efectivo` = suma de pesos de decaimiento `φ(t)` de los partidos del equipo
   (nº efectivo de partidos, ponderado por recencia); **NO es el `n_eff` del pooling** (ese es nº de
   partidos distintos). `c_seg` es la escala por segmento que se calibra `{c_seg}, {σ_ℓ}, σ_g, n₀`.
4. **Fuente de calibración (`calibracion_tickets.py`, walk-forward):**
   - Nivel base de τ por selección: fiabilidad por segmento (Brier, ECE) + **cobertura de intervalos**.
   - Factores compartidos σ_ℓ, σ_g: **log-loss del indicador "ganan todas"** sobre cestas históricas
     de N piernas + fiabilidad predicho-vs-observado por tamaño de ticket. La correlación solo aparece
     en el comportamiento conjunto.
   - El *nivel* de p lo fija `calibracion_mercado.py`; aquí solo se fija la *dispersión*.
5. **Las cestas de calibración se construyen EX-POST sobre el histórico completo, SIN filtrar por
   aprobación** (ni por CRS, EV o p mínimo). Para cada día/ventana se toman todas las predicciones
   as-of y se muestrean muchas combinaciones de N piernas (máx. 1 por partido, partidos distintos),
   cubriendo todo el rango de probabilidades y mezclas de mercado, incluidas las que el motor habría
   rechazado, e incluidas perdedoras y anuladas. Esto evita el sesgo de selección/supervivencia.
6. **Muestras pequeñas → jerarquía con pooling parcial:** `ĉ_seg = w·c_seg^MLE + (1−w)·c_padre`,
   `w = n_eff/(n_eff+n₀)`, cadena `seg → liga → global`. `n_eff` = partidos/días distintos. Umbral
   mínimo `n_min`: por debajo, se hereda el padre. Modelo nulo = un `c` global con σ=0 (independencia);
   solo se admite estructura si mejora el log-loss fuera de muestra.
7. **Cota conservadora por cuantil:** `p_conservadora,i = Q_q(p_i(θ))`. `q` es un parámetro de
   **política de riesgo, fijo y global** (defecto 0.25), NO adaptativo por segmento (eso reintroduce
   sobreajuste); única excepción admisible: `q` más estricto para tickets más grandes. No es una tasa
   de acierto. No `p − k·τ` (se sale de [0,1] en los bordes); `p − k·τ` es el caso simétrico
   (`z_0.25≈0.674`). **Validación por cobertura:** agrupando selecciones por `p_conservadora`, la
   `frecuencia_observada ≥ p_conservadora` debe cumplirse en ≈ (1−q) de los buckets grandes; si se
   viola más que q, el arreglo es ensanchar τ/σ, no bajar q. Requiere media ya calibrada.
   `EV_cons = p_conservadora · cuota_ejecutable − 1`; si ≤0, stake 0.
8. **Propagación al ticket por simulación**, no por cota analítica. τ_i y σ generan las perturbaciones
   por sorteo en `dependencia.py`; `p_conservadora` del ticket = percentil del producto conjunto π.
   **Nunca** se multiplican las `p_conservadora,i` de cada pierna.
9. **σ_g por defecto 0, evidencia-dependiente.** Hipótesis nula: sin correlación entre ligas no
   relacionadas. Jerarquía de factores: partido < liga < (opcional) misma jornada/confederación <
   global, con el shrinkage más fuerte hacia 0 en los niveles altos. σ_g solo se mantiene > 0 si
   cestas entre-ligas construidas ex-post muestran que `(σ_g libre)` mejora el log-loss fuera de
   muestra frente a `(σ_g=0, σ_ℓ ajustado)`. Expectativa: σ_g ≈ 0.
10. **Leakage — garantía dura:** τ/σ en `t` se ajustan solo con partidos resueltos antes de `t`
    (trigger `datos_hasta ≤ info_hasta`); ajuste y evaluación temporalmente disjuntos (walk-forward);
    el pooling es transversal entre segmentos en un corte temporal congelado, nunca a través del tiempo.
11. **Versionado:** τ_i se genera de un calibrador versionado `{c_seg, σ_ℓ, σ_g, n₀, prior, versión}`
    (tabla inmutable `versiones_calibrador_incertidumbre`: `config_hash`, ventana, `backtest_run_id`,
    `datos_hasta`, versión de código) + insumos inmutables del partido. Cada ticket guarda
    `calibrador_incertidumbre_id`, `semilla`, `n_draws`, `metodo` → reproducible bit a bit.
    Recalibrar = fila nueva, nunca mutar.
12. **Protocolo de aceptación de q (PRE-REGISTRADO 2026-11-09, inmodificable sin bump de versión y
    justificación en auditoría):** ventana out-of-sample walk-forward ≥ 2 temporadas; selecciones
    agrupadas por `p_conservadora` en bins de 0.05; ≥ 8 buckets poblados; ≥ 200 eventos distintos
    (tamaño efectivo = partidos distintos, no selecciones) por bucket; ≥ 3000 eventos distintos en
    total. q=0.25 se aprueba solo si: (i) proporción de buckets con `frec_observada ≥ p_conservadora`
    ≥ 0.75, tolerando ruido hasta un límite inferior del IC unilateral 95% de 0.65 (δ=0.10); (ii)
    ningún bucket violado por > 5 pp; (iii) gap agregado ponderado por n_eff (observada − cota) ≥ 0.
    Si falla, se ensancha τ/σ y se revalida; nunca se baja q para pasar.
13. **Protocolo de activación de σ_g (PRE-REGISTRADO 2026-11-09):** cestas entre-ligas ex-post sin
    filtrar ≥ 2000, ≥ 2 temporadas, out-of-sample. σ_g>0 solo si: (i) reducción relativa del log-loss
    fuera de muestra ≥ 1% vs (σ_g=0, σ_ℓ ajustado); (ii) bootstrap pareado con IC 99% de la diferencia
    que excluye 0 (α=0.01); (iii) estable en ≥ 2 folds. Control de comparaciones múltiples (Holm) si
    se prueban varios niveles de la jerarquía. Si no se cumplen las tres: σ_g=0.
14. **`modo_experimental` obligatorio (gate):** mientras q y σ_g no pasen 12-13, todo ticket se genera
    con `modo_experimental=True` (parámetro obligatorio, sin default), se marca "calibración
    provisional", se bloquea su promoción a paper/real, y `auditoria_decisiones` registra el
    `calibrador_incertidumbre_id`, q y σ_g vigentes. El flag solo se limpia cuando 12-13 se cumplen, y
    esa limpieza es un evento versionado y auditado. (Se formaliza en `v3_tickets_objetivo_diseno.md`.)
15. **Inversión de dependencia con `dependencia.py` (orden de implementación).** `incertidumbre.py` POSEE la
    interfaz `FactorSampler` (Protocol) y su implementación por defecto (`FactorSamplerJerarquico`, con
    `σ_g=0` por defecto); **NO importa `dependencia.py`**. `dependencia.py` (que llega después) CONSUME un
    `FactorSampler` inyectado. La dependencia apunta `dependencia → incertidumbre`, coherente con el orden
    (incertidumbre primero). Prohibido importar desde `dependencia.py`.
16. **Hessiano reservado, proxy por defecto.** El modelo base (Dixon-Coles+xG, `scipy.optimize.minimize`)
    NO expone hoy el Hessiano/Fisher (`versiones_modelo.convergencia` guarda iteraciones/nll/mensaje, no la
    covarianza). Por tanto `s_i = 1/√peso_efectivo` es la implementación por defecto y el Hessiano es una
    **interfaz reservada**: parámetro opcional `s_hessiano: float | None = None` que, si en el futuro se
    persiste, se inyecta. Nada de código aspiracional que falle con AttributeError.

## 3. Alternativas descartadas

- **Incertidumbre sobre p_i directamente:** rompe la coherencia entre mercados del mismo partido.
- **`p − k·τ` como primitiva:** no acotada en [0,1]; se mantiene solo como lectura aproximada del cuantil.
- **τ_i libre por partido:** inidentificable y sobreajusta.
- **c_seg sin pooling (MLE por segmento):** sobreajusta ligas con pocos datos.
- **q adaptativo por liga/mercado:** reintroduce el sobreajuste que el pooling evita; q es política, no dato.
- **Calibrar σ sobre cestas aprobadas por el sistema:** sesgo de selección/supervivencia. Se usan cestas
  ex-post sin filtrar (decisión 5).
- **σ_g como par de σ_ℓ (global siempre activo):** induce correlación espuria entre ligas no relacionadas
  (p. ej. Liga MX vs Premier). Degradado a defecto 0 y evidencia-dependiente (decisión 9).
- **Propagación analítica de la cota al ticket:** intratable para producto de variables acotadas,
  correlacionadas y asimétricas; se usa Monte Carlo.

## 4. Supuestos

- `peso_efectivo` es un buen proxy de la evidencia por equipo (ya está en el esquema).
- Multiplicadores log-normales aproximan razonablemente la incertidumbre de λ.
- Hay suficiente historial para construir cestas ex-post representativas sin sobreajustar.
- La correlación entre piernas proviene sobre todo de parámetros compartidos a nivel de liga, no de
  dependencias físicas ni de un factor global.
- El nivel (media) de p se calibra aparte en `calibracion_mercado.py`.

## 5. Limitaciones

- Con un solo modelo, `τ_entre-modelos = 0`: se subestima la incertidumbre estructural hasta que exista ensemble.
- Se espera σ_g ≈ 0; si el historial es corto, los niveles altos de la jerarquía quedan pooled (menos
  resolución, no incorrecto).
- La cobertura de la cota y la validación de σ_g solo son concluyentes con suficiente historial; en
  segmentos nuevos se parte de la versión más pooled y conservadora.
- No corrige el nivel de p; si la media está sesgada, τ/σ no lo arreglan.
- Aún no hay evidencia empírica (ningún backtest corrido): q=0.25 y los σ son provisionales hasta validar.

## 6. Versión

| Versión | Fecha | Cambio |
|---|---|---|
| v0.1 | 2026-11-09 | Diseño inicial del muestreador τ/σ y su calibración. |
| v0.2 | 2026-11-09 | Cestas de calibración ex-post sin filtrar (anti-sesgo de selección); σ_g por defecto 0 y evidencia-dependiente; q explícito como política global no adaptativa + test de cobertura; leakage como garantía dura explícita. |
| v0.3 | 2026-11-09 | Pre-registro de los protocolos de aceptación de q (cobertura: ≥8 buckets, ≥200 n_eff/bucket, umbral 0.75/δ=0.10, cap 5 pp) y de activación de σ_g (mejora log-loss ≥1%, bootstrap IC 99%, ≥2 folds); gate `modo_experimental` obligatorio. Aprobado. |
| v0.4 | 2026-11-09 | Aclaración de la decisión 3: `τ_i = c_seg·s_i` con `s_i` = Hessiano (fuente principal) o `1/√peso_efectivo` (proxy), ALTERNATIVAS no aditivas; justificación de √ (SE∝1/√n); `peso_efectivo` (campo del esquema, ponderado por recencia) ≠ `n_eff` del pooling. |
| v0.5 | 2026-11-09 | Decisiones de orden de implementación: 15) `incertidumbre.py` posee la interfaz `FactorSampler` e inyecta; no importa `dependencia.py` (inversión de dependencia). 16) Hessiano NO expuesto por el modelo base → proxy `1/√peso_efectivo` por defecto, Hessiano como interfaz reservada (`s_hessiano=None`). |
