"""Momios americanos (como los muestra Caliente: «−600», «+350») ↔ cuota decimal."""
from __future__ import annotations


def americano_a_decimal(a: float) -> float | None:
    """+350 → 4.50 · −600 → 1.1667. Fuera de ±100 no es un momio americano válido: None."""
    a = float(a)
    if a >= 100:
        return 1.0 + a / 100.0
    if a <= -100:
        return 1.0 + 100.0 / abs(a)
    return None


def decimal_a_americano(d: float) -> int | None:
    """4.50 → +350 · 1.1667 → −600 (redondeado)."""
    d = float(d)
    if d <= 1.0:
        return None
    return round((d - 1.0) * 100) if d >= 2.0 else -round(100.0 / (d - 1.0))
