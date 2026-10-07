"""Cliente HTTP para SEC EDGAR respetando sus reglas de acceso justo.

- User-Agent obligatorio con nombre y correo (la SEC bloquea bots no identificados).
- Máximo 10 solicitudes/segundo (por defecto usamos 5).
- Reintentos con espera exponencial ante 429/5xx y errores de red.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

SEC_ARCHIVES = "https://www.sec.gov/Archives"


class EdgarConfigError(RuntimeError):
    pass


class EdgarHTTPError(RuntimeError):
    def __init__(self, url: str, status: int | None, msg: str):
        super().__init__(f"{msg} ({status}) en {url}")
        self.url = url
        self.status = status


class RateLimiter:
    """Limitador simple: garantiza un intervalo mínimo entre solicitudes."""

    def __init__(
        self,
        max_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_per_second <= 0 or max_per_second > 10:
            raise ValueError("max_per_second debe estar en (0, 10] según las reglas de la SEC")
        self.min_interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None:
                delay = self._last + self.min_interval - now
                if delay > 0:
                    self._sleep(delay)
                    now = self._clock()
            self._last = now


def validate_user_agent(user_agent: str) -> str:
    ua = (user_agent or "").strip()
    if "@" not in ua or len(ua.split()) < 2:
        raise EdgarConfigError(
            "Configura TT_SEC_USER_AGENT en tu archivo .env con tu nombre y correo, por ejemplo: "
            'TT_SEC_USER_AGENT="Juan Perez juan@correo.com". La SEC lo exige.'
        )
    return ua


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        max_requests_per_second: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ):
        self.user_agent = validate_user_agent(user_agent)
        self.limiter = RateLimiter(max_requests_per_second, sleep=sleep)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        self._http = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_text(self, url: str, allow_404: bool = False) -> str | None:
        """GET de texto con límite de tasa y reintentos. None si 404 y ``allow_404``."""
        resp = self._get(url, allow_404)
        return None if resp is None else resp.text

    def get_bytes(self, url: str, allow_404: bool = False) -> bytes | None:
        """GET binario (p. ej. archivos .zip). None si 404 y ``allow_404``."""
        resp = self._get(url, allow_404)
        return None if resp is None else resp.content

    def _get(self, url: str, allow_404: bool) -> httpx.Response | None:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self._http.get(url)
            except httpx.TransportError as exc:
                last_exc = exc
                log.warning("Error de red en %s (intento %d): %r", url, attempt + 1, exc)
            else:
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 404 and allow_404:
                    return None
                if resp.status_code == 403:
                    raise EdgarHTTPError(
                        url,
                        403,
                        "La SEC rechazó la solicitud (¿User-Agent sin correo o exceso de "
                        "velocidad?)",
                    )
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise EdgarHTTPError(url, resp.status_code, "Respuesta inesperada de la SEC")
                last_exc = EdgarHTTPError(url, resp.status_code, "Error temporal de la SEC")
                log.warning("HTTP %s en %s (intento %d)", resp.status_code, url, attempt + 1)
            if attempt < self.max_retries:
                self._sleep(self.backoff_base * (2**attempt))
        raise EdgarHTTPError(
            url, getattr(last_exc, "status", None), f"Fallaron los reintentos: {last_exc!r}"
        )
