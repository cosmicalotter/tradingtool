# tradingtool: panel de apoyo a decisiones

Herramienta personal de **investigación**. Prueba estrategias con reglas fijas, congeladas antes de ver resultados, y mide con honestidad si alguna le gana a comprar y mantener un ETF.

| Estrategia | Estado |
|---|---|
| `rotacion-v1`: rotación mensual de ETFs por momentum | **En evaluación** (sección 2) |
| `insider-v1`: compras de insiders (Form 4 de la SEC) | **Archivada**: no pasó la validación 2019–2025 (`docs/ESTRATEGIAS.md`). Sus señales siguen como seguimiento |

> **Seguridad (reglas no negociables)**
> - **No envía órdenes.** "Aprobar" solo lo escribe en tu diario.
> - La conexión a IBKR es de **solo lectura** y rechaza cuentas reales por defecto (solo acepta paper, que empiezan por `DU`).
> - Tus claves viven en `.env`, que **nunca** se sube a git.
> - Nada de esto es asesoría financiera ni promete rentabilidad.

Documentos clave:

- [`docs/ETF-ROTACION.md`](docs/ETF-ROTACION.md): pre-registro de la rotación de ETFs (reglas y criterio de éxito).
- [`docs/ESTRATEGIAS.md`](docs/ESTRATEGIAS.md): registro de estrategias con sus veredictos.
- [`docs/00-critica-y-plan.md`](docs/00-critica-y-plan.md): crítica, investigación y plan por fases.
- [`docs/EVALUACION.md`](docs/EVALUACION.md): pre-registro de `insider-v1` (archivada).
- [`docs/COSTOS.md`](docs/COSTOS.md): costos de la herramienta y de invertir.

---

## 1. Instalación (CachyOS / Fedora)

```bash
# 1. Instala uv (gestor de Python). Elige UNA opción:
sudo pacman -S uv          # CachyOS / Arch
sudo dnf install uv        # Fedora
# o en cualquier Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Descarga el proyecto e instala dependencias (uv baja Python 3.13 si hace falta)
git clone https://github.com/cosmicalotter/tradingtool.git
cd tradingtool
uv sync

# 3. Crea la base local y tu archivo .env
uv run tt iniciar
```

Abre `.env` con tu editor y completa:

