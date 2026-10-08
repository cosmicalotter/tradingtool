"""Registro de estrategias archivadas, con su veredicto. Archivar no borra nada.

El código y la configuración de una estrategia archivada quedan intactos para poder reproducir
su resultado; las señales nuevas se siguen guardando solo como seguimiento (prueba fuera de
muestra gratuita). Detalle completo en docs/ESTRATEGIAS.md.
"""

from __future__ import annotations

ARCHIVED: dict[str, str] = {
    "insider-v1": (
        "insider-v1 está ARCHIVADA desde el 2026-10-08: NO PASÓ la validación 2019–2025 "
        "(exceso neto -0,86% a 63 días frente a SPY, t -1,40). Sus señales se guardan solo "
        "como seguimiento: NO son recomendaciones."
    ),
}


def archived_note(strategy_version: str) -> str | None:
    """Mensaje para mostrar si la estrategia está archivada; ``None`` si sigue activa."""
    return ARCHIVED.get(strategy_version)
