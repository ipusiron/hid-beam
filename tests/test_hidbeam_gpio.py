"""hidbeam_gpio のコールバック単体テスト。

GPIO配線部分（RPi.GPIO）は実機依存のためテストしない。ここでは、ボタン押下の
コールバックが hidbeam_arm の arm/disarm を正しく呼ぶこと、arm失敗でデーモンを
落とさないことを検証する。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hidbeam_arm
import hidbeam_gpio


class TestGpioCallbacks(unittest.TestCase):
    def test_on_arm_calls_arm_with_configured_minutes(self):
        calls = []
        orig = hidbeam_arm.arm
        hidbeam_arm.arm = lambda m: calls.append(m)
        try:
            hidbeam_gpio._on_arm(hidbeam_gpio.ARM_PIN)
        finally:
            hidbeam_arm.arm = orig
        self.assertEqual(calls, [hidbeam_gpio.ARM_MIN])

    def test_on_abort_calls_disarm(self):
        called = []
        orig = hidbeam_arm.disarm
        hidbeam_arm.disarm = lambda: called.append(True)
        try:
            hidbeam_gpio._on_abort(hidbeam_gpio.ABORT_PIN)
        finally:
            hidbeam_arm.disarm = orig
        self.assertTrue(called)

    def test_on_arm_swallows_systemexit(self):
        # arm() は不正値で SystemExit する。コールバックはそれを外へ出さない。
        orig = hidbeam_arm.arm

        def boom(m):
            raise SystemExit("bad")

        hidbeam_arm.arm = boom
        try:
            hidbeam_gpio._on_arm(1)  # 例外が外に漏れないこと
        finally:
            hidbeam_arm.arm = orig


if __name__ == "__main__":
    unittest.main()
