# Protocolo de evaluación y pre-registro: estrategia `insider-v1`

> **Estado (2026-10-08): ARCHIVADA. NO PASÓ la validación 2019–2025.** Resultados y lecciones en `docs/ESTRATEGIAS.md`.

**Congelado el:** 2026-10-07, **antes** de ver cualquier resultado histórico.
**Configuración:** `config/screener.yaml`, `config/risk.yaml`, `config/costs.yaml` y `config/outcomes.yaml`. El hash de la configuración queda registrado en cada ejecución (`tt revisar` lo muestra).

> **Regla de oro:** si cambias un parámetro después de ver resultados, ya no es `insider-v1`. Creas `insider-v2` y la evalúas con datos que **no** usaste para diseñarla: el periodo de reserva y lo que vaya llegando día a día.

---

## 1. La hipótesis, en palabras simples

Cuando directivos o directores de una empresa **compran acciones con su propio dinero en el mercado abierto**, de forma **no rutinaria** y **sin un plan programado (10b5-1)**, eso anticipa en promedio un mejor comportamiento de la acción en los meses siguientes.

La base académica es Cohen, Malloy y Pomorski (2012, *Journal of Finance*): las compras "oportunistas" tuvieron retornos anormales significativos y las "rutinarias" no. Lakonishok y Lee (2001) y Jeng, Metrick y Zeckhauser (2003) encontraron resultados en la misma línea para las compras de insiders.

**Por qué podría funcionar para ti:** la señal es pública, gratuita y está en los datos de la SEC. El efecto se concentra en empresas pequeñas y medianas, donde los fondos grandes tienen poca capacidad, y el horizonte es de meses, no de milisegundos.

**Por qué podría NO funcionar (igual de importante):**

- Las anomalías publicadas pierden entre un tercio y la mitad de su rentabilidad después de publicarse (McLean y Pontiff, 2016).
- Los costos en acciones pequeñas son altos.
- Hoy hay sitios web que publican estas compras al instante.

**Expectativa honesta:** un resultado probable es que el efecto exista pero sea pequeño después de costos.

## 2. Reglas de `insider-v1`

Todas se fijaron a priori a partir de la literatura. **Ninguna se optimizó.**

| Regla | Valor | Motivo |
|---|---|---|
| Transacción | Código **P** (compra en mercado abierto), acciones comunes, no derivados | Es la única operación con información; los otros códigos son premios, ejercicios, regalos, etc. |
| Monto mínimo por insider y evento | US$10.000 | Filtro de ruido a priori |
| Retraso máximo del reporte | 10 días calendario | Información fresca; la ley exige reportar en 2 días hábiles |
| Plan 10b5-1 | Se excluye (por casilla desde 2023 y por mención en notas antes de 2023) | Compras programadas: no informan nada |
| Roles | Directivos y directores. Se excluyen los dueños de más del 10% sin cargo | Los dueños >10% sin cargo suelen ser fondos con otros motivos |
| Clasificación | Se **excluyen los rutinarios** (mismo mes calendario en cada uno de los 3 años previos) | Cohen-Malloy-Pomorski |
| Enmiendas (4/A) | Excluidas | Evitar contar doble |
| Precio mínimo | US$5, el precio pagado por el insider | Corte académico usual; evita microcaps extremas |
| Compras privadas | Se excluye si el precio pagado está a más del 25% del cierre de mercado del día de la compra | El código P también incluye colocaciones privadas del emisor |
| Liquidez | Volumen promedio de 20 días ≥ US$250.000/día | Poder entrar y salir; costos razonables |
| Evento | Todas las compras válidas de una empresa con la misma fecha de presentación | — |
| Entrada (medición) | **Apertura del día hábil siguiente** a la fecha de presentación | Sin mirar al futuro |
| Horizonte principal | **63 días hábiles** (~3 meses) | Secundarios: 5, 10, 21 y 126 |
| Benchmark | SPY, en la misma ventana (principal); IWM como secundario para controlar el tamaño | A nivel de portafolio se comparará contra el ETF UCITS núcleo |
| Costos | Modelo IBKR Pro tiered + spread/deslizamiento según liquidez (50/25/10/5 pb por lado) para una posición de referencia de US$1.000 | Conservador a propósito |

