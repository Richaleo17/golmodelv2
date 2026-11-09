# Probabilidad conjunta de tickets (`motor/dependencia.py`) · Diseño V3

- Estado: borrador
- Versión: v0.1
- Fecha: 2026-11-09
- Autor(es): equipo V3
- Depende de: `README_implementacion.md` (mapa de migración Fase 1)
- Afecta a: `src/golmodel/motor/dependencia.py` (nuevo), `src/golmodel/motor/modulo2.py` (corrige `:229`), `db/migraciones/006_tickets_5_8.sql` (nuevo)

## 1. Contexto

En `src/golmodel/motor/modulo2.py:229` el parlay entre partidos distintos calcula la
probabilidad conjunta como `p = math.prod(b.p_adj for b in combo)`, asumiendo
independencia. El Master Prompt (§10.1-F, decisión crítica #2) lo prohíbe: sin un modelo
de dependencia, los tickets de 5-8 son estadísticamente deshonestos. El SGP intra-partido
(`modelos/sgp.py`) ya es correcto (suma celdas de la matriz, no multiplica); el defecto es
exclusivamente el combinado entre partidos.

Naturaleza real de la dependencia entre partidos distintos:
- **Física:** casi nula (equipos distintos, eventos separados).
- **Epistémica (dominante):** las probabilidades vienen de parámetros *estimados*. Condicionado
  a los parámetros verdaderos los partidos son independientes, pero no los conocemos; los
  parámetros compartidos (misma liga, misma versión de modelo, misma foto de mercado) inducen
  correlación positiva entre las probabilidades de las piernas. Lo correcto es
  `E_θ[Π p_i(θ)]`, no `Π p_i(θ̂)`.
- **Compuesto de miscalibración:** un exceso de confianza ε por pierna se vuelve `(1+ε)^n`.

## 2. Decisiones

1. Se crea `motor/dependencia.py` como único punto de cálculo de la probabilidad conjunta
   de un ticket de varias piernas.
2. Método: **Monte Carlo jerárquico con factores latentes compartidos**, Rao-Blackwellizado
   (producto analítico de piernas condicionado a cada sorteo de parámetros).
3. Modelo generativo por sorteo `s`:
   - Global: `log g⁽ˢ⁾ ~ Normal(0, σ_g²)`.
   - Por liga ℓ: `log u_ℓ⁽ˢ⁾ ~ Normal(0, σ_ℓ²)` (compartido por sus piernas).
   - Idiosincrático por partido: `log δ ~ Normal(0, τ_i²)`.
   - `λ' = λ̂ · g · u_ℓ · δ` → matriz `M_i⁽ˢ⁾ = matriz_marcadores(λ'_loc, λ'_vis, ρ_i)` (reusa v2).
   - `p_i⁽ˢ⁾ = Σ celdas del evento` (o `sgp.evaluar` si la pierna es SGP).
   - `π⁽ˢ⁾ = Π_i p_i⁽ˢ⁾`.
4. Salidas:
   - `p_media = mean_s π⁽ˢ⁾` (conjunta honesta).
   - `p_independencia = Π p_i(θ̂)` (número ingenuo; se conserva y se muestra, §10.1-F).
   - `p_conservadora = percentil_q(π⁽ˢ⁾)` (q configurable, defecto 0.25).
   - `ic`, `lift = p_media/p_independencia`, `error_mc`, `metodo`, `version`, `semilla`, `n_draws`.
5. Capa opcional de distribución de pago (muestreo de marcador por partido) solo cuando se
   necesita E[ganancia], pushes y "reducido"; replica la lógica de `parlay_liquidaciones`.
6. La dependencia emerge de la estructura (factores compartidos), no de un parámetro de
   correlación libre. Un ticket concentrado en Over queda automáticamente más correlacionado
   que uno mixto → alimenta la diversificación del §10.1-D/E.

## 3. Alternativas descartadas

- **`Π p_i` (independencia):** es el bug a corregir.
- **Sumar varianzas:** aplica a sumas, no al producto de indicadores dependientes.
- **Matriz de covarianza liga×liga estática:** requiere estimar correlaciones con datos de
  tickets escasos; frágil. Se reporta como diagnóstico derivado, no se ajusta.
- **Cópula gaussiana sobre indicadores de pierna:** introduce un parámetro de dependencia libre
  sin datos para fijarlo. El MC jerárquico es la versión principista; una cópula implícita puede
  reportarse a posteriori.
- **Muestrear siempre el marcador (sin Rao-Blackwell):** mayor varianza Monte Carlo para la
  probabilidad de ganar. Se reserva solo para la distribución de pago.

## 4. Supuestos

- Condicionado a los parámetros de un sorteo, los partidos distintos son independientes.
- La matriz 11×11 de cada partido captura bien la distribución de marcadores dado λ, ρ.
- La incertidumbre relevante se representa como multiplicadores log-normales sobre λ (ver
  `v3_incertidumbre_diseno.md`).
- Las probabilidades de entrada ya están calibradas en nivel (`calibracion_mercado.py`).

## 5. Limitaciones

- No modela dependencia física real (p. ej. mismo árbitro en dos partidos): se asume despreciable.
- Mismo equipo en dos piernas (ticket multi-día) requiere acoplar su `δ`; dentro de un día el
  esquema ya lo impide.
- La calidad de `p_media`/`p_conservadora` depende enteramente de que τ/σ estén bien calibrados
  (documento dependiente).
- El coste computacional crece con `S × n_piernas`; se fija `S` por presupuesto de error MC.

## 6. Versión

| Versión | Fecha | Cambio |
|---|---|---|
| v0.1 | 2026-11-09 | Diseño inicial del motor de dependencia. |
