# Panel de apoyo a decisiones de trading (IBKR): crítica, investigación y plan

**Versión:** v0, borrador para tu aprobación · **Fecha:** 2026-10-07 · **Estado:** sin código. Hay que aprobar esto antes de construir.

> Convención del documento: **[V]** = dato que verifiqué en fuentes en octubre de 2026 (las fuentes están al final). **[E]** = estimación o supuesto mío, con el razonamiento a la vista. **[C]** = hay que confirmarlo con un contador o directamente con IBKR. Nada de esto es asesoría financiera ni tributaria.

---

## 0. Resumen ejecutivo

1. **El plan está muy bien pensado en metodología** (núcleo determinista, LLM como lector, benchmark como rival, contrafactuales, versiones congeladas). Es mejor que el 95% de los planes de "bot con IA" que circulan.
2. **El problema central no es técnico sino de escala.** Con US$150 iniciales + US$300/mes tendrás ~US$3.750 al cabo de 12 meses y ~US$7.350 a los 24 [E]. Un alfa *excelente* de +5%/año (lo que pocos fondos profesionales sostienen) equivale a **~US$100 el año 1 y ~US$280 el año 2**. Mientras tanto, los costos fijos de operar la herramienta (datos, LLM, VPS) de **US$15–80/mes equivalen a 5%–26% anual del capital del año 1** [E]. Con este capital, los costos fijos y las comisiones mínimas se comen cualquier edge plausible.
3. **Mi recomendación honesta:** pon **~90–100% de tus aportes en un ETF global de acumulación domiciliado en Irlanda (UCITS) y mantenlo** (eso es el núcleo). Construye la herramienta como **laboratorio de investigación y aprendizaje**, en paper, con presupuesto de costos bajo (≤ US$15/mes el año 1). Solo cuando una estrategia pase criterios objetivos **y** tu capital "satélite" supere ~US$2.500 (año 2 aprox.), arriesgas dinero real en pequeño.
4. **Impuestos en Colombia (verificado):** si vendes antes de 2 años, la ganancia es **renta ordinaria con tarifa progresiva (hasta 39%)**. Si vendes después de 2 años, es **ganancia ocasional al 15%**. Además, **las pérdidas en venta de acciones no son deducibles (art. 153 E.T.)**. Ese esquema asimétrico castiga mucho al trading activo frente a comprar y mantener [V][C].
5. **Logística que cambia números:** los colombianos solo tienen acceso a IBKR Pro (no Lite). La comisión mínima es US$0,35 por orden (Tiered) o US$1 (Fixed). La TWS API **no** soporta fracciones de acciones. IBKR no paga intereses sobre los primeros US$10.000 en efectivo. El **costo de enviar dinero desde Colombia** puede pesar más que las comisiones [V].
6. **Dónde sí puede haber edge para ti:** nichos con capacidad limitada que los fondos grandes no pueden explotar (p. ej. *odd-lot tender offers*), eventos leídos de filings (compras "oportunistas" de insiders, Form 4) y, sobre todo, **el edge conductual** de no cometer errores. Dónde **no**: velocidad ante noticias, predicción de precios con LLM, rupturas técnicas genéricas en acciones grandes y PEAD en acciones grandes (evidencia académica en contra desde ~2006, aunque disputada).
7. **Trampa nueva y poco conocida:** cualquier backtest de señales con LLM sobre fechas anteriores al corte de entrenamiento del modelo está contaminado (el modelo "ya sabe" qué pasó). Con Claude Haiku 4.5 (corte de entrenamiento julio 2025) solo hay ~14 meses de historia "limpia" [V].
8. **La palanca financiera más grande que tienes no es el trading** sino tu capital humano (carrera médica, especialización) y tu tasa de ahorro. Dos horas diarias en un trabajo remunerado aportan más dinero que cualquier edge sobre US$4.000. Más abajo propongo cómo combinar las cosas.
9. **Cambio de diseño propuesto:** todo **con datos de cierre diario (EOD)**. Así no necesitas datos en tiempo real (pagos), ni VPS, ni IB Gateway encendido 24/7. Corre 10 minutos al día en tu computador después del cierre. Esto reduce la complejidad a la mitad.

---

## 1. Crítica honesta del plan

### 1.1 Lo que está muy bien (consérvalo)

- **Humano como filtro final y núcleo determinista.** Es exactamente lo correcto, y es consistente con lo que muestra la evidencia práctica.
- **El ETF amplio como rival.** Es la pregunta correcta, y casi nadie se la hace. El dato de fondo: ~90% de los fondos activos de gran capitalización en EE. UU. quedaron por debajo del S&P 500 a 15 años (SPIVA) [V].
- **Registrar trades bloqueados y contrafactuales.** Es de lo más valioso del plan. Con eso mides si tú (o el LLM) agregan o restan valor al filtrar.
- **Versiones congeladas y pruebas en paralelo.** Es correcto.
- **Lista de sesgos (look-ahead, supervivencia, overfitting).** Está bien identificada.
- **La lectura de filings como uso del LLM.** Es el uso más defendible.

### 1.2 Lo que está mal o es riesgoso

