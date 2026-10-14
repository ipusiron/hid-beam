"""hidbeam_arm の単体テスト。ファイルベースの武装状態を検証する。"""

import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hidbeam_arm


class TestArm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.armed_file = os.path.join(self.tmp.name, "armed")
        # モジュールグローバルをテスト用ディレクトリへ差し替え
        self._orig_run = hidbeam_arm.RUN_DIR
        self._orig_armed = hidbeam_arm.ARMED_FILE
        hidbeam_arm.RUN_DIR = self.tmp.name
        hidbeam_arm.ARMED_FILE = self.armed_file

    def tearDown(self):
        hidbeam_arm.RUN_DIR = self._orig_run
        hidbeam_arm.ARMED_FILE = self._orig_armed
        self.tmp.cleanup()

    def test_arm_writes_future_expiry(self):
        hidbeam_arm.arm(2)
        self.assertTrue(os.path.exists(self.armed_file))
        with open(self.armed_file) as f:
            expiry = float(f.read().strip())
        # 2分後 ± 少々
        self.assertGreater(expiry, time.time() + 60)
        self.assertLess(expiry, time.time() + 2 * 60 + 5)

    def test_arm_file_mode_0644(self):
        hidbeam_arm.arm(1)
        mode = os.stat(self.armed_file).st_mode & 0o777
        # Windows ではパーミッションの意味が異なるため POSIX のみ検証
        # 武装ファイルは期限のみで非機密。root/サービスユーザー双方が読める 0644。
        if os.name == "posix":
            self.assertEqual(mode, 0o644)

    def test_disarm_removes_file(self):
        hidbeam_arm.arm(1)
        hidbeam_arm.disarm()
        self.assertFalse(os.path.exists(self.armed_file))

    def test_disarm_when_not_armed(self):
        # 例外を出さずに完了する
        hidbeam_arm.disarm()
        self.assertFalse(os.path.exists(self.armed_file))

    def test_arm_rejects_zero(self):
        with self.assertRaises(SystemExit):
            hidbeam_arm.arm(0)

    def test_arm_rejects_negative(self):
        with self.assertRaises(SystemExit):
            hidbeam_arm.arm(-5)

    def test_arm_rejects_over_max(self):
        with self.assertRaises(SystemExit):
            hidbeam_arm.arm(hidbeam_arm.MAX_MINUTES + 1)

    def test_arm_at_max_ok(self):
        hidbeam_arm.arm(hidbeam_arm.MAX_MINUTES)
        self.assertTrue(os.path.exists(self.armed_file))


if __name__ == "__main__":
    unittest.main()
