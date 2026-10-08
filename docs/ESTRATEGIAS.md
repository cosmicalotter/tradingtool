# Registro de estrategias

Cada estrategia se congela **antes** de ver resultados, se evalúa una sola vez con su criterio pre-registrado y queda aquí con su veredicto, sea bueno o malo. Archivar no borra nada: el código y la configuración siguen en el repositorio para que cualquiera pueda reproducir el resultado.

| Estrategia | Estado | Pre-registro | Veredicto |
|---|---|---|---|
| `insider-v1` (compras de insiders) | **Archivada** el 2026-10-08 | `docs/EVALUACION.md` | **NO PASA** (validación 2019–2025) |
| `rotacion-v1` (rotación de ETFs) | En evaluación | `docs/ETF-ROTACION.md` | Pendiente: `uv run tt etf-backtest` |

---

## `insider-v1`: compras de insiders (ARCHIVADA)

**Versión evaluada:**
- Código: commit `95dd546`. Las reglas no cambiaron desde que se congelaron el 2026-10-07; los commits posteriores solo tocaron el manejo de datos (limpieza de símbolos, fuente Alpaca).
- Configuración: hash `f01db21edb5fbc77` (screener `972be0aa41cd5de2`).

**Datos usados:**
- Form 4 de la SEC: datasets trimestrales 2013Q1–2026Q1.
- Precios: Alpaca (plan gratuito, desde 2016).
- Señales históricas: 2016-04-01 → 2025-09-30.
- Periodo de validación: 2019-01-01 → 2025-09-30. La reserva sigue cerrada.

### Resultado de la validación (63 días hábiles, neto de costos, frente a SPY)

| Grupo | Señales | Meses | Exceso neto | Mediana | Aciertos | t mensual | IC 90% |
|---|---|---|---|---|---|---|---|
| **Pasan los filtros (principal)** | 17.981 | 82 | **−0,86%** | −2,99% | 43% | −1,40 | [−2,57%, +0,24%] |
| Bloqueadas (contrafactual) | 50.077 | 82 | −0,13% | −4,67% | 38% | −0,21 | [−2,69%, +2,39%] |
| Pasan · con oportunista | 2.047 | 82 | −1,58% | −3,11% | 42% | −1,57 | [−3,70%, +0,09%] |
| Pasan · cluster (≥2 insiders) | 7.312 | 82 | −0,70% | −3,26% | 43% | −1,38 | [−2,96%, +0,31%] |
| Pasan · con ejecutivo | 7.431 | 82 | −0,76% | −3,31% | 43% | −1,51 | [−2,91%, +0,16%] |
| Pasan · solo directores | 10.550 | 82 | −0,93% | −2,81% | 43% | −1,32 | [−2,64%, +0,32%] |
| Pasan · deslistadas con −30% (robustez) | 17.981 | 82 | −1,16% | −3,15% | 43% | −1,78 | [−2,97%, −0,07%] |
| Pasan · exceso frente a IWM (tamaño) | 17.981 | 82 | +0,05% | −1,82% | 46% | 0,02 | [−0,76%, +0,83%] |

**Por año** (señales que pasan):

| Año | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|
| Exceso neto medio | −4,73% | +5,27% | −4,15% | +0,70% | −2,24% | −0,73% | −2,12% |

**Veredicto pre-registrado: NO PASA.** Falla en cuatro criterios:
- exceso neto medio > 0;
- t mensual ≥ 2;
- IC 90% por encima de 0;
- positivo en ≥ 60% de los años (solo 2 de 7).

Antes, la prueba con datos de Massive (oct. 2024 – sep. 2025) había dado −2,63% (t −4,16). Era INSUFICIENTE por cubrir solo 12 meses, pero apuntaba en la misma dirección.

### Qué aprendimos

1. **No hay ventaja frente a SPY después de costos.** El intervalo de confianza deja como mejor caso un resultado cercano a cero.
2. **Frente a empresas de su mismo tamaño (IWM), el exceso es prácticamente cero.** Las acciones que compran los insiders se comportan como cualquier empresa pequeña.
3. **Los filtros no agregan valor:** las señales bloqueadas no salieron peores que las aprobadas.
4. **Ningún subgrupo se salva.** Que haya un oportunista, un cluster o un ejecutivo, o solo directores, no cambia el resultado.
5. Esto coincide con la literatura sobre el decaimiento de anomalías publicadas: el efecto existió en datos antiguos y hoy es difícil de capturar después de costos.

**Sesgo conocido:** Alpaca cubre solo en parte las acciones deslistadas, lo que tiende a mejorar el resultado. La realidad probablemente fue algo peor.

### Qué queda funcionando

`tt diario` y el panel siguen guardando las señales nuevas como **seguimiento fuera de muestra**: es gratis y sirve para confirmar el veredicto con datos futuros. Se muestran con un aviso de "ARCHIVADA" y **no son recomendaciones**.

**Regla de oro:** cambiar parámetros ahora para "hacer que funcione" sería ajustar a la medida del pasado. Una eventual `insider-v2` necesitaría una hipótesis nueva justificada **antes** de mirar datos, y confirmarse en la reserva y en el día a día.