| # | Problema | Por qué importa | Propuesta |
|---|---|---|---|
| 1 | **Umbral de "~15% anual neto"** | Un umbral absoluto no dice nada. En 2023–2024 el S&P 500 subió más de 20% cada año, así que una estrategia con 15% habría perdido contra el benchmark. En un año de -20%, un 0% sería excelente. Además, 15% neto es superar al mercado por ~5 puntos al año, algo extremadamente raro. | Criterio **relativo y ajustado por riesgo**, con significancia estadística y costos fijos incluidos (ver §5, Fase 0). |
| 2 | **No se dimensionó el efecto del capital pequeño** | Mínimos de comisión, spread, costos fijos (datos y LLM) y acciones enteras (sin fracciones por API). Con posiciones de US$100–300, un swing con rotación alta pierde **8%–21% al año solo en fricción** [E]. | Diseño EOD de baja rotación, presupuesto de costos y capital mínimo antes de operar en real. |
| 3 | **Impuestos colombianos sub-ponderados** | Menos de 2 años → renta ordinaria. Pérdidas no deducibles. Además, el **valor total de cada venta cuenta como ingreso bruto**, así que operar mucho puede volverte declarante de renta aunque ganes poco [V][C]. | Incluir el impuesto estimado en el backtest y preferir holding ≥ 2 años para el núcleo. |
| 4 | **"30 días de paper" vs. potencia estadística** | Para detectar un edge modesto (+0,15R por trade, desviación 1,2R) con t = 2 necesitas **~256 trades**. Para validar un alfa con *information ratio* 0,5 con datos en vivo hacen falta **~16 años** [E]. | Aceptar que el forward test sirve para encontrar bugs y medir ejecución, y que la evidencia de edge viene de un backtest bien hecho, de una razón económica y de una pre-registración. |
| 5 | **Fase 4 (VPS y automatización) llega antes de tiempo** | IB Gateway pide re-autenticación periódica (con 2FA en real). Montar Docker con IBC en un VPS es la parte más frágil del stack para alguien que no es desarrollador. | Postergarla indefinidamente. Con estrategias EOD quizá nunca haga falta. |
| 6 | **El LLM también tiene look-ahead** | Si haces backtest de "LLM lee el 8-K de 2022", el modelo pudo ver en su entrenamiento lo que pasó después con esa acción. | Validar componentes LLM solo con eventos **posteriores** al corte de entrenamiento del modelo, y en forward. |
| 7 | **El benchmark "VOO/VT" no es tu alternativa real** | Como residente colombiano, VOO te cuesta 30% de retención sobre dividendos y te expone al impuesto de sucesiones de EE. UU. por encima de US$60k. | Benchmark = el ETF que realmente comprarías (UCITS de acumulación), medido en USD **y en COP**, después de sus costos. |

### 1.3 Lo que falta

- **Pre-registro.** Antes de ver resultados, escribir la hipótesis, los parámetros, el universo y los criterios de éxito. Es el antídoto principal contra el overfitting y contra mi sesgo y el tuyo de "seguir probando hasta que funcione".
- **Registro de experimentos.** Cada backtest guardado con commit de git, hash de config y hash de datos. El *número* de pruebas hechas es necesario para calcular el Deflated Sharpe Ratio.
- **Prueba de "respuesta conocida" del motor de backtest.** Replicar un resultado publicado (p. ej. la regla de media móvil de 10 meses de Faber sobre SPY). Si no da parecido, el motor tiene un bug.
- **Presupuesto de costos fijos** (datos, LLM, servidor) tratado como costo de la estrategia.
- **Objetivo financiero y horizonte del dinero.** ¿Necesitas parte de esto para el año rural, la especialización o una mudanza? Ese dinero no debe estar en un satélite activo.
- **Fondo de emergencia en COP.** Hoy hay CDT al 12–14% EA [V], con inflación ~6% [V].
- **Riesgo cambiario medido.** La TRM pasó de ~4.300 a ~3.310 COP/USD entre 2025 y octubre 2026 [V]. Eso es una pérdida de ~20–25% en pesos para cualquier activo en USD en ese periodo. Debes ver tus resultados en ambas monedas.
- **Plan de costo de oportunidad de tu tiempo** (ver §2.2).

---

## 2. Respuestas a la sección 6 (ordenadas por impacto esperado)

### 2.1 (Tu pregunta 1) ¿Cuál es el camino con mejor retorno/riesgo para tu caso?

**Supuestos explícitos [E]:** horizonte de más de 10 años, aportes de US$300/mes, renta variable global con retorno esperado de largo plazo de ~6–8% nominal en USD (no garantizado; puede ser mucho menor en una década concreta), tolerancia alta a la volatilidad y sin necesidad de liquidez en 3+ años (a confirmar).

**Proyección de aportes** (solo aritmética, no es promesa):

| Mes | Retorno 0% | Retorno 7%/año | Retorno 10%/año |
|---|---|---|---|
| 12 | US$3.750 | US$3.875 | US$3.927 |
| 24 | US$7.350 | US$7.860 | US$8.082 |
| 60 | US$18.150 | US$21.569 | US$23.210 |
| 120 | US$36.150 | US$51.611 | US$60.348 |

Fíjate en la fila de 120 meses: la diferencia entre 7% y 10% (~US$9k) es menor que lo que suman tus propios aportes. **En los primeros 5 años manda tu tasa de ahorro, no tu rentabilidad.**

**Comparación de caminos (para tu capital y tu situación):**

