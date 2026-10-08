# Pre-registro: estrategia `rotacion-v1` (rotación de ETFs por momentum)

**Congelado el:** 2026-10-08, **antes** de descargar un solo precio para esta estrategia. Ninguna regla se ajustó mirando resultados, porque no existía ningún resultado.
**Configuración:** `config/etf.yaml`. Su huella (*hash* de reglas) aparece en cada informe de `tt etf-backtest`.

> **Regla de oro** (la misma de `insider-v1`): si se cambia una regla después de ver resultados, ya no es `rotacion-v1`. Se crea `rotacion-v2` y se evalúa con datos que no se usaron para diseñarla.

---

## 1. La idea, en palabras simples

Una vez al mes se mira qué "cajón" del mundo viene subiendo más:
- acciones de EE. UU.,
- acciones de otros países desarrollados,
- acciones de países emergentes.

Se invierte en el que va mejor, **pero solo si le está ganando al efectivo** (letras del Tesoro de EE. UU.). Si ninguno le gana al efectivo, es señal de que las acciones están débiles: la cartera se refugia en lo defensivo, bonos del Tesoro o efectivo, el que venga mejor.

A esto se le llama **momentum dual**:
- **Momentum relativo:** elegir el mejor entre varios.
- **Momentum absoluto, o "tendencia":** invertir solo si sube más que el efectivo.

**Evidencia a favor:**
- **La tendencia** (momentum en el tiempo) está documentada en más de 100 años y en decenas de mercados (Moskowitz, Ooi y Pedersen, 2012; Hurst, Ooi y Pedersen, 2017). Su gran aporte histórico fue **reducir las caídas largas**, como 2000–2002 y 2008.
- **El momentum entre clases de activos** aparece en muchos mercados (Asness, Moskowitz y Pedersen, 2013).
- Hay versiones simples para inversionistas comunes: Faber (2007) y Antonacci (2014).

**Evidencia en contra (igual de importante):**
- **Después de publicarse**, estas estrategias rindieron menos que comprar y mantener el S&P 500. Influye que 2010–2024 fue un mercado alcista excepcional para EE. UU.
- **Con señales mensuales, una caída muy rápida** (febrero–marzo de 2020) llega antes de que la estrategia reaccione, y al rebote se entra tarde (el efecto "serrucho").
- **Cada cambio cuesta comisiones**, y con cuentas pequeñas el mínimo por orden pesa mucho.
- **Vender realiza ganancias**, que pueden generar impuestos en Colombia (consúltalo con un contador). Comprar y mantener los difiere.

**Expectativa honesta:** lo más probable es que `rotacion-v1` rinda algo menos que SPY en los años buenos y caiga bastante menos en las crisis largas. Su objetivo realista no es "ganarle mucho" a SPY, sino un mejor balance entre riesgo y retorno: llegar a un lugar parecido con menos sustos.

## 2. Reglas exactas de `rotacion-v1`

| Regla | Valor | Motivo |
|---|---|---|
| Activos de riesgo | SPY (acciones de EE. UU.), EFA (desarrollados fuera de EE. UU.), EEM (emergentes) | Las tres grandes regiones de acciones del mundo. Se eligieron por amplitud, no por rendimiento pasado |
| Activos defensivos | IEF (bonos del Tesoro de 7–10 años) y efectivo (letras del Tesoro a 3 meses) | Los bonos protegieron en crisis de tasas a la baja (2008, 2020); el efectivo, en crisis con alza de tasas (2022) |
| Señal | Retorno total (con dividendos) a 1, 3, 6 y 12 meses, al cierre del último día hábil del mes | Promediar 4 horizontes evita depender de uno "afortunado", que es la principal fuente de sobreajuste |
| Regla de cada horizonte | Se toma el mejor activo de riesgo si su retorno supera al del efectivo. Si no, el mejor defensivo (IEF o efectivo) | Momentum dual |
| Pesos | Cada horizonte decide el 25% de la cartera | Ej.: tres horizontes eligen SPY y uno IEF → 75% SPY y 25% IEF |
| Ejecución | Al cierre del **primer día hábil del mes siguiente** (T+1) | Sin mirar al futuro: la señal solo se conoce tras el cierre de fin de mes |
| Banda de no operación | No se toca un activo si su peso actual está a menos de 5 puntos del objetivo. Las entradas y salidas completas siempre se hacen | Menos órdenes y menos comisiones |
| Costos | Comisión de IBKR en Londres: el mayor entre US$1,90 y 0,05% de cada orden. Más 10 pb por lado de spread y deslizamiento. Cuenta supuesta de US$5.000 | Conservador. Se reporta la sensibilidad con cuentas de US$1.000 y US$20.000 |

## 3. Comparadores

- **Comprar y mantener SPY.** Es lo que harías con el ETF núcleo del S&P 500, y es el comparador principal.
- **60/40:** 60% SPY y 40% IEF, rebalanceado cada mes con la misma banda. Es el comparador con un riesgo parecido.

## 4. Estrategias de la literatura (solo referencia: NO deciden el veredicto)

Se replican con sus parámetros originales, para dar contexto:

- **GEM** (Antonacci, 2014): mira los últimos 12 meses. Si SPY rinde más que el efectivo, compra el mejor entre SPY y EFA; si no, compra bonos agregados (AGG).
- **Tendencia SMA10 sobre SPY** (Faber, 2007): SPY si su cierre mensual está por encima del promedio de los últimos 10 cierres mensuales; si no, efectivo.
- **GTAA5** (Faber, 2007): 20% en cada uno de SPY, EFA, IEF, VNQ (inmobiliario) y DBC (materias primas). Cada uno se mantiene solo si está por encima de su promedio de 10 meses; si no, ese 20% pasa a efectivo.

