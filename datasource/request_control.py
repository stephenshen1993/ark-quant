"""Scoped HTTP bounds for third-party market adapters (including AkShare)."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

import requests

_active = ContextVar("market_request_bounds", default=False)
_progress = ContextVar("market_progress", default=None)
_original_send = requests.sessions.Session.send
_HTTP_TIMEOUT = (5.0, 20.0)


def _bounded_send(self, request, **kwargs):
    if _active.get():
        timeout = kwargs.get("timeout")
        limits = _HTTP_TIMEOUT
        values = timeout if isinstance(timeout, tuple) else (timeout, timeout)
        kwargs["timeout"] = tuple(
            limit if value is None else min(float(value), limit)
            for value, limit in zip(values, limits)
        )
    return _original_send(self, request, **kwargs)


# Install once at module import; the context flag leaves unrelated requests untouched.
requests.sessions.Session.send = _bounded_send


@contextmanager
def market_requests():
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)


def bounded_market_requests(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with market_requests():
            return fn(*args, **kwargs)
    return wrapped


@contextmanager
def report_to(callback):
    token = _progress.set(callback)
    try:
        yield
    finally:
        _progress.reset(token)


def report_progress(stage, completed=None, total=None):
    callback = _progress.get()
    if callback is not None:
        callback(stage, completed, total)