| Camino | Exceso esperado vs. ETF tras costos e impuestos | Riesgo/complejidad | Veredicto |
|---|---|---|---|
| **Pasivo: ETF global UCITS de acumulación** | 0 (es el benchmark) | Mínima | **Núcleo: 90–100%** |
| Rotación de ETFs / trend following | ~0 o negativo en CAGR. El beneficio histórico es menor drawdown, no más retorno (GEM: protección en caídas) [V] | Baja | Te sirve poco: a los 22 años con aportes mensuales, **las caídas te favorecen** porque compras barato. Además realiza ganancias antes de 2 años. Sirve como **banco de pruebas del motor**, no como inversión. |
| Swing técnico en acciones individuales | Probablemente negativo para minoristas tras costos (evidencia de day/swing traders en Brasil y Taiwán: la gran mayoría pierde) [V] | Alta | No, salvo como experimento en paper. |
| PEAD + LLM | Incierto: Martineau (2022) dice que en acciones grandes no existe desde 2006; dos papers de 2025 dicen que sigue vivo; Subrahmanyam (UCLA) lo atribuye a decisiones de diseño [V] | Media | Candidato de investigación, no de inversión todavía. |
| Compras oportunistas de insiders (Form 4) + LLM | Evidencia académica fuerte (82 pb/mes ponderado por valor, muestra 1986–2007), posible decaimiento posterior [V] | Media | **Mi candidato principal de investigación** (ver §2.4). |
| *Odd-lot tender offers* | Pequeño pero real, de baja frecuencia | Baja-media | **Encaja muy bien con capital pequeño** (ver §2.9). |
| Opciones, futuros, apalancados | Negativo esperado para tu nivel y capital (un micro-futuro MES tiene nocional de ~US$30k) | Muy alta | **No por ahora.** |
| CDT en COP | 12–14% EA nominal en COP, con inflación ~6% [V] | Baja (FOGAFIN hasta el límite asegurado) | Para fondo de emergencia y metas de menos de 3 años, **no** para el largo plazo. |

**Núcleo concreto [C: validar con contador]:**

- **ETF UCITS irlandés de acumulación en USD** que cotice en LSE. Ejemplos de la familia "All-World/ACWI": VWRA, FWRA, SSAC, con TER de ~0,15–0,22%. Si quieres solo EE. UU., CSPX (~0,07%).
- **Ventajas frente a VT/VOO para un colombiano:**
  - El fondo paga 15% de retención sobre dividendos de EE. UU. en lugar del 30% que pagarías tú (no hay tratado EE. UU.–Colombia) [V].
  - No es un activo "US-situs", así que evitas el impuesto de sucesiones de EE. UU. por encima de US$60k [V].
  - La versión de acumulación no reparte dividendos, así que no tienes ingreso anual que declarar por ellos [C].
- **Comisión en LSE (Tiered):** mínimo ~US$1,70 + tasas, unos ~US$1,9 por compra [V]. Eso es 0,64% sobre US$300 y 0,21% sobre US$900. **Compra cada 2–3 meses** (o junta los aportes en COP en un CDT o cuenta de alto rendimiento y envía el dinero trimestralmente).
- **Mantener más de 2 años** para que aplique ganancia ocasional (15%) en vez de renta ordinaria [V].

### 2.2 (Tu pregunta 8) Plan de ingresos complementario

Esto es lo que más impacto tiene en tu patrimonio, así que va antes que lo técnico.

- **Costo de oportunidad:** 2 h/día son ~730 h/año. Aun a US$5/h son ~US$3.650/año, el **equivalente a un 100% de retorno sobre el capital del año 1**. El mejor edge de trading plausible sobre US$4–8k rinde US$100–400/año [E].
- **Opciones, de mayor a menor retorno esperado por hora [E]:**
  1. **Tu carrera.** Prepararte para entrar a una especialización tiene un retorno de varios múltiplos de tu ingreso futuro de por vida. Si esa es tu meta, una parte de esas 2 horas probablemente rinde más ahí que en cualquier otra cosa.
  2. **Trabajo remoto como experto de dominio para IA** (evaluación y anotación de contenido médico para empresas que entrenan modelos). Hay plataformas que pagan a perfiles médicos tarifas en dólares muy superiores al ingreso local por hora. *Incertidumbre:* la disponibilidad, los requisitos (a veces piden título) y las tarifas varían. Verifica la legitimidad de cada plataforma y nunca pagues por "acceder".
  3. **Tutorías a estudiantes de medicina** (ciencias básicas, preparación de exámenes). Hay demanda local inmediata y no requiere inversión.
  4. **Herramientas con IA para consultorios o médicos** (agenda, documentación). Tiene potencial alto, pero hay implicaciones de privacidad de datos de salud (Ley 1581, normas de historia clínica). Es para más adelante.
- **Propuesta de reparto del tiempo [E]:** ~30–45 min/día a esta herramienta (aprendizaje de Python, datos e IA, que también son habilidades valiosas en informática médica) y el resto a lo que más rinda según tus metas. Tú decides.

### 2.3 (Tu pregunta 7) Sesgos y trampas que quizá no estés viendo

**De datos y metodología:**

- **Look-ahead paramétrico del LLM** (ver §0, punto 7). Existen modelos "cronológicamente consistentes" de investigación, como ChronoBERT/ChronoGPT y DatedGPT, precisamente por esto [V].
- **Supervivencia:** IBKR no da históricos de acciones deslistadas, así que **no sirve para backtests de acciones individuales**.
- **Timestamps:** un 8-K aceptado a las 16:05 ET no se puede operar al cierre de ese día. Hay que usar la hora de aceptación de EDGAR, y la ejecución siempre debe ser en la apertura siguiente o después.
- **Fundamentales re-expresados:** usar el dato "tal como se reportó", no el corregido después.
- **Splits, dividendos y benchmark de retorno total** mal alineados.
- **Selección de periodo:** los backtests que empiezan en 2009–2010 son puro mercado alcista.
- **Pruebas múltiples:** si corres 1.000 backtests, esperas ver un Sharpe de ~3,26 por azar [V]. Hay que contar las pruebas.
- **Costos subestimados y fills optimistas en paper.**

**Humanos:**

- **Sesgo narrativo:** el LLM escribe tesis convincentes para movimientos aleatorios.
- **Costo hundido:** después de meses construyendo, querrás que funcione.
- **Overrides discrecionales sin medir.**
- **Confundir aprendizaje con rentabilidad.**

**Los míos, como modelo (tómalos en serio):**

