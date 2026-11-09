# GolModel V3 · Índice de documentos de diseño

> Registro central y auditable de todas las decisiones de diseño de la evolución
> `golmodelv2 → V3`. Ningún diseño se considera válido si no está listado aquí y
> no sigue la plantilla fija de abajo.

Última actualización del índice: **2026-11-09**

---

## 1. Catálogo de documentos

Dos categorías de "aprobado", explícitamente distintas:
- **Aprobado · decisión cerrada:** la decisión de diseño está tomada y no se reabre sin bump de versión.
- **Aprobado · política provisional:** criterios/umbrales fijados a priori pero **pendientes de validación
  OOS**; valen como política operativa bajo `modo_experimental=True`, NO como hechos validados.

### 1.1 Diseño aprobado · decisión cerrada

| Documento | Módulo / tema | Estado | Depende de | Últ. act. |
|---|---|---|---|---|
| `v3_dependencia_diseno.md` | `motor/dependencia.py` · probabilidad conjunta real de tickets 5-8 | aprobado · cerrada | — | 2026-11-09 |
| `v3_incertidumbre_diseno.md` | Muestreador τ/σ + calibración en `backtest/calibracion_tickets.py` | aprobado · cerrada (v0.5) | `v3_dependencia_diseno.md` | 2026-11-09 |
| `v3_tickets_objetivo_diseno.md` | `motor/tickets.py` · función objetivo del ticket 5-8 | aprobado · cerrada (v0.9) | `v3_dependencia_diseno.md`, `v3_incertidumbre_diseno.md` | 2026-11-09 |
| `v3_cerebro2_diseno.md` | `cerebro2/` · auditor independiente y estados de decisión | aprobado · cerrada (v0.5) | `v3_tickets_objetivo_diseno.md`, `v3_incertidumbre_diseno.md`, `v3_dependencia_diseno.md` | 2026-11-09 |

### 1.2 Aprobado · política provisional (pendiente de validación OOS)

| Documento | Módulo / tema | Estado | Depende de | Últ. act. |
|---|---|---|---|---|
| `v3_umbrales_prerregistro.md` | Pre-registro consolidado de umbrales + árbol de ajuste | aprobado · PROVISIONAL (pend. validación OOS) | todos los de 1.1 | 2026-11-09 |

### 1.3 Tratado como implementación (sin documento de diseño gateado)

| Pieza | Dónde se especifica | Nota |
|---|---|---|
| Mapa de migración / arquitectura Fase 1 | `docs/README_implementacion.md` | Referencia de implementación, no decisión gateada |
| `modelos/calibracion_mercado.py` (nivel de p) | Implementación bajo disciplinas ya aprobadas | Reusa walk-forward, pooling jerárquico, versionado, cobertura |

Estados posibles: **borrador** → **aprobado (cerrada | provisional)** → **implementado** → **obsoleto**.

---

## 2. Plantilla fija obligatoria para todo diseño V3

Cada `v3_*_diseno.md` DEBE abrir con esta cabecera y contener, en este orden, estas
seis secciones. Un diseño sin ellas no es auditable y no se implementa.

```
# <Título> · Diseño V3

- Estado: borrador | aprobado | implementado | obsoleto
- Versión: vMAJOR.MINOR
- Fecha: AAAA-MM-DD
- Autor(es): <quién>
- Depende de: <otros documentos de diseño>
- Afecta a: <archivos/módulos/tablas del repo>

## 1. Contexto
Por qué existe este diseño: problema, evidencia en el repo, requisito del Master Prompt.

## 2. Decisiones
Lo que se decide, de forma afirmativa y numerada. Cada decisión debe poder citarse.

## 3. Alternativas descartadas
Opciones consideradas y la razón explícita del descarte. Evita reabrir debates cerrados.

## 4. Supuestos
Qué se asume cierto (datos disponibles, comportamiento del mercado, estadística) y que,
si cambia, invalida el diseño.

## 5. Limitaciones
Qué NO resuelve este diseño, riesgos conocidos y deuda deliberada.

## 6. Versión
Historial de cambios: versión, fecha, qué cambió y por qué.
```

---

## 3. Reglas del registro

1. Todo diseño nuevo se añade a la tabla del §1 antes de escribirse.
2. Al cambiar un diseño, se actualiza su sección **Versión** y la fecha en este índice.
3. Un diseño pasa a **aprobado** solo por decisión explícita del responsable del proyecto.
4. Las dependencias son dirigidas: si A depende de B, B debe estar al menos en **borrador**
   antes de aprobar A.
5. Estos documentos describen DISEÑO, no implementación. El código llega después de
   **aprobado**.
