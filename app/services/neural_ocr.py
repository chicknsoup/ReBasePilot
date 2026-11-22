"""Dual-stage offline neural OCR (PP-OCRv4 DBNet detector + EasyOCR latin_g2 64px BiLSTM-CTC).

Runs in-process via ``onnxruntime`` (if installed) or OpenCV ``cv2.dnn`` (always bundled,
zero extra dependencies). Models are loaded lazily on first use from ``assets/ocr_models/``.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from app.utils.common import get_resource_path
from app.utils.logger import setup_logger

logger = setup_logger('NeuralOCR')

_LOCK = threading.Lock()
_ENGINE: Optional['_NeuralEngine'] = None
_ENGINE_INIT_ATTEMPTED = False


def _locate_model_files() -> Optional[Tuple[Path, Path, Path]]:
    base = get_resource_path('assets/ocr_models')
    det = base / 'det.onnx'
    rec = base / 'rec_latin.onnx'
    vocab = base / 'rec_latin.json'
    if det.is_file() and rec.is_file() and vocab.is_file():
        return (det, rec, vocab)
    return None


class _OnnxRunner:
    """Thin wrapper supporting either onnxruntime.InferenceSession or cv2.dnn.Net."""

    def __init__(self, model_path: Path):
        self._lock = threading.Lock()
        self._backend = 'cv2'
        self._ort_session = None
        self._ort_input_name = None
        self._cv_net = None

        try:
            import onnxruntime as ort  # type: ignore

            opts = ort.SessionOptions()
            # Prevent ORT's greedy power-of-2 CPU memory arena from hoarding hundreds of MB across variable ROI sizes
            opts.enable_cpu_mem_arena = False
            opts.enable_mem_pattern = False
            opts.intra_op_num_threads = min(4, max(2, (os.cpu_count() or 4) // 2))
            opts.inter_op_num_threads = 1
            # Disable thread busy-spinning so idle/between-raid CPU stays near 0%
            opts.add_session_config_entry('session.intra_op.allow_spinning', '0')
            opts.add_session_config_entry('session.inter_op.allow_spinning', '0')
            sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=['CPUExecutionProvider'])
            self._ort_session = sess
            self._ort_input_name = sess.get_inputs()[0].name
            self._backend = 'onnxruntime'
            return
        except Exception:
            pass

        self._cv_net = cv2.dnn.readNetFromONNX(str(model_path))
        self._backend = 'cv2'

    @property
    def backend(self) -> str:
        return self._backend

    def run(self, blob: np.ndarray) -> np.ndarray:
        with self._lock:
            if self._ort_session is not None:
                return self._ort_session.run(None, {self._ort_input_name: blob})[0]
            self._cv_net.setInput(blob)
            return self._cv_net.forward()


class _NeuralEngine:
    def __init__(self, det_path: Path, rec_path: Path, vocab_path: Path):
        data = json.loads(vocab_path.read_text(encoding='utf-8'))
        chars = data.get('characters') or []
        if len(chars) < 50:
            raise ValueError('Invalid rec_latin.json vocabulary')
        self.vocab: List[str] = [str(c) for c in chars]
        self.det = _OnnxRunner(det_path)
        self.rec = _OnnxRunner(rec_path)
        logger.info('Neural OCR loaded (det=%s, rec=%s, vocab=%d)', self.det.backend, self.rec.backend, len(self.vocab))

    @staticmethod
    def _is_dark_background(bgr: np.ndarray) -> bool:
        h, w = bgr.shape[:2]
        if h < 2 or w < 2:
            return False
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
        return float(np.median(border)) < 128.0

    def detect_boxes(self, roi_bgr: np.ndarray) -> List[Tuple[int, int, int, int, bool]]:
        orig_h, orig_w = roi_bgr.shape[:2]
        if orig_h < 4 or orig_w < 4:
            return []

        pad = 16
        bordered = cv2.copyMakeBorder(roi_bgr, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        bh, bw = bordered.shape[:2]
        max_dim = max(bh, bw)
        if bh < 380 and bw < 1100:
            scale = 2.0
        elif max_dim > 1920:
            scale = 1920.0 / float(max_dim)
        else:
            scale = 1.0

        sw = max(32, int(round(bw * scale)))
        sh = max(32, int(round(bh * scale)))
        scaled = cv2.resize(bordered, (sw, sh), interpolation=cv2.INTER_LINEAR) if abs(scale - 1.0) > 1e-4 else bordered

        tw = max(32, (sw + 31) & ~31)
        th = max(32, (sh + 31) & ~31)
        canvas = np.zeros((th, tw, 3), dtype=np.float32)
        arr = cv2.cvtColor(scaled, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        arr[:, :, 0] = (arr[:, :, 0] - 0.485) / 0.229
        arr[:, :, 1] = (arr[:, :, 1] - 0.456) / 0.224
        arr[:, :, 2] = (arr[:, :, 2] - 0.406) / 0.225
        canvas[:sh, :sw] = arr

        blob = np.transpose(canvas, (2, 0, 1))[None, :, :, :]
        prob_map = self.det.run(blob)[0, 0, :sh, :sw]

        mask = (prob_map >= 0.26).astype(np.uint8)
        n_cc, _labels, stats, _cen = cv2.connectedComponentsWithStats(mask)
        boxes: List[Tuple[int, int, int, int, bool]] = []
        for idx in range(1, n_cc):
            bx, by, cw, ch, area = map(int, stats[idx][:5])
            if area < 5 or cw < 2 or ch < 2:
                continue
            if float(prob_map[by:by + ch, bx:bx + cw].mean()) < 0.45:
                continue
            delta = (cw * ch * 1.75) / (2.0 * (cw + ch))
            px = max(2.5, delta * 1.2)
            py = max(2.0, delta)
            x0 = max(0, min(orig_w - 1, int(round((bx - px) / scale - pad))))
            y0 = max(0, min(orig_h - 1, int(round((by - py) / scale - pad))))
            x1 = max(x0 + 1, min(orig_w, int(round((bx + cw + px) / scale - pad))))
            y1 = max(y0 + 1, min(orig_h, int(round((by + ch + py) / scale - pad))))
            if (x1 - x0) < 3 or (y1 - y0) < 4:
                continue
            dark_bg = self._is_dark_background(roi_bgr[y0:y1, x0:x1])
            boxes.append((x0, y0, x1, y1, dark_bg))

        # Tight single-line crop fallback
        if not boxes and orig_h <= 110 and orig_w >= orig_h:
            boxes.append((0, 0, orig_w, orig_h, self._is_dark_background(roi_bgr)))

        return boxes

    def recognize_crop(self, crop_bgr: np.ndarray, *, dark_bg: bool = True) -> Tuple[str, float]:
        if crop_bgr is None or not getattr(crop_bgr, 'size', 0):
            return ('', 0.0)
        ch, cw = crop_bgr.shape[:2]
        if cw < 2 or ch < 3:
            return ('', 0.0)

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        if dark_bg:
            gray = 255 - gray
        p2, p90 = np.percentile(gray, [2, 90])
        if p90 > p2 + 5.0:
            gray = np.clip((gray.astype(np.float32) - p2) * 255.0 / (p90 - p2), 0.0, 255.0).astype(np.uint8)

        target_h = 64
        scaled_w = max(16, min(1600, int(round(cw * (target_h / float(ch))))))
        resized = cv2.resize(gray, (scaled_w, target_h), interpolation=cv2.INTER_CUBIC)
        padded = cv2.copyMakeBorder(resized, 0, 0, 10, 10, cv2.BORDER_CONSTANT, value=255)
        blob = ((padded.astype(np.float32) / 255.0) * 2.0 - 1.0)[None, None, :, :]

        logits = self.rec.run(blob)[0]  # (seq_len, num_classes)
        if logits.ndim != 2 or logits.shape[0] == 0:
            return ('', 0.0)

        best_ids = logits.argmax(axis=1)
        chars: List[str] = []
        probs: List[float] = []
        prev = 0
        vocab_len = len(self.vocab)
        for t, cls_idx in enumerate(best_ids):
            c = int(cls_idx)
            if c != 0 and c != prev and c < vocab_len:
                row = logits[t]
                m = float(row[c])
                ex = np.exp(row - m)
                prob = float(1.0 / max(1e-9, float(ex.sum())))
                tok = self.vocab[c]
                if tok == ' ' and prob < 0.50:
                    prev = 0
                    continue
                chars.append(tok)
                if tok != ' ':
                    probs.append(prob)
            prev = c

        if not probs:
            return ('', 0.0)
        mean_prob = sum(probs) / float(len(probs))
        if mean_prob < 0.24 or (len(probs) == 1 and mean_prob < 0.42):
            return ('', 0.0)
        return (''.join(chars).strip(), mean_prob * 100.0)


class NeuralOcrService:
    """Thread-safe facade for offline PP-OCRv4 + EasyOCR Latin recognition."""

    @staticmethod
    def models_present() -> bool:
        return _locate_model_files() is not None

    @staticmethod
    def _get_engine() -> Optional[_NeuralEngine]:
        global _ENGINE, _ENGINE_INIT_ATTEMPTED
        if _ENGINE is not None:
            return _ENGINE
        with _LOCK:
            if _ENGINE is not None:
                return _ENGINE
            if _ENGINE_INIT_ATTEMPTED:
                return None
            _ENGINE_INIT_ATTEMPTED = True
            paths = _locate_model_files()
            if paths is None:
                logger.warning('Neural OCR models not found under assets/ocr_models/')
                return None
            try:
                _ENGINE = _NeuralEngine(*paths)
            except Exception:
                logger.warning('Failed to initialize Neural OCR engine', exc_info=True)
                _ENGINE = None
            return _ENGINE

    @staticmethod
    def is_available() -> bool:
        return NeuralOcrService.models_present() and NeuralOcrService._get_engine() is not None

    @staticmethod
    def find_words(screen_img: np.ndarray, region=None, *, white_text: bool = False):
        """Detect and recognize words in ``screen_img`` (or ``region``), returning ``OcrWordBox`` list."""
        from app.services.vision import OcrWordBox

        if screen_img is None or not getattr(screen_img, 'size', 0):
            return []
        engine = NeuralOcrService._get_engine()
        if engine is None:
            return []

        fh, fw = screen_img.shape[:2]
        if region is not None:
            rx, ry, rw, rh = map(int, region)
            rx = max(0, min(fw - 1, rx))
            ry = max(0, min(fh - 1, ry))
            rw = max(1, min(fw - rx, rw))
            rh = max(1, min(fh - ry, rh))
            roi = screen_img[ry:ry + rh, rx:rx + rw]
        else:
            rx, ry = 0, 0
            roi = screen_img

        boxes = engine.detect_boxes(roi)
        if not boxes:
            return []

        results: List[OcrWordBox] = []
        for x0, y0, x1, y1, detected_dark in sorted(boxes, key=lambda b: (b[1], b[0])):
            dark_bg = True if white_text else detected_dark
            text, conf = engine.recognize_crop(roi[y0:y1, x0:x1], dark_bg=dark_bg)
            if not text:
                continue
            words = [w for w in text.split() if w]
            if not words:
                continue
            bw = max(1, x1 - x0)
            bh = max(1, y1 - y0)
            if len(words) == 1:
                results.append(
                    OcrWordBox(
                        left=rx + x0,
                        top=ry + y0,
                        width=bw,
                        height=bh,
                        text=words[0],
                        confidence=float(conf),
                    )
                )
            else:
                # Split horizontal span proportionally across words so downstream column/row grouping works
                total_chars = max(1, sum(len(w) for w in words) + len(words) - 1)
                cursor = 0
                for w in words:
                    w_left = x0 + int(round(bw * (cursor / float(total_chars))))
                    w_width = max(1, int(round(bw * (len(w) / float(total_chars)))))
                    results.append(
                        OcrWordBox(
                            left=rx + w_left,
                            top=ry + y0,
                            width=w_width,
                            height=bh,
                            text=w,
                            confidence=float(conf),
                        )
                    )
                    cursor += len(w) + 1
        return results

    @staticmethod
    def recognize_line(screen_img: np.ndarray, region, *, dark_bg: bool = True):
        """Recognize a single known text-line ROI directly with rec_latin (skips DBNet)."""
        from app.services.vision import OcrWordBox

        if screen_img is None or not getattr(screen_img, 'size', 0):
            return []
        engine = NeuralOcrService._get_engine()
        if engine is None:
            return []

        fh, fw = screen_img.shape[:2]
        rx, ry, rw, rh = map(int, region)
        rx = max(0, min(fw - 1, rx))
        ry = max(0, min(fh - 1, ry))
        rw = max(1, min(fw - rx, rw))
        rh = max(1, min(fh - ry, rh))
        crop = screen_img[ry:ry + rh, rx:rx + rw]
        # Trim empty horizontal margins on dark-background white-text strips so BiLSTM doesn't waste time on blank space
        if dark_bg and rw > rh * 2:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            bright_cols = np.where(((hsv[:, :, 2] > 180) & (hsv[:, :, 1] < 90)).any(axis=0))[0]
            if bright_cols.size == 0:
                return []
            c0 = max(0, int(bright_cols[0]) - 10)
            c1 = min(rw, int(bright_cols[-1]) + 32)
            if c1 - c0 >= 12:
                crop = crop[:, c0:c1]
                rx += c0
                rw = c1 - c0
        text, conf = engine.recognize_crop(crop, dark_bg=dark_bg)
        text = text.strip(" []|")
        if not text:
            return []
        words = [w for w in text.split() if w]
        if not words:
            return []
        if len(words) == 1:
            return [OcrWordBox(left=rx, top=ry, width=rw, height=rh, text=words[0], confidence=float(conf))]
        total_chars = max(1, sum(len(w) for w in words) + len(words) - 1)
        cursor = 0
        out: List[OcrWordBox] = []
        for w in words:
            w_left = rx + int(round(rw * (cursor / float(total_chars))))
            w_width = max(1, int(round(rw * (len(w) / float(total_chars)))))
            out.append(OcrWordBox(left=w_left, top=ry, width=w_width, height=rh, text=w, confidence=float(conf)))
            cursor += len(w) + 1
        return out