- **Complacencia:** tiendo a validar el entusiasmo del usuario. Me diste permiso para criticar y lo estoy usando, pero sigue desafiándome.
- **Código plausible pero con bugs:** un bug de un día de desfase en un backtest infla resultados de forma muy convincente. Por eso exijo tests de respuesta conocida y de fuga de datos.
- **Datos factuales desactualizados o inventados** (comisiones, normas tributarias). Por eso marco [V]/[E]/[C] y cito fuentes.
- **Tendencia a sobre-ingeniería.** Pídeme siempre la versión más simple.

### 2.4 (Tu pregunta 3) Dónde sí puede haber edge para un minorista con LLMs, y dónde no

**Sí, plausible (no garantizado):**

1. **Nichos con capacidad limitada.** Un fondo de US$1.000 millones no puede ganar US$50 en un *odd-lot tender*. Tú sí.
2. **Amplitud de lectura en acciones pequeñas que nadie cubre.** El LLM lee cientos de 8-K y Form 4 al día por centavos. El límite es la liquidez y los costos de esas acciones.
3. **Compras oportunistas de insiders.** Desde 2023 el Form 4 trae una casilla que indica si la operación se hizo bajo un plan 10b5-1 [E: verificar formato]. Eso facilita separar operaciones "rutinarias" de "oportunistas", la distinción clave de Cohen, Malloy y Pomorski (2012) [V]. Los datos son gratuitos y *point-in-time* (EDGAR).
4. **Horizonte largo y paciencia.** Holdings de meses, que los profesionales evitan por riesgo de carrera.
5. **Edge conductual.** No vender en pánico, no sobre-operar. Tu diario va a medir esto.

**No (no gastes tiempo):** reaccionar a noticias más rápido que los algoritmos (se pierde en milisegundos), pedir al LLM que prediga precios a partir de gráficos o titulares, rupturas técnicas genéricas en acciones grandes y líquidas, PEAD en acciones grandes, comprar opciones o 0DTE, day trading de cripto o forex, y copiar "señales".

### 2.5 (Tu pregunta 4) Gestión de riesgo y sizing

- **Para el satélite (cuando exista):** riesgo fijo de 0,5% por trade (máximo 1%), stop basado en ATR, máximo 5 posiciones y tope por posición del 25% del satélite.
- **Regla de tamaño mínimo:** solo operar si el costo de ida y vuelta es ≤ 0,3%, lo que exige posiciones de unos US$500 o más. Por eso el satélite necesita **≥ ~US$2.500** [E].
- **Kelly fraccional:** úsalo solo como **tope**, nunca como objetivo, y con ≤ ¼ de Kelly. Y solo después de tener más de 200 trades reales o de paper. Con errores de estimación, Kelly completo apuesta de más de forma sistemática.
- **Volatility targeting a nivel de portafolio:** mejoró el Sharpe en algunos estudios (Moreira y Muir 2017), pero la robustez fuera de muestra es discutida (Cederburg et al. 2020). Úsalo para controlar riesgo, no esperes retorno extra de ahí.
- **Correlación:** como máximo 2 posiciones con correlación de 60 días > 0,7, y límite por sector.
- **Filtro de régimen sin hindsight:** una regla simple **pre-registrada** (SPY sobre su media de 200 días o momentum de 12 meses), fijada *antes* del backtest y sin optimizarla.
- **Controles duros:** pérdida máxima diaria/semanal, kill switch, límite de órdenes por minuto, detección de duplicados y reconciliación con el broker al iniciar.

### 2.6 (Tu pregunta 2) Fuentes de datos accesibles

| Necesidad | Gratis | Barato | Comentario |
|---|---|---|---|
| Precios EOD de ETFs | IBKR histórico (vía API), Stooq, yfinance (no oficial) | Tiingo (~US$0–30/mes), EODHD (desde ~€20/mes) [V] | Para ETFs el sesgo de supervivencia es menor. |
| Precios sin sesgo de supervivencia + constituyentes históricos | Listas comunitarias en GitHub (imperfectas) | **Norgate Platinum ~US$630/año** (incluye deslistadas y miembros históricos de índices) [V] | Norgate es el estándar minorista, pero su actualizador corre en **Windows**. No pagar hasta la Fase 2c. |
| Filings (8-K, 10-Q, 10-K, Form 4, SC TO) | **SEC EDGAR**: gratis, máximo 10 solicitudes/segundo, exige User-Agent [V]. Librería `edgartools` | — | La hora de aceptación da *point-in-time* real. El **8-K Item 2.02** marca la hora exacta de publicación de resultados, gratis. |
| Fundamentales point-in-time | EDGAR XBRL (companyfacts / frames) | Sharadar (Nasdaq Data Link) | Hay que usar la fecha de presentación, no la de cierre del periodo. |
| Calendario de earnings | Finnhub (plan gratuito), EDGAR 8-K 2.02 histórico | EODHD Fundamentals | Para el histórico, EDGAR es más confiable que las APIs gratuitas. |
| Noticias | Feeds gratuitos de IBKR, comunicados vía 8-K Ex. 99.1, GDELT | Finnhub o Benzinga (pagos) | Las noticias son el área más ruidosa y con más look-ahead. |
| Macro | FRED | — | — |
| Datos en tiempo real IBKR | Datos diferidos gratis | Paquete de US$10/mes, exonerado si generas ≥ US$30/mes en comisiones (no te pasará) [V] | **Con diseño EOD no lo necesitas.** |

### 2.7 (Tu pregunta 5) Arquitectura recomendada para *vibe coding*

- **Principios:** EOD-first, pocas dependencias, todo reproducible, tests antes que features, el broker es la fuente de verdad y la UI más simple posible.
- **Stack:**
  - Python 3.12 + `uv` (entornos)
  - `ib_async` (sucesor mantenido de ib_insync, compatible al 100%) [V]
  - `pandas`, DuckDB + Parquet (datos y diario en un solo archivo, sin servidor)
  - `pydantic-settings` + `.env` para secretos
  - SDK oficial de Anthropic con salidas estructuradas (JSON schema)
  - Streamlit (UI)
  - `pytest`, `ruff`, `pre-commit` con escáner de secretos y GitHub Actions como CI
  - Logging en JSON