| Variable | Qué poner |
|---|---|
| `TT_SEC_USER_AGENT` | Tu nombre y correo, p. ej. `"Juan Perez juan@correo.com"`. La SEC lo exige. |
| `TT_TIINGO_API_KEY` | Clave gratuita de [tiingo.com](https://www.tiingo.com), para los ETFs de la rotación |
| `TT_MASSIVE_API_KEY` | Clave gratuita de [massive.com](https://massive.com) (antes Polygon.io), para los precios de insiders |

Luego verifica que todo esté bien:

```bash
uv run tt revisar
```

## 2. Rotación de ETFs (`rotacion-v1`)

Una vez al mes elige entre acciones de EE. UU., de otros países desarrollados y emergentes, **solo si le ganan al efectivo**. Si no, se refugia en bonos del Tesoro o en efectivo. Reglas, evidencia a favor y en contra, y criterio de éxito en [`docs/ETF-ROTACION.md`](docs/ETF-ROTACION.md).

**Paso 1 (una vez): clave gratuita de Tiingo.**
1. Crea una cuenta en [tiingo.com](https://www.tiingo.com).
2. Abre el menú de tu cuenta → **API** y copia tu **Token**.
3. Pon en `.env`, sin comillas ni espacios: `TT_TIINGO_API_KEY=tu_token`.

La tasa del Tesoro sale de FRED (Reserva Federal), gratis y sin clave.

**Paso 2: datos y backtest pre-registrado.**

```bash
uv run tt etf-precios     # historia completa de 7 ETFs + tasa del Tesoro (~1 minuto)
uv run tt etf-backtest    # validación 2015–2024 con costos: tabla, robustez y VEREDICTO
```

El veredicto es **PASA**, **NO PASA** o **INSUFICIENTE**, según los 5 criterios fijados en `docs/ETF-ROTACION.md` §7. Otros periodos (solo informativos): `--periodo diseno` o `--periodo todo`. La reserva (desde 2025) está bloqueada: se abre **una sola vez**, al final, con `--periodo reserva --abrir-reserva`.

**Paso 3: la señal de cada mes** (primer día hábil del mes):

```bash
uv run tt etf-senal --capital 1500   # cartera objetivo con montos para tus US$1.500
```

- Muestra qué eligió cada horizonte y por qué, y la cartera objetivo con los ETF UCITS sugeridos (VUAA, EXUS, EIMI, CBU0, IB01).
- Lo compara con el mes anterior.
- La recomendación queda guardada y **no se puede reescribir**: es el seguimiento en papel honesto.
- **No envía órdenes.** El panel (`uv run tt panel`, pestaña "Rotación ETF") muestra lo mismo con gráficos.

## 3. Insiders (archivada): primera carga de datos

```bash
# Historia de Form 4. Hacen falta ≥3 años antes del periodo a evaluar para clasificar a
# los insiders como rutinarios u oportunistas. Son ~10-17 MB por trimestre.
uv run tt sec-historico --desde 2021Q1 --hasta 2026Q2

# Precios diarios de todo el mercado (una llamada por día, ~13 s entre llamadas por el
# límite del plan gratuito). ~500 días tardan ~2 horas. Déjalo corriendo.
uv run tt precios --desde 2024-10-15
```

## 4. Insiders: uso diario (solo seguimiento)

```bash
uv run tt diario     # Form 4 de ayer → precios → screener → resultados
uv run tt panel      # abre el panel en http://127.0.0.1:8501
```

En el panel revisas cada idea: por qué pasó, quién compró, la gráfica de precio, el tamaño de posición sugerido y el costo. Luego decides **Aprobar**, **Rechazar** (con motivo) u **Omitir**. Todo queda en el diario. Las ideas bloqueadas y las rechazadas también se siguen, para medir si tus filtros y tu criterio agregan valor.

**Mejor momento:** en la mañana antes de las 8:30 (hora de Colombia), cuando ya está publicado el índice de la SEC del día anterior, o en la noche.

Para que corra solo de lunes a viernes a las 7:30, escribe `crontab -e` y agrega esta línea (ajusta la ruta de `uv` con `which uv`):

```
30 7 * * 1-5 cd $HOME/tradingtool && $HOME/.local/bin/uv run tt diario >> data/logs/diario.log 2>&1
```

## 5. Insiders: validación histórica (ya hecha: NO PASA)

```bash
uv run tt historico --desde 2024-10-01 --hasta 2025-09-30   # reconstruye señales pasadas
uv run tt precios --desde 2024-10-01                        # asegura precios
uv run tt resultados                                        # mide qué pasó después
uv run tt evaluar --origen backtest                         # veredicto pre-registrado
```

El periodo de **reserva** (desde 2025-10-01) está bloqueado: se abre una sola vez, al final, con `--abrir-reserva`, y la apertura queda registrada. Con solo ~12 meses de historia gratuita, lo esperable es el veredicto **INSUFICIENTE**.

**Gratis, historia 2016–hoy: Alpaca.** Crea una cuenta gratuita en alpaca.markets (basta la *paper*), genera las claves en *API Keys* y ponlas en `.env` (sin comillas, sin espacios alrededor del `=`):

```
TT_ALPACA_KEY_ID=...
TT_ALPACA_SECRET_KEY=...
```

Usa una carpeta de datos aparte para no mezclar precios de dos proveedores. Con fuentes "por ticker" (Alpaca, EODHD, Tiingo) los **precios van antes** de `historico`, porque el filtro de liquidez los necesita:

```bash
export TT_DATA_DIR=data_alpaca TT_PRICE_SOURCE=alpaca
uv run tt iniciar
uv run tt sec-historico --desde 2013Q1 --hasta 2026Q1
uv run tt precios --desde 2016-01-01 --hasta 2026-01-31    # solo tickers con compras de insiders
uv run tt historico --desde 2016-04-01 --hasta 2025-09-30
uv run tt resultados
uv run tt evaluar --origen backtest --periodo validacion
unset TT_DATA_DIR TT_PRICE_SOURCE                          # volver a la base diaria
```

Limitación honesta: Alpaca cubre solo en parte las acciones deslistadas, así que el resultado puede salir algo optimista (sesgo de supervivencia). Para 2009–2025 con deslistadas completas, la opción más barata es pagar **un mes de EODHD (US$19,99)** con el mismo orden de comandos (`TT_PRICE_SOURCE=eodhd`, `TT_EODHD_API_KEY=...`, `sec-historico --desde 2006Q1`, `precios --desde 2008-09-01`, `historico --desde 2009-01-01`).

Ver `docs/EVALUACION.md` §5 y `docs/COSTOS.md`.

## 6. IBKR paper (opcional en esta fase)

1. Descarga **IB Gateway** para Linux desde el sitio de IBKR (instalador `.sh`) e instálalo.
2. Inicia sesión con tu **usuario paper**.
3. En *Configure → Settings → API → Settings*:
   - activa **Enable ActiveX and Socket Clients**;
   - deja marcado **Read-Only API**;
   - usa el puerto **4002**;
   - en *Trusted IPs*, solo `127.0.0.1`.
4. Ejecuta `uv run tt cuenta` para ver tu cuenta paper en solo lectura.

Los históricos de precios por la API de IBKR exigen una suscripción de datos de mercado y no incluyen acciones deslistadas. Por eso la herramienta usa Massive para los precios y deja IBKR solo para consultar la cuenta.

## 7. Comandos

| Comando | Para qué |
|---|---|
| `tt etf-precios` | Rotación: descargar ETFs (historia completa) y tasa del Tesoro |
| `tt etf-backtest [--periodo ...]` | Rotación: backtest pre-registrado y veredicto |
| `tt etf-senal [--capital USD]` | Rotación: recomendación del mes (se guarda; sin órdenes) |
| `tt iniciar` | Crear la base local y `.env` |
| `tt revisar [--ibkr]` | Diagnóstico de configuración y datos |
| `tt sec-historico --desde 2021Q1 --hasta 2026Q2` | Cargar la historia de Form 4 |
| `tt sec-diario [--dias 5]` | Form 4 recientes |
| `tt precios [--desde AAAA-MM-DD]` | Actualizar precios |
| `tt screener [--fecha AAAA-MM-DD]` | Evaluar compras recientes |
| `tt resultados` | Medir resultados de todas las señales |
| `tt diario` | Todo lo anterior, en orden |
| `tt historico` | Reconstrucción histórica (reserva bloqueada) |
| `tt informe` / `tt evaluar` | Resúmenes y veredicto estadístico |
| `tt riesgo --entrada 50 --stop 45` | Calculadora de tamaño y costos |
| `tt cuenta` | Cuenta IBKR paper (solo lectura) |
| `tt panel` | Panel web local |

## 8. Para desarrolladores

```bash
uv run pytest            # tests (sin red)
uv run ruff check .      # lint
uv run ruff format .     # formato
```

Estructura: `src/tradingtool/` contiene `etf/` (rotación de ETFs: datos, reglas, simulación, métricas e informe), `edgar/` (SEC), `insiders/` (clasificación y screener), `prices/` (fuentes, caché y calidad), `risk/` (costos y tamaño), `journal/` (diario y resultados), `broker/` (IBKR solo lectura), `ui/` (panel), `evaluation.py` y `cli.py`. La configuración versionada está en `config/` y los datos locales en `data/` (ignorado por git).
