# Fase 4: motor con CRS y Módulo 2

Implementación: `motor/modulo2.py`, `modelos/crs.py`, `motor/bankroll.py`. Pruebas: `tests/test_fase4.py` y `db/tests/test_schema.sql`.

## 1. Flujo por partido (recálculo del Módulo 1 y el Módulo 2)

```
PARA cada partido del día (America/Mexico_City):
    datos      ← historial de liga hasta inicio (xG, xGA), calendario de todas las competiciones,
                 tabla as-of, cuotas vigentes (casa de referencia + casa de ejecución)
    M          ← matriz Dixon-Coles(λ_local·(1+ajuste_bajas), λ_visit·(1+ajuste_bajas), ρ)
    p_mercado  ← Shin(cuotas de referencia) por grupo   # sin Pinnacle: mediana de ≥3 casas → umbral +0.02
    Mm         ← matriz implícita del mercado (si el error de ajuste es ≤ 0.015)

    # --- CRS ANTES de las cuotas justas finales ---
    crs        ← CRS.calcular(datos, p_modelo_1X2(M), p_mercado_1X2)
        favorito ← lado con mayor p_mercado (sin margen); si max < 0.40 → sin favorito, CRS = INSUFICIENTE
        5 parámetros → {disponible, activado, puntos, valor crudo, umbral}
        total    ← round(100 · puntos / máximo_disponible)   # parcial si faltan parámetros
        si disponibles < 3 → INSUFICIENTE
        alerta   ← ALTA (≥70) | MEDIA (≥40) | BAJA
        veto     ← total ≥ 70 → selecciones_vetadas = {gana favorito, AH favorito −1.0/−1.5, DC favorito+X}
    SI crs.veto:
        M_crs        ← matriz con λ_favorito × (1 − 0.10)       # coherente para TODOS los mercados
        sugerencia   ← cuota justa de AH +1.0 (con push) y +1.5 del débil, desde M y desde M_crs
    M_decision ← M_crs SI (veto Y modo = 'activo') SI NO M       # por defecto: modo 'sombra'

    PARA cada selección s:
        p_modelo ← P(M_decision, s);  p_modelo_crs ← P(M_crs, s) (se guarda aunque esté en sombra)
        p_adj    ← α·p_modelo + (1−α)·p_mercado
        EV       ← p_adj·cuota − 1        (AH ±1.0: p_gana·(cuota−1) − p_pierde)
        estado   ← valor (EV ≥ umbral efectivo) | marginal (0 < EV < umbral) | sin valor
        SI s ∈ vetadas Y veto_simples → no genera pick ("Filtrado por CRS")
    INSERTAR predicción inmutable (matriz, CRS completo, versión, info_hasta)
```

Umbral efectivo = 0.04 (simple) o 0.08 (SGP; 0.12 si no hay matriz de mercado), +0.02 si α no está calibrado y +0.02 si falta la casa de referencia.

## 2. Módulo 2: generador de parlays

```
ENTRADA: partidos del día con predicción, cuotas y CRS
SALIDA: mejor parlay + 2 alternativas, más la lista de descartes con su motivo

candidatos_por_partido ← {}
PARA cada partido e:
    SI cuotas no vigentes: descartar partido ("Cuota no vigente"); CONTINUAR
    bloques ← []
    # Simples (solo selecciones aptas para parlay: sin push)
    PARA cada selección s con cuota de la casa:
        SI s ∈ e.crs.vetadas: descartar ("CRS {total} ALTA: favorito vetado"); CONTINUAR
        p_mercado ← cuota de s sin margen; si no hay: derivada (DC = suma de 1X2; resto desde Mm) → α tratado como no calibrado
        p_adj ← α·P(M_decision, s) + (1−α)·p_mercado
        exigir p_adj ≥ 0.60, 1.30 ≤ cuota ≤ 2.50, EV ≥ umbral efectivo
        bloques += simple(s)
    # SGP de 2 selecciones (solo con cuota SGP REAL: API o captura manual)
    PARA cada par (a, b) con cuota SGP:
        SI {a, b} ∩ vetadas ≠ ∅: descartar ("SGP con selección del favorito")
        P ← Σ celdas de M_decision donde se cumplen a y b      # nunca P(a)·P(b)
        descartar si P = 0 (imposible) o P/min(P(a), P(b)) ≥ 0.98 (redundante)
        p_adj ← α·P + (1−α)·P_Mm(a, b)   (sin Mm: p_adj = P y umbral 0.12)
        exigir p_adj ≥ 0.60, cuota en rango, cuota ≥ 1/p_adj, EV ≥ umbral SGP
        bloques += sgp(a, b)   # badge "Correlación calculada", lift = P / (P(a)·P(b))
    SI bloques: candidatos_por_partido[e] ← argmax_bloque g(bloque)
        donde g = p·log(1 + s·b) + (1−p)·log(1 − s), con s = stake simple (Kelly 0.25, tope 2u)

top ← los 12 partidos con mayor g
parlays ← []
PARA k EN 3..5, PARA cada combinación C de k partidos de top:
    P     ← Π p_adj(bloque)            # independencia SOLO entre partidos distintos
    SI P < 0.15: CONTINUAR
    cuota ← Π cuota(bloque)
    f*    ← (b·P − (1−P)) / b,  b = cuota − 1
    stake ← min(0.10·f*, 0.5u);  SI stake ≤ 0: CONTINUAR
    g     ← P·log(1 + stake·b) + (1−P)·log(1 − stake)
    parlays += (C, P, cuota, 1/P, EV = P·cuota − 1, stake, g)
ordenar parlays por g (no por EV)
DEVOLVER parlays[0..2]

# Banca, al final del día (simples + parlays comparten el límite)
asignar_exposicion(apuestas ordenadas por g, límite 5u, máximo 1 simple con stake por partido)
```

**Por qué g y no EV:** con Kelly 0.10, un parlay de 5 bloques con más EV bruto recibe menos stake y crece menos que uno de 3. La prueba `test_modulo2_kelly_no_premia_parlays_largos_por_ev` lo verifica.

## 3. Contrato de la UI
- **Módulo 1:** badge `CRS {total}` en color según la alerta, con "parcial" si faltan parámetros. Al pasar el cursor se muestran los 5 componentes con su valor crudo y su umbral. Con alerta ALTA:
  - las selecciones del favorito se marcan "Filtrado por CRS";
  - se muestra la sugerencia de AH +1.0 / +1.5 del débil con su cuota justa. Solo se vuelve pick si la cuota real de la casa da EV ≥ umbral.
- **Módulo 2:** cada bloque de la tarjeta muestra su CRS. Si un partido con CRS ALTA entra al parlay por un mercado alternativo, se muestra "Favorito filtrado por CRS".
- **Modo del ajuste:** se muestra "Ajuste CRS: sombra" hasta que `backtest/crs_eval.py` permita activarlo.

## 4. Defensa en profundidad (base de datos)
- `predicciones`: el CRS completo, con restricciones de coherencia. Por ejemplo, no puede haber veto sin selecciones vetadas, ni alerta sin total.
- Triggers: no se puede insertar un pick ni un bloque de parlay con una selección vetada o con push, ni con un CRS distinto al de su predicción.
- `v_crs_efectividad`: por alerta, compara la frecuencia real de victoria del favorito con lo que decían el mercado sin margen y el modelo. Así se ve si el CRS detecta riesgo de verdad.