- **Motor de backtest:** uno propio, pequeño, para barras diarias (ejecución en la apertura siguiente, modelo de costos IBKR, slippage). Validado contra un resultado publicado y contrastado una vez con una librería externa. Es más fácil de auditar que un framework grande.
- **Despliegue:** tu computador primero (tarea diaria tras el cierre). VPS solo si alguna vez hace falta, con Docker e IBC, y asumiendo la re-autenticación semanal con 2FA en cuentas reales.
- **Horario:** el mercado de EE. UU. abre de 9:30 a 16:00 ET. En Colombia (UTC-5 todo el año) eso es **8:30–15:00 mientras EE. UU. está en horario de verano** (marzo–noviembre) y **9:30–16:00 el resto del año**.

### 2.8 (Tu pregunta 6) Cómo evaluar si el LLM agrega valor real

1. **Exactitud factual primero.** Usar un set de 30–50 filings revisados por ti, más una **verificación automática de que cada cita textual del LLM aparece literalmente en el documento fuente** (es un detector barato de alucinaciones). Meta: ≥ 90% de afirmaciones correctas.
2. **A/B con placebo**, misma estrategia base, tres brazos:
   - (A) sin LLM
   - (B) con veto o filtro del LLM
   - (C) **veto aleatorio con la misma tasa que B**

   Sin el brazo C no sabes si el LLM ayuda o si simplemente operar menos ayuda.
3. **Solo con eventos posteriores al corte de entrenamiento del modelo** (Haiku 4.5: julio 2025) y luego en forward. Prompt y modelo **congelados y versionados**.
4. **Costo contra mejora:**
   - Un resumen de 8-K con Haiku 4.5 cuesta ~US$0,012. 50 al día suman ~US$12,6/mes, o ~US$6,3 con la Batch API (50% de descuento). Una lectura profunda de un 10-Q con Sonnet 5.5 cuesta ~US$0,12 [E, con precios vigentes de US$1/US$5 y US$2/US$10 por millón de tokens].
   - El LLM "se gana" su costo solo si la mejora en expectativa por trade × número de trades supera el costo mensual.

### 2.9 (Tu pregunta 9) Ideas fuera de la caja (con riesgo explícito)

| Idea | Qué es | Riesgo | Comentario |
|---|---|---|---|
| **Odd-lot tender offers** | En algunas recompras por oferta pública, quien tiene menos de 100 acciones y las ofrece todas queda exento del prorrateo. El LLM lee el SC TO-I y detecta la cláusula, el precio y las condiciones. | Bajo-medio: la oferta se puede retirar (MAC), el precio puede caer si es "Dutch auction" y a veces hay exclusiones por jurisdicción. | Capacidad diminuta y pocos eventos al año, pero **diseñado para capital pequeño**. IBKR permite elegir acciones corporativas voluntarias [C: verificar flujo]. |
| **Screener de insiders oportunistas** | Form 4 + casilla 10b5-1 + clusters de compras + contexto del LLM | Medio | Candidato principal de la Fase 2c. |
| **El diario como guardarraíl conductual** | Medir tus decisiones frente a las reglas | Ninguno | Probablemente el mayor valor real de la herramienta. |
| **Torneo de estrategias en paper** | Varias versiones congeladas en paralelo | Ninguno | Más aprendizaje por mes de calendario. |
| **Efectivo ocioso en IBKR** | Sin intereses en los primeros US$10k [V]. Un ETF de T-bills UCITS (p. ej. IB01) en vez de efectivo | Bajo | Solo si mantienes efectivo esperando entrada. |
| Opciones, futuros, ETFs apalancados | — | Alto | **No por ahora.** Reconsiderar con más de US$25k y experiencia. |

---

## 3. Colombia e IBKR: lo verificado y lo que hay que preguntar al contador

**Verificado (octubre 2026):**

- **IBKR:**
  - Para colombianos solo existe IBKR Pro. Fixed: US$0,005/acción, mínimo US$1, máximo 1%. Tiered: desde US$0,0035/acción, mínimo US$0,35, más tasas.
  - La TWS API no admite fracciones de acciones.
  - No paga intereses sobre los primeros US$10k de efectivo y con NAV menor a US$100k el interés es proporcional.
- **Envío de dinero:** según fuentes secundarias, Bancolombia y Davivienda restringen giros a IBKR desde 2023. Alternativas: Global66 (ACH), DolarApp (~US$3 por ACH), Wise, o bancos como Itaú, Scotiabank, BBVA o Banco de Bogotá. **Objetivo: costo total (spread cambiario + comisión) < 1%** [V parcialmente, verifica tú el costo real con una transferencia pequeña].
- **Impuestos:**
  - UVT 2026 = $52.374.
  - Venta < 2 años → renta ordinaria (tabla del art. 241, de 0% hasta 39%). Venta ≥ 2 años → ganancia ocasional al 15%.
  - Pérdidas en venta de acciones no deducibles (art. 153 E.T.).
  - Declaración de activos en el exterior si al 1 de enero tienes más de 2.000 UVT ($104.748.000 COP, ~US$31.600), aunque no declares renta.
  - Hay que declarar renta si los ingresos brutos superan 1.400 UVT.
- **EE. UU.:**
  - 30% de retención sobre dividendos para residentes colombianos (no hay tratado).
  - Impuesto de sucesiones sobre activos US-situs por encima de US$60k. Los ETF UCITS irlandeses no son US-situs.
