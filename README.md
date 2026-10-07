# tradingtool: panel de apoyo a decisiones (compras de insiders)

Herramienta personal de **investigación**. Cada día revisa las compras de acciones que hacen directivos y directores de empresas de EE. UU. (Form 4 de la SEC). Aplica reglas fijas y pre-registradas (estrategia `insider-v1`) y te muestra ideas con su tesis, su riesgo y su costo. Guarda en un diario lo que apruebas, lo que rechazas y lo que bloquean los filtros, para medir con honestidad si algo de esto le gana a comprar y mantener un ETF.

> **Seguridad (reglas no negociables)**
> - **No envía órdenes.** "Aprobar" solo lo escribe en tu diario.
> - La conexión a IBKR es de **solo lectura** y rechaza cuentas reales por defecto (solo acepta paper, que empiezan por `DU`).
> - Tus claves viven en `.env`, que **nunca** se sube a git.
> - Nada de esto es asesoría financiera ni promete rentabilidad.

Documentos clave:

- [`docs/00-critica-y-plan.md`](docs/00-critica-y-plan.md): crítica, investigación y plan por fases.
- [`docs/EVALUACION.md`](docs/EVALUACION.md): pre-registro de la estrategia y criterio de éxito.
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
| `TT_MASSIVE_API_KEY` | Clave gratuita de [massive.com](https://massive.com) (antes Polygon.io), para los precios |

Luego verifica que todo esté bien:

```bash
uv run tt revisar
```

## 2. Primera carga de datos (una sola vez)

```bash
# Historia de Form 4. Hacen falta ≥3 años antes del periodo a evaluar para clasificar a
# los insiders como rutinarios u oportunistas. Son ~10-17 MB por trimestre.
uv run tt sec-historico --desde 2021Q1 --hasta 2026Q2

# Precios diarios de todo el mercado (una llamada por día, ~13 s entre llamadas por el
# límite del plan gratuito). ~500 días tardan ~2 horas. Déjalo corriendo.
uv run tt precios --desde 2024-10-15
```

## 3. Uso diario (~10 minutos)

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

## 4. ¿Funciona la estrategia? (validación histórica)

```bash
uv run tt historico --desde 2024-10-01 --hasta 2025-09-30   # reconstruye señales pasadas
uv run tt precios --desde 2024-10-01                        # asegura precios
uv run tt resultados                                        # mide qué pasó después
uv run tt evaluar --origen backtest                         # veredicto pre-registrado
```

El periodo de **reserva** (desde 2025-10-01) está bloqueado: se abre una sola vez, al final, con `--abrir-reserva`, y la apertura queda registrada. Con solo ~12 meses de historia gratuita, lo esperable es el veredicto **INSUFICIENTE**.

Para una validación seria 2009–2025, la opción más barata es pagar **un mes de EODHD (US$19,99)**. Pon `TT_PRICE_SOURCE=eodhd` y `TT_EODHD_API_KEY=...` en `.env`, y luego:

```bash
uv run tt sec-historico --desde 2006Q1 --hasta 2025Q3
uv run tt historico --desde 2009-01-01 --hasta 2025-09-30
uv run tt precios --desde 2008-09-01 --hasta 2025-12-31   # solo los tickers de los eventos
uv run tt resultados && uv run tt evaluar --origen backtest
```

Ver `docs/EVALUACION.md` §5 y `docs/COSTOS.md`.

## 5. IBKR paper (opcional en esta fase)

1. Descarga **IB Gateway** para Linux desde el sitio de IBKR (instalador `.sh`) e instálalo.
2. Inicia sesión con tu **usuario paper**.
3. En *Configure → Settings → API → Settings*:
   - activa **Enable ActiveX and Socket Clients**;
   - deja marcado **Read-Only API**;
   - usa el puerto **4002**;
   - en *Trusted IPs*, solo `127.0.0.1`.
4. Ejecuta `uv run tt cuenta` para ver tu cuenta paper en solo lectura.

Los históricos de precios por la API de IBKR exigen una suscripción de datos de mercado y no incluyen acciones deslistadas. Por eso la herramienta usa Massive para los precios y deja IBKR solo para consultar la cuenta.

## 6. Comandos

| Comando | Para qué |
|---|---|
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

## 7. Para desarrolladores

```bash
uv run pytest            # tests (sin red)
uv run ruff check .      # lint
uv run ruff format .     # formato
```

Estructura: `src/tradingtool/` contiene `edgar/` (SEC), `insiders/` (clasificación y screener), `prices/` (fuentes, caché y calidad), `risk/` (costos y tamaño), `journal/` (diario y resultados), `broker/` (IBKR solo lectura), `ui/` (panel), `evaluation.py` y `cli.py`. La configuración versionada está en `config/` y los datos locales en `data/` (ignorado por git).