### 2.1 Evidencia revisada y ajustes hechos ANTES de ver datos (2026-10-07)

Una revisión de la literatura, solo con resúmenes y fuentes secundarias porque los PDF estaban bloqueados, encontró:

- **El famoso "82 pb/mes" de Cohen-Malloy-Pomorski es de un portafolio largo-corto** (compras oportunistas menos ventas oportunistas), no de solo comprar. No se pudo verificar el alfa de solo la pata compradora.
- **Decaimiento:** la única réplica posterior a 2008 encontrada (una tesis de maestría, confianza baja) reporta un poder predictivo 60–70% menor (≈0,3–0,4% mensual).
- **Desde la ley SOX de 2002**, buena parte de la reacción ocurre al publicarse el Form 4 (1–2% en 2–3 días). Quien entra al día siguiente se pierde una parte.
- **El tamaño de la compra no ayuda:** el retorno porcentual baja con el tamaño (Cziraki y Gider, 2021).
- **Roles:** tras SOX, los altos ejecutivos no superan a los directores. Los dueños de más del 10% sin cargo no tienen retorno anormal.
- **Lo mejor documentado:** compras **en cluster**, es decir, varios insiders en 30 días (más de 2% al mes siguiente según Alldredge y Blank, 2019).

**Ajustes en consecuencia,** antes de cualquier backtest:

- Precio mínimo de US$2 a **US$5**.
- Filtro de **compras privadas**.
- En el puntaje (solo ordena ideas), **peso 0 al tamaño de la compra y al cargo ejecutivo**; el cluster y el aumento de participación conservan su peso.

**Análisis secundarios pre-registrados:** solo oportunistas, cluster de ≥2 insiders en 30 días, desglose por rol, periodo desde abril de 2023 (llegada de la casilla 10b5-1) y exceso contra IWM.

**Expectativa realista:** si el efecto sobrevive, será pequeño (décimas de punto porcentual al mes) y concentrado en empresas pequeñas, justo donde los costos son mayores. Que el veredicto salga NO PASA es un desenlace plausible y valioso.

## 3. Garantías contra la mirada al futuro (*look-ahead*)

Cada una está verificada por un test automático:

- Toda señal se ancla en `filing_date`, el día en que la información fue pública, nunca en la fecha de la transacción.
- La clasificación rutinario/oportunista usa solo filings presentados **antes del 1 de enero** del año del evento.
- Liquidez, ATR y precio de referencia usan solo barras con fecha ≤ `filing_date`.
- Los resultados se miden desde la apertura del día hábil **siguiente** a la fecha de presentación. Es conservador (aunque el filing llegue antes de la apertura, se espera al día siguiente) y realista para quien corre la rutina una vez al día.
- Si una acción deja de cotizar (deslistada), el resultado se cierra con su último precio y se marca `truncated`. No se descarta, para evitar el sesgo de supervivencia.

## 4. Criterio principal (pre-registrado)

Grupo: señales que **pasan** los filtros. Horizonte: 63 días hábiles. Origen: histórico (`backtest`). El grupo pasa solo si cumple **todo**:

1. Al menos **200 señales** y al menos **24 meses** con señales.
2. Exceso medio **neto de costos** sobre SPY **> 0**.
3. **t mensual ≥ 2**: se promedia el exceso de las señales de cada mes y se calcula el t de esos promedios, porque las señales del mismo mes no son independientes.
4. **Intervalo de confianza del 90%** (bootstrap por meses) completamente por encima de 0.
5. Exceso neto positivo en **al menos el 60% de los años**.

`uv run tt evaluar` aplica este criterio y da el veredicto: **PASA**, **NO PASA** o **INSUFICIENTE**.

**Hipótesis secundaria (contrafactual):** las señales que pasan deben superar a las **bloqueadas**. Si no lo hacen, los filtros no agregan valor.