- **Macro:**
  - BanRep en 12,25% (septiembre 2026), inflación 6,2% (agosto 2026).
  - CDT al 12–14% EA.
  - TRM ~3.310 COP/USD (1 de octubre de 2026).
  - Salario mínimo 2026 = $1.750.905 COP (~US$529 a esa TRM).

**Matiz que te favorece hoy [E][C]:** la tabla del art. 241 cobra 0% hasta 1.090 UVT de renta líquida gravable. Con ingresos de interno, tu tarifa marginal hoy podría ser 0%, así que el castigo tributario del trading de corto plazo sería bajo *ahora*, pero crecerá cuando ganes como médico. Las pérdidas siguen sin ser deducibles.

**Preguntas concretas para el contador (llévalas impresas):**

1. ¿Las ganancias por venta de ETFs UCITS irlandeses (acciones de un ICAV) se tratan como "acciones" para el art. 153 y para el plazo de 2 años?
2. ¿Un ETF de acumulación genera algún ingreso gravable anual en Colombia antes de vender?
3. ¿El valor bruto de cada venta cuenta como ingreso bruto para el tope de 1.400 UVT?
4. ¿Cómo se calcula el costo fiscal en COP (TRM de compra contra TRM de venta) y cómo tributa la diferencia cambiaria?
5. Régimen cambiario: ¿qué declaración o canalización necesito para inversiones financieras en el exterior hechas por fintechs (Global66, DolarApp)?
6. ¿Cómo afectaría el trading corto mi situación cuando pase a ingresos de médico general?
7. ¿Hay riesgo de que me consideren "comerciante" de valores (acciones como inventario y no como activo fijo) si opero con frecuencia?

---

## 4. Preguntas para ti (necesito estas respuestas antes de construir)

1. **Monto:** ¿US$300 o US$400 al mes? (escribiste ambos). ¿Y **necesitarás parte de este dinero en los próximos 1–3 años** (año rural, especialización, matrícula, mudanza)?
2. **Cuenta:** ¿ya tienes cuenta **real** de IBKR abierta y fondeada? La cuenta paper con datos útiles depende de tener la real. ¿Por qué medio piensas fondearla?
3. **Computador:** ¿Windows, Mac o Linux? (Afecta a Norgate y al manejo de IB Gateway.)
4. **Presupuesto de costos fijos mensuales** (datos, LLM, servidor) que aceptas el año 1. Yo propongo **≤ US$15/mes**.
5. **Núcleo pasivo:** ¿aceptas poner el ~90–100% en un ETF UCITS de acumulación (validado con contador) y usar la herramienta como laboratorio en paper hasta que una estrategia pase los criterios? ¿O quieres desde ya un satélite real pequeño (máximo 10%) para aprender con dinero real, sabiendo que ese dinero probablemente pierda contra el ETF?
6. **Objetivo de la herramienta:** ¿es principalmente aprendizaje o principalmente ingresos? Si es ingresos, ¿estás abierto a dedicar parte del tiempo a las vías de §2.2?
7. **Estrategia de investigación para la Fase 2c:** (a) insiders oportunistas (mi recomendación), (b) PEAD con sorpresa basada en precio + LLM, o (c) solo rotación de ETFs.
8. **Idioma:** asumo UI y resúmenes del LLM en español, con las citas textuales en inglés (idioma del filing). ¿Correcto?

---

## 5. Plan final ajustado

**Principio rector:** cada fase tiene entregables y un **criterio de aprobación**. Si una fase no pasa, se arregla o se archiva. Archivar una estrategia que no funciona es un **resultado válido**, no un fracaso.

### Fase 0: Fundaciones y reglas del juego (semana 1, casi sin código)

**Entregables:**

- Cuenta IBKR real + paper, y ruta de fondeo probada con una transferencia pequeña (costo medido).
- Núcleo pasivo en marcha: primera compra del ETF elegido y calendario de compras cada 2–3 meses.
- `docs/EVALUACION.md`: protocolo de evaluación, plantilla de pre-registro y definición de los regímenes (alcista, bajista, lateral, alta volatilidad) **fijada ex-ante**.
- `docs/COSTOS.md`: modelo de costos (comisiones IBKR, spread y slippage por tipo de activo, costo de fondeo, presupuesto fijo, impuestos estimados).
- Benchmark formal: el ETF UCITS elegido, retorno total en USD y COP, después de sus costos.

**Criterios para declarar viable una estrategia** (sustituyen al "15% anual"):

1. **Reproducible:** mismos datos + config → mismo resultado (hash) y tests en verde.
2. **Motor validado:** la prueba de respuesta conocida pasa.
3. **Fuera de muestra estricto (walk-forward)**, neto de comisiones IBKR, spread y slippage. **Con el doble de costos debe seguir sin perder contra el benchmark.**
4. **Contra el benchmark**, debe cumplir una de dos:
   - (a) CAGR mayor con drawdown máximo no peor, o
   - (b) Sharpe ≥ Sharpe del benchmark + 0,2 y CAGR ≥ CAGR del benchmark − 1 punto.

   Todo después del impuesto colombiano estimado.
5. **Deflated Sharpe:** probabilidad ≥ 0,95 dado el número de pruebas registradas.
6. **Muestra:** ≥ 200 trades fuera de muestra (estrategias de eventos) o ≥ 20 años de datos mensuales (asignación).
7. **Robustez:**
   - Variar los parámetros ±20–30% conserva ≥ 70% del exceso de retorno.
   - Ningún año o acción aporta más del 35% de la ganancia.
8. **Regímenes:** no se destruye en ninguno de los 4 y aporta en al menos 3.
9. **Descuento del 50%:** con la mitad del exceso del backtest, debe seguir ganando al benchmark **después de costos fijos y al capital que realmente usarías**. (Con capital pequeño, este es el criterio que más estrategias descarta.)