## 5. Datos

- **ETF de EE. UU. como sustitutos,** porque tienen historia desde 2002–2006:
  - Precios ajustados por dividendos (retorno total) de **Tiingo**, gratis con registro.
  - Cada descarga trae **la historia completa**, porque cada dividendo cambia los precios ajustados del pasado. Pegar trozos descargados en fechas distintas sesgaría los retornos.
- **Efectivo:** tasa de las letras del Tesoro a 3 meses de **FRED** (serie DTB3, Reserva Federal de St. Louis), gratis y sin clave.
  - Se acumula día a día y se le resta 0,07% anual, el costo de un ETF de letras como IB01.
- **Diferencias con los ETF UCITS reales:**
  - Los UCITS irlandeses pagan una retención del 15% sobre los dividendos de EE. UU. dentro del fondo, unos −0,2% al año en el S&P 500.
  - Además tienen su propio error de seguimiento.
  - Esto afecta casi por igual a la estrategia y a comprar y mantener, así que no cambia la comparación.

## 6. Periodos

| Periodo | Fechas | Uso |
|---|---|---|
| Diseño | Desde el inicio de los datos (~dic. 2006) hasta 2014 | Contexto y criterio de consistencia. Incluye la crisis de 2008 |
| **Validación** | **2015-01-01 – 2024-12-31** | **Veredicto principal**: 10 años, después de publicadas GEM y SMA10 |
| **Reserva** | **desde 2025-01-01** | **Bloqueada.** Se abre una sola vez, al final, con `tt etf-backtest --periodo reserva --abrir-reserva`, y la apertura queda registrada |
| Día a día | desde 2026-10 | Cada recomendación mensual (`tt etf-senal`) queda guardada y no se puede reescribir |

El inicio efectivo lo fija el activo con la historia más corta (DBC, febrero de 2006, más 10 meses de promedio): todas las estrategias arrancan el mismo día.

## 7. Criterio pre-registrado (veredicto)

Se evalúa sobre la **validación** (2015–2024), con costos. `rotacion-v1` **PASA** solo si cumple **todo**:

1. **Mejor riesgo/retorno que SPY:** su índice de Sharpe es mayor o igual que el de SPY. El Sharpe es el retorno por encima del efectivo dividido por la volatilidad (mensual, anualizado).
2. **Caídas mucho menores:** su peor caída (desde un máximo, con datos diarios) es como mucho el **60%** de la peor caída de SPY.
3. **Rinde al menos como el 60/40:** su rendimiento anual compuesto es mayor o igual que el del 60/40.
4. **Es robusta:** al menos **4 de 6 variantes vecinas** cumplen los criterios 1 y 2. Las variantes son:
   - horizontes 3-6-12;
   - horizontes 1-3-6;
   - horizontes 1-3-6-9-12;
   - ejecutar 5 días hábiles después;
   - sin emergentes;
   - refugio solo en efectivo.
5. **Es consistente:** los criterios 1 y 2 también se cumplen en el periodo de diseño.

Además, se exigen **al menos 96 meses** de validación con datos. Si no, el veredicto es **INSUFICIENTE**.

**PASA no significa "probado".** Con 10 años de datos la incertidumbre es grande: el informe la muestra con intervalos bootstrap (por bloques de 6 meses, 2.000 repeticiones). PASA significa que vale la pena seguirla en papel; nada más.

## 8. Lo que NO se hará

- **No se elegirá la estrategia de la literatura que salga mejor** para llamarla "la buena". Eso sería seleccionar mirando resultados.
- **No se agregarán activos que ya sabemos que subieron mucho** (oro, Nasdaq, bitcoin) después de verlo.
- **No se ajustarán horizontes, banda ni activos** después de ver el veredicto.

## 9. Implementación con ETF UCITS (cuenta IBKR)

| Sustituto en el backtest | ETF UCITS sugerido (Bolsa de Londres, en USD, de acumulación) |
|---|---|
| SPY | **VUAA** (Vanguard S&P 500). Alternativa: CSPX, con un precio por acción más alto |
| EFA | **EXUS** (Xtrackers MSCI World ex USA) |
| EEM | **EIMI** (iShares Core MSCI EM IMI) |
| IEF | **CBU0** (iShares $ Treasury Bond 7-10yr) |
| Efectivo | **IB01** (iShares $ Treasury Bond 0-1yr) |

**Verifica en IBKR cada símbolo, la bolsa (LSE) y la moneda (USD) antes de operar.**

Consejos prácticos:
- **Usa el aporte mensual para comprar lo que falta**, en vez de vender y volver a comprar. Así reduces órdenes, comisiones e impuestos.
- **IBKR no permite fracciones de ETF UCITS.** Con cuentas pequeñas, los pesos serán aproximados.
- **El dinero que vas a necesitar pronto** (por ejemplo, para una mudanza en 10 meses) no debería estar en ninguna estrategia con acciones. Para eso existen fondos como IB01.

## 10. Qué pasa después del veredicto

- **PASA:**
  1. Se abre la reserva **una vez**.
  2. Si se sostiene, viene el seguimiento en papel de 6 a 12 meses: `tt etf-senal` guarda cada recomendación mensual.
  3. El dinero real solo entra con tu **autorización escrita**, y cada orden con tu aprobación.
- **NO PASA:** se archiva en `docs/ESTRATEGIAS.md`. El plan base sigue siendo el ETF núcleo con aportes mensuales, que es lo que la evidencia respalda para la mayoría de las personas.
- **INSUFICIENTE:** faltan datos. Revisa `tt etf-precios`; con Alpaca la historia empieza en 2016 y no alcanza.
