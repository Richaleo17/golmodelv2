"""Motor NFL (fútbol americano). Paquete SEPARADO del motor de fútbol: no usa Poisson ni Dixon-Coles y no comparte tablas
con él. Margen ~ Normal(μ, σ) con μ de Elo + EPA; total ~ Normal. Mercados: moneyline, spread y totales (con push en líneas enteras).

DVOA (FTN/Football Outsiders) es de pago y no se usa: la parte «ajustada por rival» la cubre la EPA neta suavizada contra la
calidad del calendario implícita en Elo.
"""
