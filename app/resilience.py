import time
import random
import threading
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

import requests

# Lightweight resilience utilities: exponential backoff, circuit breaker, and resilient HTTP


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    """Simple per-key circuit breaker with consecutive-failure threshold.

    - Closed: normal operation
    - Open: immediately fail for reset_timeout seconds
    - Half-open: after reset_timeout, allow a single probe; success closes, failure opens
    """

    def __init__(self, fail_threshold: int = 5, reset_timeout: float = 30.0) -> None:
        self.fail_threshold = max(1, int(fail_threshold))
        self.reset_timeout = max(1.0, float(reset_timeout))
        self._consecutive_failures = 0
        self._opened_at: float = 0.0
        self._half_open_try: bool = False
        self._lock = threading.Lock()

    def state(self) -> str:
        with self._lock:
            now = time.monotonic()
            if self._opened_at > 0 and (now - self._opened_at) < self.reset_timeout:
                return "open"
            if self._opened_at > 0 and (now - self._opened_at) >= self.reset_timeout:
                # eligible for half open
                return "half_open"
            return "closed"

    def allow_request(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._opened_at > 0:
                if (now - self._opened_at) < self.reset_timeout:
                    return False
                # half-open window
                if not self._half_open_try:
                    self._half_open_try = True
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = 0.0
            self._half_open_try = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.fail_threshold:
                self._opened_at = time.monotonic()
                self._half_open_try = False


_breakers: Dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_breaker(key: str, fail_threshold: int, reset_timeout: float) -> CircuitBreaker:
    with _breakers_lock:
        br = _breakers.get(key)
        if br is None:
            br = CircuitBreaker(fail_threshold=fail_threshold, reset_timeout=reset_timeout)
            _breakers[key] = br
        return br


def _exp_backoff_with_jitter(attempt: int, base_ms: int = 100, cap_ms: int = 2000) -> float:
    base_ms = max(1, int(base_ms))
    cap_ms = max(base_ms, int(cap_ms))
    # 2^attempt backoff multiplied by base, then cap, add full jitter
    delay_ms = min(cap_ms, base_ms * (2 ** max(0, attempt)))
    jitter_ms = random.randint(0, delay_ms)
    return (delay_ms + jitter_ms) / 1000.0


def resilient_request(
    method: str,
    url: str,
    *,
    timeout: float,
    retries: int,
    backoff_base_ms: int,
    status_forcelist: Sequence[int] = (500, 502, 503, 504),
    circuit_key: Optional[str] = None,
    circuit_fail_threshold: int = 5,
    circuit_reset_sec: float = 30.0,
    **kwargs: Any,
) -> requests.Response:
    """HTTP request with retry/backoff + circuit breaker.

    Raises CircuitOpenError if the circuit is open or requests.RequestException on final failure.
    """
    method = method.upper()
    br: Optional[CircuitBreaker] = None
    if circuit_key:
        br = get_breaker(circuit_key, circuit_fail_threshold, circuit_reset_sec)

    attempts = max(0, int(retries)) + 1
    last_exc: Optional[BaseException] = None
    for i in range(attempts):
        if br and not br.allow_request():
            raise CircuitOpenError(f"circuit_open:{circuit_key}")
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in status_forcelist:
                # treat as failure
                if br:
                    br.record_failure()
                last_exc = requests.HTTPError(f"HTTP {resp.status_code} for {url}")
                if i < attempts - 1:
                    time.sleep(_exp_backoff_with_jitter(i, base_ms=backoff_base_ms))
                    continue
                resp.raise_for_status()  # raise on final failure
            # success
            if br:
                br.record_success()
            return resp
        except Exception as e:  # requests exceptions
            last_exc = e
            if br:
                br.record_failure()
            if i < attempts - 1:
                time.sleep(_exp_backoff_with_jitter(i, base_ms=backoff_base_ms))
                continue
            # final failure
            if isinstance(e, Exception):
                raise
    # Should not reach here
    assert last_exc is not None
    raise last_exc