**Aprobación:** tú apruebas el protocolo antes de ver cualquier backtest.

### Fase 1: Panel de investigación de solo lectura (semanas 2–6)

**Entregables, en orden:**

- **1a. Esqueleto:**
  - Repo, `uv`, config con `.env` (y `.env.example`), logging, pre-commit con escáner de secretos y CI.
  - Conexión a **IBKR paper** con `ib_async`: chequeo de salud, snapshot de cuenta y posiciones (solo lectura).
- **1b. Datos EOD:**
  - Fuente gratuita primero; almacenamiento en DuckDB/Parquet.
  - Chequeos de calidad: huecos, splits, outliers, precios ≤ 0, fechas duplicadas, desfases de zona horaria.
  - Universo definido: ETFs líquidos + acciones de EE. UU. con volumen y precio mínimos.
- **1c. Screener de reglas fijas, versionado:**
  - Momentum, volumen relativo, rupturas, tendencia, earnings próximos y liquidez mínima.
  - Cada corrida se guarda con hash de config.
- **1d. Diario automático:**
  - ID de señal, snapshot de indicadores, tesis, decisión (aprobar/rechazar + motivo) y resultado.
  - **Seguimiento automático de contrafactuales** de ideas rechazadas o bloqueadas a +5/+10/+20 días, con MAE/MFE.
- **1e. Calculadora de riesgo y tamaño:**
  - % de riesgo, stop por ATR y múltiplos de R.
  - Advertencia automática si el costo de ida y vuelta supera 0,3%.
  - Redondeo a acciones enteras.
- **1f. Módulo LLM:**
  - Descarga de EDGAR (8-K, Form 4) y resumen estructurado con Haiku 4.5 (salida JSON).
  - Campos: hechos con cita textual y sección; interpretación separada; "qué cambió"; nivel de confianza.
  - Verificación automática de citas, registro de costo por llamada y tope diario de gasto.
- **1g. UI en Streamlit:**
  - Lista de ideas, tesis, riesgo y fuentes.
  - Botones de aprobar/rechazar que **solo escriben en el diario** (no envían órdenes).

**Criterio de aprobación:**

- Corre a diario durante 2 semanas sin caídas.
- Chequeos de calidad de datos en verde.
- ≥ 90% de exactitud factual del LLM en el set de evaluación, con 100% de citas verificables.
- Costo dentro del presupuesto.
- Tú puedes usarlo solo (con un README paso a paso).

### Fase 2: Laboratorio de validación (semanas 6–16)

**Entregables:**

- **2a. Motor de backtest:**
  - Barras diarias, ejecución en la apertura siguiente, modelo de costos IBKR y slippage conservador.
  - **Tests de fuga:** cambiar datos futuros no debe cambiar las señales pasadas.
  - **Prueba de respuesta conocida:** replicar la media móvil de 10 meses de Faber sobre SPY.
- **2b. Rotación/tendencia de ETFs:**
  - Evaluada honestamente contra el benchmark.
  - Esperado: menor drawdown y CAGR similar o menor.
  - Decisión documentada.
- **2c. Estrategia candidata de eventos** (insiders oportunistas o PEAD con sorpresa basada en precio):
  - Pre-registrada.
  - Datos sin supervivencia (aquí sí se evalúa pagar Norgate o similar).
  - Walk-forward, regímenes, robustez, Deflated Sharpe y estrés de costos.
- **2d. A/B del LLM con placebo**, solo con eventos posteriores a julio de 2025.
- **Registro de experimentos:** todas las pruebas, incluidas las fallidas.

**Criterio de aprobación:** pasar los 9 criterios de la Fase 0. Si no se pasan, la estrategia se archiva con su informe.

### Fase 3: Forward test en paper y ejecución asistida (mínimo 6 meses)

**Entregables:**

- **Gestor de órdenes en paper con todos los controles duros:**
  - Pérdida diaria/semanal máxima, tamaño máximo por posición, número máximo de posiciones, límite de órdenes por minuto.
  - Detección de duplicados y kill switch.
  - Reconciliación al arrancar (el broker manda y las anomalías bloquean).
- **Flujo "propone → apruebas con un clic → orden a paper".**
- **Versión v1.0 congelada** y retadores corriendo en paralelo.
- **Reporte mensual:** slippage real contra el modelo, tasa de fills, rechazos, incidentes y deriva respecto al backtest.

**Criterio de aprobación:**

- ≥ 6 meses y ≥ 50 trades (ideal 100).
- Resultados dentro del intervalo de confianza del 90% del backtest (bootstrap de trades).
- Slippage ≤ 1,5× el modelo.
- 0 incidentes críticos en los últimos 30 días.
- Reconciliación al 100%.

### Fase 4: Dinero real pequeño (solo si todo lo anterior pasa)

**Condiciones de promoción:**

- Criterios de la Fase 3 cumplidos.
- Satélite ≥ US$2.500 y ≤ 10–20% del portafolio.
- **Tu autorización escrita.**
- **Aprobación humana de cada orden** (regla no negociable 1).

**Degradación automática a paper si ocurre cualquiera de estas:**

- Drawdown del satélite > 15%.
- Expectativa de los últimos 30 trades < 0 con 90% de confianza.
- 2 incidentes críticos.

**Infraestructura:** sigue en tu computador. VPS solo si una estrategia aprobada lo requiere de verdad.

---

## 6. Estructura de repo propuesta (se crea solo después de tu OK)

