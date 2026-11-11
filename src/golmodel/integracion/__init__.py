"""Adaptadores entre la BD / el pipeline V2 y los módulos de modelo V3.

REGLA: aquí no hay lógica de modelo. Cada adaptador lee (BD → objetos del módulo V3) o llama a un módulo V3 con esos
objetos; toda decisión de negocio (qué cuota, qué umbral, qué segmento) vive en el módulo correspondiente y se
REUTILIZA, nunca se reimplementa. Cuando falta un dato, el contrato es `None` explícito / omisión con motivo — jamás
un valor inventado.
"""
