"""Rotación de ETFs por momentum (`rotacion-v1`). Pre-registro en docs/ETF-ROTACION.md.

- ``data``: descarga de ETFs (historia completa ajustada) y del efectivo (FRED DTB3).
- ``strategies``: reglas como funciones puras (sin red ni base de datos).
- ``engine``: simulación diaria con costos, banda de no operación y ejecución T+N.
- ``metrics``: métricas por periodo, bootstrap y simulación de aportes.
- ``report``: arma el informe completo y el veredicto pre-registrado.

Nada de este paquete envía órdenes.
"""
