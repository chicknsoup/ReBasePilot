import threading
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app.core.bot import Bot
from app.services.vision import OcrWordBox, VisionService


def _make_cloud_frame(*, pink_error: bool = True, width: int = 1600, height: int = 900) -> np.ndarray:
    frame = np.full((height, width, 3), 245, dtype=np.uint8)
    # Draw bottom-left Return Home button box
    cv2.rectangle(frame, (15, int(height * 0.78)), (int(width * 0.12), int(height * 0.98)), (170, 195, 210), -1)
    if pink_error:
        # Salmon-pink error text band in center (BGR: B=180, G=170, R=245)
        y0, y1 = int(height * 0.52), int(height * 0.56)
        x0, x1 = int(width * 0.28), int(width * 0.72)
        frame[y0:y1, x0:x1] = (180, 170, 245)
    else:
        # Pure white "Searching for opponents..." text band
        y0, y1 = int(height * 0.52), int(height * 0.56)
        x0, x1 = int(width * 0.35), int(width * 0.65)
        frame[y0:y1, x0:x1] = (255, 255, 255)
    return frame


class UnableToAttackCloudTests(unittest.TestCase):
    def _make_bot(self, vision=None):
        bot = Bot.__new__(Bot)
        bot.stop_event = threading.Event()
        bot.window = Mock()
        bot.input = Mock()
        bot.vision = vision or VisionService()
        bot._battle_rewards = Mock()
        bot._battle_rewards.handle.return_value = False
        bot._status_callback = None
        bot._update_config_size = Mock()
        bot._find_reload_button = Mock(return_value=(None, None, None))
        return bot

    def test_ignores_normal_searching_clouds_and_detects_pink_unable_banner(self):
        bot = self._make_bot()
        searching = _make_cloud_frame(pink_error=False)
        self.assertEqual(bot._detect_unable_to_attack_cloud(searching), (None, None))

        error_cloud = _make_cloud_frame(pink_error=True)
        fake_words = [
            OcrWordBox(left=10, top=10, width=80, height=20, text='Unable', confidence=95.0),
            OcrWordBox(left=100, top=10, width=80, height=20, text='attack', confidence=95.0),
        ]
        with patch.object(VisionService, 'find_words_ocr', return_value=fake_words), \
             patch('app.core.bot.find_ui_label', return_value=(102, 845)):
            pt = bot._detect_unable_to_attack_cloud(error_cloud)
        self.assertEqual(pt, (102, 845))

    def test_wait_for_any_image_aborts_immediately_on_unable_to_attack(self):
        bot = self._make_bot(vision=Mock())
        error_cloud = _make_cloud_frame(pink_error=True)
        home_frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        bot.window.screenshot.side_effect = [error_cloud, home_frame]
        bot.vision.find_template.side_effect = lambda frame, tpl, **_kw: (100, 840) if (frame is home_frame and tpl == 'attack.png') else (None, None)

        with patch('app.core.bot.find_ui_label', return_value=(104, 848)), \
             patch.object(bot.stop_event, 'wait', return_value=False):
            res = bot._wait_for_any_image(('endbattle.png', 'surrender.png'), timeout=30, error=False)

        self.assertEqual(res, (None, None))
        self.assertTrue(bot._cloud_attack_aborted)
        bot.input.click.assert_called_once_with(104, 848, pause=0.5, rand=False)

    def test_wait_for_next_base_returns_cloud_abort_when_unable_to_attack_appears(self):
        bot = self._make_bot(vision=Mock())
        prev_frame = np.full((900, 1600, 3), 50, dtype=np.uint8)
        error_cloud = _make_cloud_frame(pink_error=True)
        home_frame = np.zeros((900, 1600, 3), dtype=np.uint8)

        # Poll 1 sees battle_prep cleared (cloud screen); then _wait_for_any_image sees error_cloud -> clicks Return Home
        bot.window.screenshot.side_effect = [error_cloud, error_cloud, home_frame]
        bot._battle_prep_visible = Mock(return_value=False)
        bot.vision.find_template.side_effect = lambda frame, tpl, **_kw: (100, 840) if (frame is home_frame and tpl == 'attack.png') else (None, None)

        with patch('app.core.bot.find_ui_label', return_value=(104, 848)), \
             patch.object(bot.stop_event, 'wait', return_value=False):
            status = bot._wait_for_next_base(prev_frame=prev_frame, prev_loot=(200000, 200000, 0))

        self.assertEqual(status, 'cloud_abort')
        bot.input.click.assert_called_once_with(104, 848, pause=0.5, rand=False)


if __name__ == '__main__':
    unittest.main()