**Análisis exploratorios** (se reportan, pero NO se usan para elegir parámetros de `insider-v1`): con oportunista vs. sin oportunista, cluster (≥2 insiders en 30 días) vs. individual, con ejecutivo vs. solo directores, y los horizontes secundarios.

## 5. Periodos

| Periodo | Fechas | Uso |
|---|---|---|
| Diseño | 2009 – 2018 | Solo para encontrar bugs y entender los datos. No se ajustan parámetros de v1. |
| Validación | 2019 – 2025-09-30 | Evaluación principal de v1 |
| **Reserva** | **desde 2025-10-01** | **Bloqueada**. Se abre UNA vez, al final, con `tt historico --abrir-reserva`, y la apertura queda registrada. |
| Día a día (`live`) | desde 2026-10-07 | La prueba fuera de muestra más honesta: datos que aún no existían al congelar |

**Limitación de datos y presupuesto (honesto):** el plan gratuito de precios cubre aproximadamente los últimos 2 años; se verificará al configurar la clave. Eso deja la validación con ~12 meses, por debajo de los 24 exigidos, y el veredicto será **INSUFICIENTE**.

Hay dos caminos, ambos válidos:

- **(a) Gratis.** Acumular resultados día a día en paper durante 12–24 meses.
- **(b) Pago puntual.** Comprar **un mes** de un plan con historia larga (incluyendo acciones deslistadas) para evaluar 2009–2025 de una vez. La opción más barata, EODHD a US$19,99, ya está integrada; ver `docs/COSTOS.md`. Se decide con tu aprobación explícita.

## 6. Qué pasa después del veredicto

- **PASA** → Fase 3: paper trading con la versión congelada, al menos 6 meses y al menos 50 operaciones, con aprobación manual de cada idea y medición del deslizamiento real frente al modelado.
- **NO PASA** → v1 se archiva con su informe; eso también es un resultado. Se puede diseñar v2 con lo aprendido, solo con datos de diseño y validación, y confirmarla en la reserva y en el día a día.
- **INSUFICIENTE** → seguir acumulando datos día a día o decidir el camino (b).

Pasar a dinero real exige además los criterios de la Fase 4 del plan (`docs/00-critica-y-plan.md`) y **tu autorización escrita**. Cada orden real requiere tu aprobación explícita.

## 7. Sesgos conocidos y cómo se controlan

| Sesgo | Control |
|---|---|
| Mirar al futuro | §3 y sus tests |
| Supervivencia | Se usan los datasets completos de la SEC (incluyen empresas que luego desaparecieron) y los resultados truncados no se descartan. Massive "grouped daily" incluye las acciones deslistadas en el periodo que cubre. **Prueba de robustez:** `tt evaluar` repite el cálculo restando 30% a las señales que dejaron de cotizar (Shumway, 1997), porque ningún proveedor barato da el "retorno de deslistado". |
| Sobreajuste / múltiples pruebas | Parámetros de la literatura, sin optimizar. Toda ejecución queda registrada en la tabla `runs` (commit y hash de config). Las versiones nuevas se nombran y se corren en paralelo. |
| Precios ajustados por splits | Reparación automática de saltos; el filtro de precio usa el precio pagado por el insider (sin ajustar). |
| Costos subestimados | Modelo conservador; se reporta el costo medio por señal. |
| Narrativa | El LLM no participa en v1. Si se agrega, se evalúa con A/B y placebo, y solo con eventos posteriores a su fecha de corte de entrenamiento. |
| Filings conjuntos | La identidad del insider es el primer reporting owner; el rol es el de cualquiera de los dueños del filing. |
| Benchmark que no controla tamaño | Las compras de insiders se concentran en empresas pequeñas, así que un exceso sobre SPY podría ser solo "prima de tamaño". Por eso `tt evaluar` reporta también el exceso contra IWM (Russell 2000) como análisis secundario. Si el exceso desaparece contra IWM, la "ventaja" era solo prima de tamaño. |
| Señales superpuestas | Muchas señales comparten meses; por eso el t se calcula sobre promedios mensuales y no por señal. |
