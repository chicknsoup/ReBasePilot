# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\utils\\logger.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional

_FILE_HANDLERS: dict[str, RotatingFileHandler] = {}
_FILE_HANDLERS_LOCK = threading.Lock()


class _SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that never dies silently if Windows blocks rename (WinError 32)."""

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            if self.stream is None or getattr(self.stream, 'closed', False):
                try:
                    self.stream = self._open()
                except OSError:
                    pass


def _get_shared_file_handler(log_file: str, formatter: logging.Formatter) -> RotatingFileHandler:
    key = os.path.normcase(os.path.abspath(log_file))
    with _FILE_HANDLERS_LOCK:
        handler = _FILE_HANDLERS.get(key)
        if handler is None:
            handler = _SafeRotatingFileHandler(
                log_file,
                mode='a',
                encoding='utf-8',
                maxBytes=5242880,
                backupCount=3,
            )
            handler.setFormatter(formatter)
            _FILE_HANDLERS[key] = handler
        return handler


def setup_logger(name='BaseLooter', log_file=None, level=logging.INFO):
    """Configures and returns a logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.hasHandlers():
        return logger
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError, OSError):
        pass
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    if log_file is None:
        from app.utils.common import get_autoloot_log_path
        log_file = str(get_autoloot_log_path())
    try:
        file_handler = _get_shared_file_handler(log_file, formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f'Failed to set up file logging: {e}')
    return logger