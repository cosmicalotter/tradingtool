# Costos: de la herramienta y de invertir

Objetivo: **US$0 al mes** en la herramienta mientras se investiga. Cada costo puntual se decide con tu aprobación.

## 1. Costos de la herramienta

| Componente | Opción elegida | Costo |
|---|---|---|
| Datos de insiders (Form 4) | SEC EDGAR + datasets trimestrales | **Gratis** (máx. 10 solicitudes/s, exige nombre y correo) |
| Precios diarios | Plan gratuito de Massive (antes Polygon): todo el mercado por día, una llamada | **Gratis**. Límite de llamadas por minuto e historia de ~2 años (verificar con tu clave) |
| Precios alternativos | Tiingo (gratis con límites) o históricos de IBKR | Gratis |
| Datos en tiempo real de IBKR | **No hace falta** (la estrategia usa datos de cierre) | US$0 |
| LLM | **No se usa en v1** (reglas fijas). Si se agrega: Gemini con tus créditos de Vertex, o un modelo local | US$0 |
| Servidor/VPS | No hace falta: corre en tu computador ~10 min al día | US$0 |
| CI (tests automáticos) | GitHub Actions | Gratis |
| **Opcional, una vez** | Un mes de historia larga de precios con acciones deslistadas (ver abajo) | ~US$20–39 una vez, solo si lo apruebas |

### Opciones para validar con historia larga (2009–2025, incluyendo deslistadas)

No existe una fuente gratuita y descargable que cubra eso. Estas son las alternativas (precios vistos en octubre de 2026; verifícalos antes de pagar):

| Opción | Costo | Ventaja | Desventaja |
|---|---|---|---|
| **Alpaca** (plan gratuito "Basic") | **US$0** | Historia desde 2016, ~200 llamadas/min; integrada (`TT_PRICE_SOURCE=alpaca`) | No llega a 2009 y cubre solo en parte las deslistadas (algo de sesgo de supervivencia) |
| **EODHD** "EOD Historical Data – All World" | **US$19,99** un mes | La más barata; ya está integrada en la herramienta (`TT_PRICE_SOURCE=eodhd`) | Tickers reutilizados vienen con sufijo `_old`; revisa la licencia antes de guardar datos |
| Sharadar SEP (historia completa) | ~US$39 un mes | Mejor calidad: ID permanente por empresa y razones de deslistado | La licencia exige borrar los datos crudos 30 días después de cancelar; requiere integrar un adaptador nuevo |
| Massive Advanced | US$199 un mes | Un solo proveedor | Caro; datos sin ajustar |
| QuantConnect (nube gratuita) | US$0 | Datos sin sesgo de supervivencia desde 1998 | No se pueden exportar: habría que reescribir el estudio allá |

Los planes Starter (US$29, 5 años) y Developer (US$79, 10 años) de Massive **no** llegan a 2009.

## 2. Costos de invertir

### Comisiones de operación (IBKR Pro, modelo en `config/costs.yaml`)

| Concepto | Valor |
|---|---|
| Acciones/ETF de EE. UU. (Tiered) | US$0,0035/acción, mínimo **US$0,35** por orden, más tasas de bolsa |
| ETF UCITS en Londres (Tiered) | Mínimo **~US$1,70** + tasas ≈ US$1,9 por compra |
| Venta (EE. UU.) | Además, tarifa SEC y FINRA TAF (centavos) |
| Spread + deslizamiento (supuesto conservador) | 50 / 25 / 10 / 5 pb por lado, según la liquidez de la acción |

### Costos de enviar dinero desde Colombia

ARQ cobra **US$3 por depósito**, sin importar el monto. Falta verificar el **spread** entre la tasa COP→USD que te da ARQ y la TRM del día: compara ambas en tu próximo envío. El costo real es la suma de las dos cosas.

### Cuánto pesan los costos según cada cuánto compras el ETF

Supuesto: US$400 al mes, solo costos fijos (US$3 de ARQ + ~US$1,9 de comisión por compra):

| Frecuencia | Monto por compra | Costo por compra | % |
|---|---|---|---|
| Mensual | US$400 | US$4,9 | **1,23%** |
| Cada 2 meses | US$800 | US$4,9 | **0,61%** |
| Trimestral | US$1.200 | US$4,9 | **0,41%** |

**Recomendación:** juntar los aportes en COP (en una cuenta de alto rendimiento líquida) y enviar y comprar **cada 2 o 3 meses**.

### Por qué el satélite activo necesita más capital

Una posición de US$100 cuesta ~0,8% ida y vuelta solo en mínimos y spread; una de US$1.000 cuesta ~0,2% (ver `tt riesgo`). Por eso la calculadora rechaza operaciones cuyo costo ida y vuelta supere 0,30%. Con capital pequeño, la mayoría de las ideas no pasarán ese filtro, y está bien que así sea.

## 3. Impuestos (resumen; confirmar con un contador)

- **Venta antes de 2 años:** la ganancia es renta ordinaria, con tarifa progresiva. **Después de 2 años:** ganancia ocasional al 15%.
- **Las pérdidas en venta de acciones no son deducibles** (art. 153 del Estatuto Tributario).
- El **valor bruto** de las ventas puede contar para el tope de ingresos que obliga a declarar renta.

**Conclusión:** el trading activo tiene un costo fiscal oculto frente a comprar y mantener. Las preguntas para el contador están en `docs/00-critica-y-plan.md` §3.
