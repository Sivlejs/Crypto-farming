"""
Threading utilities for Nexus AI.

Provides decorators and utilities for safe thread management,
especially important when running under eventlet/gunicorn where
native threads can interact poorly with patched sockets.
"""
from __future__ import annotations

import functools
import threading
import traceback
from typing import Callable

from nexus.utils.logger import get_logger

logger = get_logger(__name__)


def catch_thread_exceptions(func: Callable) -> Callable:
    """
    Decorator that wraps thread target functions to catch all exceptions.
    
    This prevents uncaught exceptions in daemon threads from crashing the
    main process, especially important when running under eventlet/gunicorn
    where native threads can interact poorly with patched sockets.
    
    The decorator logs any exceptions and allows the thread to exit gracefully.
    
    Usage:
        @catch_thread_exceptions
        def my_thread_target():
            # This thread won't crash the main process if an exception occurs
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except SystemExit:
            # Allow normal thread exit
            pass
        except Exception:
            logger.error(
                "Unhandled exception in thread '%s':\n%s",
                threading.current_thread().name,
                traceback.format_exc()
            )
    return wrapper


# Alias for backward compatibility
safe_thread_target = catch_thread_exceptions
