"""Cerebro 2 · auditor independiente del Cerebro 1 (docs/v3_cerebro2_diseno.md v0.4).

Consume los reclamos del Cerebro 1 (p calibrada, p_conservadora, CRS, IC, lift), recomputa referencias que el
Cerebro 1 no puede mover (consenso de mercado ≥3 casas + Power, calibración realizada del segmento) y nunca
re-estima modelos. Emite uno de cinco estados por selección y por ticket mediante gates deterministas, con
fallo seguro a ABSTENCIÓN y techo CON_RESERVAS mientras `modo_experimental` siga activo.
"""
from golmodel.cerebro2.decision import auditar_seleccion, auditar_ticket, decidir
from golmodel.cerebro2.estados import Decision, Estado, Gate, Severidad
from golmodel.cerebro2.politica import CEREBRO2_VERSION, Politica, cargar_politica

__all__ = ["CEREBRO2_VERSION", "Decision", "Estado", "Gate", "Politica", "Severidad", "auditar_seleccion",
           "auditar_ticket", "cargar_politica", "decidir"]
