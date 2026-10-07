"""Fuentes de precios diarios (todas devuelven barras ajustadas en el formato de base.COLUMNS).

- MassiveSource (antes Polygon.io): endpoint "grouped daily" trae TODAS las acciones de EE. UU.
  de un día en una sola llamada (ideal para el plan gratuito, limitado en llamadas/minuto) e
  incluye acciones que luego se deslistaron (sin sesgo de supervivencia en ese periodo).
- TiingoSource: por ticker; plan gratuito limitado en símbolos únicos por mes.
- AlpacaSource: por ticker; plan gratuito con historia desde 2016 y ~200 llamadas/minuto.
- IbkrSource: históricos vía TWS/IB Gateway (solo tickers que cotizan hoy).
- CsvSource: archivos locales (pruebas o datos importados por el usuario).

Las claves de API viajan en cabeceras HTTP (nunca en la URL, para que no queden en logs).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from tradingtool.edgar.client import RateLimiter
from tradingtool.prices.base import COLUMNS, PriceSourceError, normalize_bars

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")


def _ms_to_ny_date(ms: int | float) -> date:
    return datetime.fromtimestamp(float(ms) / 1000.0, UTC).astimezone(NY).date()


class _HttpJsonClient:
    def __init__(
        self,
        headers: dict[str, str],
        max_per_second: float,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
    ):
        import time

        self._sleep = sleep or time.sleep
        self.limiter = RateLimiter(max_per_second, sleep=self._sleep)
        self.max_retries = max_retries
        self._http = httpx.Client(headers=headers, transport=transport, timeout=timeout)

    def get_json(self, url: str, params: dict[str, object] | None = None) -> object:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self._http.get(url, params=params)
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (401, 403):
                    raise PriceSourceError(
                        f"Clave de API inválida, o el dato está fuera de lo que cubre tu plan "
                        f"(p. ej. el plan gratuito de Massive solo cubre ~2 años) "
                        f"({resp.status_code})"
                    )
                if resp.status_code == 404:
                    return None
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise PriceSourceError(
                        f"Respuesta inesperada {resp.status_code}: {resp.text[:200]}"
                    )
                last = PriceSourceError(f"HTTP {resp.status_code}")
            if attempt < self.max_retries:
                wait = 15.0 * (attempt + 1)  # límites por minuto: esperar en serio
                log.warning("Reintentando %s en %.0fs (%r)", url, wait, last)
                self._sleep(wait)
        raise PriceSourceError(f"Fallaron los reintentos para {url}: {last!r}")


# ----------------------------------------------------------------------------- Massive / Polygon


class MassiveSource:
    """Massive.com (antes Polygon.io). Plan gratuito: pocas llamadas por minuto, datos de cierre."""

    name = "massive"
    adjusted = True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.massive.com",
        calls_per_minute: float = 4.5,  # plan gratuito: 5/min; dejamos margen (~13 s)
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        if not api_key:
            raise PriceSourceError("Falta TT_MASSIVE_API_KEY en tu .env (la clave gratuita sirve).")
        self.base_url = base_url.rstrip("/")
        self._client = _HttpJsonClient(
            {"Authorization": f"Bearer {api_key}"},
            max_per_second=calls_per_minute / 60.0,
            transport=transport,
            sleep=sleep,
        )

    @staticmethod
    def _rows(results: list[dict], ticker: str | None = None) -> pd.DataFrame:
        rows = [
            {
                "ticker": ticker or r.get("T"),
                "date": _ms_to_ny_date(r["t"]),
                "open": r.get("o"),
                "high": r.get("h"),
                "low": r.get("l"),
                "close": r.get("c"),
                "volume": r.get("v"),
            }
            for r in results
            if r.get("t") is not None and (ticker or r.get("T"))
        ]
        return normalize_bars(pd.DataFrame(rows, columns=COLUMNS))

    def market_day(self, day: date) -> pd.DataFrame:
        data = self._client.get_json(
            f"{self.base_url}/v2/aggs/grouped/locale/us/market/stocks/{day:%Y-%m-%d}",
            {"adjusted": "true", "include_otc": "false"},
        )
        if not isinstance(data, dict):
            return pd.DataFrame(columns=COLUMNS)
        if data.get("status") not in ("OK", "DELAYED", None):
            detail = data.get("error") or data.get("message")
            raise PriceSourceError(f"Massive respondió {data.get('status')}: {detail}")
        return self._rows(data.get("results") or [])

    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        data = self._client.get_json(
            f"{self.base_url}/v2/aggs/ticker/{ticker.upper()}/range/1/day/{start:%Y-%m-%d}/{end:%Y-%m-%d}",
            {"adjusted": "true", "sort": "asc", "limit": 50000},
        )
        if not isinstance(data, dict):
            return pd.DataFrame(columns=COLUMNS)
        return self._rows(data.get("results") or [], ticker=ticker.upper())


# ----------------------------------------------------------------------------- Tiingo


class TiingoSource:
    name = "tiingo"
    adjusted = True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.tiingo.com",
        requests_per_hour: float = 50.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        if not api_key:
            raise PriceSourceError("Falta TT_TIINGO_API_KEY en tu .env.")
        self.base_url = base_url.rstrip("/")
        self._client = _HttpJsonClient(
            {"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
            max_per_second=requests_per_hour / 3600.0,
            transport=transport,
            sleep=sleep,
        )

    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        # Tiingo usa "-" en clases de acciones (BRK-B); EDGAR suele usar "." (BRK.B).
        sym = ticker.upper().replace(".", "-")
        data = self._client.get_json(
            f"{self.base_url}/tiingo/daily/{sym}/prices",
            {"startDate": f"{start:%Y-%m-%d}", "endDate": f"{end:%Y-%m-%d}"},
        )
        if not isinstance(data, list):
            return pd.DataFrame(columns=COLUMNS)
        rows = [
            {
                "ticker": ticker.upper(),
                "date": str(r.get("date", ""))[:10],
                "open": r.get("adjOpen", r.get("open")),
                "high": r.get("adjHigh", r.get("high")),
                "low": r.get("adjLow", r.get("low")),
                "close": r.get("adjClose", r.get("close")),
                "volume": r.get("adjVolume", r.get("volume")),
            }
            for r in data
        ]
        return normalize_bars(pd.DataFrame(rows, columns=COLUMNS))


# ----------------------------------------------------------------------------- Alpaca


class AlpacaSource:
    """Alpaca Market Data (plan gratuito "Basic"): barras diarias ajustadas desde 2016.

    Necesita una cuenta gratuita (basta la paper) y sus dos claves. El plan gratuito no entrega
    los últimos 15 minutos del feed SIP, así que nunca se pide el día de hoy.
    Limitación honesta: la cobertura de acciones deslistadas es parcial.
    """

    name = "alpaca"
    adjusted = True

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        base_url: str = "https://data.alpaca.markets",
        calls_per_minute: float = 180.0,  # plan gratuito: 200/min; dejamos margen
        feed: str = "sip",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        if not key_id or not secret_key:
            raise PriceSourceError("Faltan TT_ALPACA_KEY_ID y/o TT_ALPACA_SECRET_KEY en tu .env.")
        self.base_url = base_url.rstrip("/")
        self.feed = feed
        self._client = _HttpJsonClient(
            {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key},
            max_per_second=calls_per_minute / 60.0,
            transport=transport,
            sleep=sleep,
        )

    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        end = min(end, datetime.now(NY).date() - timedelta(days=1))
        if start > end:
            return pd.DataFrame(columns=COLUMNS)
        sym = ticker.upper()
        params: dict[str, object] = {
            "symbols": sym,
            "timeframe": "1Day",
            "start": f"{start:%Y-%m-%d}",
            "end": f"{end:%Y-%m-%d}",
            "adjustment": "all",
            "feed": self.feed,
            "limit": 10000,
            "sort": "asc",
        }
        rows: list[dict] = []
        for _ in range(100):  # tope de páginas por seguridad
            data = self._client.get_json(f"{self.base_url}/v2/stocks/bars", params)
            if not isinstance(data, dict):
                break
            for r in (data.get("bars") or {}).get(sym) or []:
                if not r.get("t"):
                    continue
                rows.append(
                    {
                        "ticker": sym,
                        "date": _iso_to_ny_date(r["t"]),
                        "open": r.get("o"),
                        "high": r.get("h"),
                        "low": r.get("l"),
                        "close": r.get("c"),
                        "volume": r.get("v"),
                    }
                )
            token = data.get("next_page_token")
            if not token:
                break
            params = {**params, "page_token": token}
        return normalize_bars(pd.DataFrame(rows, columns=COLUMNS))


def _iso_to_ny_date(ts: str) -> date:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(NY).date()


# ----------------------------------------------------------------------------- EODHD


class EodhdSource:
    """EODHD "EOD Historical Data" (pago, ~US$20 por un mes): historia larga por ticker,
    incluidas acciones deslistadas. Útil para la validación histórica 2009-2025.

    EODHD exige la clave en la URL (``api_token``); por eso el logging de httpx se silencia.
    Los precios OHLC se ajustan con el factor ``adjusted_close / close``.
    """

    name = "eodhd"
    adjusted = True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://eodhd.com",
        requests_per_second: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        if not api_key:
            raise PriceSourceError("Falta TT_EODHD_API_KEY en tu .env.")
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self._client = _HttpJsonClient(
            {}, max_per_second=requests_per_second, transport=transport, sleep=sleep
        )

    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        sym = ticker.upper().replace(".", "-")
        data = self._client.get_json(
            f"{self.base_url}/api/eod/{sym}.US",
            {
                "from": f"{start:%Y-%m-%d}",
                "to": f"{end:%Y-%m-%d}",
                "fmt": "json",
                "api_token": self._key,
            },
        )
        if not isinstance(data, list):
            return pd.DataFrame(columns=COLUMNS)
        rows = []
        for r in data:
            close, adj = r.get("close"), r.get("adjusted_close")
            try:
                f = float(adj) / float(close) if close and adj else 1.0
            except (TypeError, ValueError, ZeroDivisionError):
                f = 1.0
            rows.append(
                {
                    "ticker": ticker.upper(),
                    "date": r.get("date"),
                    "open": _scale(r.get("open"), f),
                    "high": _scale(r.get("high"), f),
                    "low": _scale(r.get("low"), f),
                    "close": _scale(close, f),
                    "volume": (float(r["volume"]) / f)
                    if r.get("volume") and f
                    else r.get("volume"),
                }
            )
        return normalize_bars(pd.DataFrame(rows, columns=COLUMNS))


def _scale(value: object, factor: float) -> float | None:
    try:
        return float(value) * factor  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- IBKR


class IbkrSource:
    """Históricos diarios ajustados vía TWS / IB Gateway (conexión de solo lectura)."""

    name = "ibkr"
    adjusted = True

    def __init__(self, ib: object):
        self._ib = ib  # instancia conectada de ib_async.IB (p. ej. IbkrReadOnly._ib)

    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        from ib_async import Stock

        days = max((end - start).days + 1, 1)
        duration = f"{min(days // 365 + 1, 20)} Y" if days > 365 else f"{days} D"
        contract = Stock(ticker.upper().replace(".", " "), "SMART", "USD")
        bars = self._ib.reqHistoricalData(  # type: ignore[attr-defined]
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow="ADJUSTED_LAST",
            useRTH=True,
            formatDate=1,
        )
        rows = [
            {
                "ticker": ticker.upper(),
                "date": b.date,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars or []
        ]
        df = normalize_bars(pd.DataFrame(rows, columns=COLUMNS))
        return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


# ----------------------------------------------------------------------------- CSV


class CsvSource:
    """Lee ``<dir>/<TICKER>.csv`` con columnas date,open,high,low,close,volume."""

    name = "csv"
    adjusted = True

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        path = self.directory / f"{ticker.upper()}.csv"
        if not path.exists():
            return pd.DataFrame(columns=COLUMNS)
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        df["ticker"] = ticker.upper()
        df = normalize_bars(df)
        return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)
