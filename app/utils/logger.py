# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\utils\\logger.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional
def setup_logger(name='BaseLooter', log_file=None, level=logging.INFO):
    """Configures and returns a logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.hasHandlers():
        return logger
    else:
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
            file_handler = RotatingFileHandler(log_file, mode='a', encoding='utf-8', maxBytes=5242880, backupCount=3)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            return logger
        except Exception as e:
            print(f'Failed to set up file logging: {e}')
            return logger