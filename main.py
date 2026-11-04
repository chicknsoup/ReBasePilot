# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'main.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""Entry point for the BasePilot GUI."""
from __future__ import annotations
import os
import sys
from multiprocessing import freeze_support
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS') and (not os.environ.get('TESSERACT_CMD')):
            _bundled_tess = os.path.join(sys._MEIPASS, 'tesseract', 'tesseract.exe')
            if os.path.isfile(_bundled_tess):
                os.environ['TESSERACT_CMD'] = _bundled_tess
if '--profile' in sys.argv:
    try:
        os.environ['BASEPILOT_PROFILE'] = sys.argv[sys.argv.index('--profile') + 1]
    except IndexError:
        pass
if __name__ == '__main__' and '--release-self-test' in sys.argv:
        freeze_support()
        from app.utils.release_check import run_release_check
        try:
            _report = sys.argv[sys.argv.index('--release-self-test') + 1]
        except IndexError:
            raise SystemExit('--release-self-test requires a JSON report path')
        sys.exit(run_release_check(_report))
from app.ui.qt.app import run_gui
from app.utils.logger import setup_logger
from app.utils.tesseract_env import configure_tesseract
logger = setup_logger('Main')
def main():
    try:
        configure_tesseract()
        try:
            from app.services.display import DisplayService
            DisplayService().restore_if_pending()
        except Exception:
            logger.warning('Display restore-if-pending check failed', exc_info=True)
        logger.info('Starting Application...')
        run_gui()
    except Exception as e:
        logger.critical(f'Unhandled exception: {e}', exc_info=True)
        sys.exit(1)
if __name__ == '__main__':
    freeze_support()
    main()