```
tradingtool/
├── pyproject.toml            # uv; dependencias fijadas
├── .env.example              # nombres de variables, nunca valores
├── .gitignore                # .env, data/, logs/, *.duckdb
├── README.md                 # instalación y uso paso a paso, en español
├── config/
│   ├── universe.yaml
│   ├── screener.yaml         # reglas versionadas
│   └── risk.yaml             # límites duros
├── src/tradingtool/
│   ├── settings.py           # pydantic-settings (lee .env)
│   ├── broker/               # conexión ib_async, snapshot, reconciliación (órdenes en Fase 3)
│   ├── data/                 # fuentes, almacenamiento, calidad, universo
│   ├── signals/              # indicadores y screener
│   ├── risk/                 # sizing y límites
│   ├── llm/                  # EDGAR, resúmenes, esquemas, verificación de citas, costos
│   ├── journal/              # diario y contrafactuales
│   ├── backtest/             # motor, costos, métricas, walk-forward (Fase 2)
│   └── ui/                   # app Streamlit
├── tests/                    # incluye tests de fuga y de respuesta conocida
├── docs/                     # este plan, protocolo de evaluación, costos, decisiones
└── data/ logs/               # ignorados por git
```

**Primer entregable tras tu OK:** Fase 1a + 1b + 1d mínimos. Es decir, conexión a paper (solo lectura), descarga y validación de datos EOD de un universo pequeño, screener con 2–3 reglas y diario en DuckDB, con instrucciones para correrlo en tu computador.

---

## Fuentes (consultadas el 2026-10-07)

**IBKR:**
- [Commissions & Fees](https://investors.interactivebrokers.com/en/pricing/commissions-home.php)
- [Market Data Pricing](https://www.interactivebrokers.com/en/pricing/research-news-marketdata.php)
- [Interest Rates](https://www.interactivebrokers.com/en/accounts/fees/pricing-interest-rates.php)
- [Fractional shares (IBKR Campus)](https://www.interactivebrokers.com/campus/trading-lessons/fractional-shares/)
- [Tiered vs. Fixed en LSE (Guy Rutenberg)](https://www.guyrutenberg.com/2025/01/04/tiered-vs-fixed-commissions-in-ibkr/)
- [IBKR en Colombia: guía 2026 (FinanzasPlus)](https://finanzasplus.co/blog/interactive-brokers-colombia-guia-completa)
- [Cómo abrir cuenta IBKR desde Colombia (FinanzasPlus)](https://finanzasplus.co/blog/como-abrir-cuenta-interactive-brokers-colombia)
- [Global66: fondea tu broker](https://www.global66.com/co/fondea-tu-broker/)

**Impuestos Colombia:**
- [Tributación de acciones del exterior (Gerencie)](https://www.gerencie.com/impuestos-acciones/tributacion-de-las-acciones-del-exterior-en-colombia)
- [Impuestos a inversiones (Rankia)](https://www.rankia.co/blog/analisis-colcap/6466975-impuestos-inversiones-colombia)
- [Pérdida en venta de acciones no deducible (Gerencie)](https://www.gerencie.com/perdidas-en-la-venta-de-acciones-no-es-deducible.html)
- [Art. 153 E.T. (Actualícese)](https://actualicese.com/estatutotributario/153-2/)
- [DIAN, Oficio 5272 / Concepto 978 de 2023](https://normograma.dian.gov.co/dian/compilacion/docs/oficio_dian_5272_2023.htm)
- [Obligados a declarar activos en el exterior (Actualícese)](https://actualicese.com/personas-naturales-obligadas-a-declarar-activos-en-el-exterior/)

**EE. UU. y UCITS:**
- [UCITS ETF: withholding y estate tax (TaxesForExpats)](https://www.taxesforexpats.com/articles/investments/ucits-etf-withholding-tax.html)

**Macro Colombia:**
- [BBVA Research: BanRep, septiembre 2026](https://www.bbvaresearch.com/publicaciones/colombia-banrep-incremento-la-tasa-de-politica-monetaria-en-septiembre/)
- [CDT 2026 (Valora Analitik)](https://www.valoraanalitik.com/cdt-colombia-buena-rentabilidad-ano-2026/)
- [TRM 1 de octubre de 2026](https://www.dolar-colombia.com/2026-10-01)
- [Salario mínimo 2026](https://siemprealdia.co/colombia/derecho-laboral/salario-minimo-2026/)

**Evidencia académica:**
- [Martineau, Rest in Peace PEAD](https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf)
- [UCLA Anderson Review: ¿PEAD otra vez?](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/)
- [Cohen, Malloy y Pomorski, Decoding Inside Information](https://www.nber.org/papers/w16454)
- [Deflated Sharpe Ratio](https://papers.ssrn.com/abstract=2460551)
- [GEM, perspectiva fuera de muestra](https://www.advisorperspectives.com/articles/2019/05/06/global-equity-momentum-a-craftsmans-perspective)
- [SPIVA](https://www.spglobal.com/spdji/en/spiva/)
- Chague, De-Losso y Giovannetti, *Day Trading for a Living?* (Brasil)
- Barber, Lee, Liu y Odean (Taiwán)
- Moreira y Muir (2017)
- Cederburg et al. (2020)

**LLM y look-ahead:**
- [DatedGPT](https://arxiv.org/abs/2603.11838)
- [Chronologically Consistent LLMs](https://arxiv.org/html/2502.21206v2)
- [Claude models overview (cortes de entrenamiento y precios)](https://platform.claude.com/docs/en/models/overview)

**Datos y herramientas:**
- [Norgate](https://norgatedata.com/stockmarketpackages.php)
- [Comparativa de APIs 2026 (EODHD, Tiingo)](https://www.lambdafin.com/articles/financial-data-api-2026)
- [SEC: Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data)
- [ib_async](https://pypi.org/project/ib_async)
- [Ejemplo de odd-lot tender (J&J/Kenvue)](https://mymoneyblog.com/johnson-and-johnson-kenvue-odd-lot-arbitrage-deal.html)
