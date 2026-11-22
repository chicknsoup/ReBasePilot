# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'main.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""Entry point for the BasePilot GUI."""
from __future__ import annotations
import os
import sys

# Must be set before importing numpy/cv2 so OpenBLAS does not reserve ~230 MB of per-core buffers.
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')

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
        try:
            import ctypes as _ct
            _k32 = _ct.windll.kernel32
            _k32.GetCurrentProcess.restype = _ct.c_void_p
            _k32.SetPriorityClass.argtypes = [_ct.c_void_p, _ct.c_uint32]
            # BELOW_NORMAL_PRIORITY_CLASS (0x4000) keeps desktop/browser/emulator completely smooth
            _k32.SetPriorityClass(_k32.GetCurrentProcess(), 0x00004000)
        except Exception:
            pass
        try:
            import cv2
            cv2.setNumThreads(2)
        except Exception:
            pass
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