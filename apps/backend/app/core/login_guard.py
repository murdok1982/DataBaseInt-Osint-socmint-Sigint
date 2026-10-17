"""Protección del login: fuerza bruta y anti-repetición TOTP.

Autorizado por: **SPEC-04 §2** (TOTP con anti-repetición) y **SPEC-04 §2.1**
(bloqueo tras ``LOGIN_MAX_FAILURES`` fallos en ``LOGIN_WINDOW_SECONDS``).

El estado vive **en memoria del proceso**. Con varios workers de uvicorn cada
uno lleva su propio contador; por eso SPEC-04 §2.1 exige además ``limit_req`` en
Nginx (``deploy/nginx/nginx.conf``), que es global por IP.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Deque, Optional

from app.core.config import get_settings


@dataclass
class LoginThrottle:
    """Ventana deslizante de fallos por clave ``(ip, usuario)``."""

    max_failures: int
    window_seconds: int
    clock: Callable[[], float] = time.monotonic
    _failures: dict[str, Deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def key(client_ip: str, username: str) -> str:
        return f"{client_ip}|{(username or '').strip().lower()}"

    def _prune(self, key: str, now: float) -> Deque[float]:
        bucket = self._failures.setdefault(key, deque())
        while bucket and now - bucket[0] >= self.window_seconds:
            bucket.popleft()
        if not bucket:
            self._failures.pop(key, None)
        return bucket

    def retry_after(self, key: str) -> Optional[int]:
        """Segundos de bloqueo restantes, o ``None`` si se permite intentar."""
        with self._lock:
            now = self.clock()
            bucket = self._prune(key, now)
            if len(bucket) < self.max_failures:
                return None
            remaining = self.window_seconds - (now - bucket[0])
            return max(1, math.ceil(remaining))

    def register_failure(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            bucket = self._prune(key, now)
            bucket.append(now)
            self._failures[key] = bucket

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


@dataclass
class TotpReplayGuard:
    """Recuerda el último contador TOTP aceptado por operador."""

    _last: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def accept(self, user_id: str, counter: int) -> bool:
        """``True`` si ``counter`` es posterior al último aceptado (y lo registra)."""
        with self._lock:
            last = self._last.get(user_id)
            if last is not None and counter <= last:
                return False
            self._last[user_id] = counter
            return True


@lru_cache(maxsize=1)
def get_login_throttle() -> LoginThrottle:
    settings = get_settings()
    return LoginThrottle(
        max_failures=int(settings.login_max_failures),
        window_seconds=int(settings.login_window_seconds),
    )


@lru_cache(maxsize=1)
def get_totp_replay_guard() -> TotpReplayGuard:
    return TotpReplayGuard()


def reset_login_guards() -> None:
    """Reinicia contadores y caché (tests y recarga de configuración)."""
    get_login_throttle.cache_clear()
    get_totp_replay_guard.cache_clear()


__all__ = [
    "LoginThrottle",
    "TotpReplayGuard",
    "get_login_throttle",
    "get_totp_replay_guard",
    "reset_login_guards",
]
