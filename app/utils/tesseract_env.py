"""Resolve Tesseract OCR for development and for PyInstaller one-file builds."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
from app.utils.logger import setup_logger
logger = setup_logger('TesseractEnv')


def _patch_pytesseract_hidden_console():
    """Hide the console window when ``tesseract.exe`` is spawned from a GUI / windowed process."""
    if sys.platform != 'win32':
        return
    flag = getattr(subprocess, 'CREATE_NO_WINDOW', None)
    if flag is None:
        return
    try:
        import pytesseract.pytesseract
        pt = pytesseract.pytesseract
        _orig = pt.subprocess_args
        if getattr(_orig, '_basepilot_patched', False):
            return

        def subprocess_args(include_stdout=True):
            kwargs = _orig(include_stdout)
            prev = int(kwargs.get('creationflags', 0))
            kwargs['creationflags'] = prev | flag
            return kwargs

        subprocess_args._basepilot_patched = True
        pt.subprocess_args = subprocess_args
    except ImportError:
        return None


def _tessdata_dir(install_root):
    """Directory that contains ``*.traineddata`` (Tesseract expects TESSDATA_PREFIX to point here on Windows)."""
    return install_root / 'tessdata'


def configure_tesseract():
    """
Point pytesseract at tesseract.exe and set TESSDATA_PREFIX to the ``tessdata`` folder
(where ``eng.traineddata`` lives), not the install root.

Resolution order:
1. ``TESSERACT_CMD`` env var (full path to tesseract.exe)
2. Frozen app: ``sys._MEIPASS/tesseract/tesseract.exe`` or ``sys._MEIPASS/tesseract.exe``
3. Dev default: ``<repo>/tesseract/tesseract.exe`` or ``C:\\Program Files\\Tesseract-OCR\\tesseract.exe``
"""
    try:
        import pytesseract
        _patch_pytesseract_hidden_console()
        if os.environ.get('TESSERACT_CMD'):
            cmd = Path(os.environ['TESSERACT_CMD'])
            pytesseract.pytesseract.tesseract_cmd = str(cmd)
            td = _tessdata_dir(cmd.parent)
            os.environ['TESSDATA_PREFIX'] = str(td.resolve()) + os.sep
            logger.debug('Tesseract from TESSERACT_CMD: %s, tessdata=%s', cmd, td)
        elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            base = Path(sys._MEIPASS)
            for bundled in (base / 'tesseract' / 'tesseract.exe', base / 'tesseract.exe'):
                if bundled.is_file():
                    pytesseract.pytesseract.tesseract_cmd = str(bundled)
                    td = _tessdata_dir(bundled.parent)
                    os.environ['TESSDATA_PREFIX'] = str(td.resolve()) + os.sep
                    logger.debug('Tesseract bundled at %s, tessdata=%s', bundled, td)
                    break
            else:
                logger.error('Frozen build: tesseract.exe not found under %s. OCR will not work; the Tesseract binary was not bundled/extracted correctly.', base)
        else:
            from app.utils.common import get_resource_path
            for candidate in (get_resource_path('tesseract/tesseract.exe'), get_resource_path('tesseract_bundle/tesseract.exe'), Path('C:\\Program Files\\Tesseract-OCR\\tesseract.exe')):
                if candidate.is_file():
                    pytesseract.pytesseract.tesseract_cmd = str(candidate)
                    td = _tessdata_dir(candidate.parent)
                    os.environ['TESSDATA_PREFIX'] = str(td.resolve()) + os.sep
                    logger.debug('Tesseract dev install: %s, tessdata=%s', candidate, td)
                    break
            else:
                logger.error('Tesseract not configured (install with winget install UB-Mannheim.TesseractOCR or set TESSERACT_CMD). OCR will not work until Tesseract is available.')
    except ImportError:
        return None
