"""hid_core の単体テスト（実機不要）。

安全性の核心は「送信前に全体を検証し、問題があれば1件も打鍵を生成しない」ことと
「上限を越えられない」ことである。ここを重点的に検証する。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hid_core
from hid_core import (
    compile_script, DuckyCompileError, Limits, KeyEvent, DelayEvent,
    char_to_keycode, CHAR_DELAY_MS,
)


class TestCharToKeycode(unittest.TestCase):
    def test_us_lower(self):
        self.assertEqual(char_to_keycode("a", "us"), (0x04, 0))

    def test_us_shift(self):
        kc, mod = char_to_keycode("A", "us")
        self.assertEqual(kc, 0x04)
        self.assertEqual(mod, hid_core.MOD_LSHIFT)

    def test_us_at_is_shift_2(self):
        # US配列では @ は Shift+2
        self.assertEqual(char_to_keycode("@", "us"), (0x1F, hid_core.MOD_LSHIFT))

    def test_jis_at_is_plain(self):
        # JIS配列では @ は単独キー（0x2F）
        self.assertEqual(char_to_keycode("@", "jis"), (0x2F, 0))

    def test_newline_tab(self):
        self.assertEqual(char_to_keycode("\n", "us"), (0x28, 0))
        self.assertEqual(char_to_keycode("\t", "us"), (0x2B, 0))

    def test_unsupported(self):
        kc, mod = char_to_keycode("あ", "us")
        self.assertIsNone(kc)


class TestCompileSuccess(unittest.TestCase):
    def test_simple_string(self):
        c = compile_script("STRING abc", "us")
        self.assertEqual(c.key_event_count, 3)
        self.assertEqual(c.command_count, 1)
        self.assertTrue(all(isinstance(s, KeyEvent) for s in c.steps))

    def test_rem_and_blank_ignored(self):
        c = compile_script("REM comment\n\nSTRING x\nREM\n", "us")
        self.assertEqual(c.key_event_count, 1)
        self.assertEqual(c.command_count, 1)  # STRING のみ

    def test_delay_creates_delay_event(self):
        c = compile_script("DELAY 500\nSTRING a", "us")
        self.assertEqual(c.delay_total_ms, 500)
        self.assertTrue(any(isinstance(s, DelayEvent) and s.ms == 500 for s in c.steps))

    def test_special_keys(self):
        c = compile_script("ENTER\nTAB\nESC", "us")
        self.assertEqual(c.key_event_count, 3)
        self.assertIn("ENTER", c.specials)
        self.assertIn("TAB", c.specials)

    def test_modifier_combo(self):
        c = compile_script("GUI r", "us")
        self.assertEqual(c.key_event_count, 1)
        ev = c.steps[0]
        self.assertTrue(ev.modifier & hid_core.MOD_LGUI)
        self.assertEqual(ev.keycode, 0x15)  # r
        self.assertTrue(c.uses_gui_modifier)
        self.assertIn("GUI+r", c.combos)

    def test_ctrl_alt_delete(self):
        c = compile_script("CTRL ALT DELETE", "us")
        ev = c.steps[0]
        self.assertTrue(ev.modifier & hid_core.MOD_LCTRL)
        self.assertTrue(ev.modifier & hid_core.MOD_LALT)
        self.assertEqual(ev.keycode, hid_core.SPECIAL_KEYS["DELETE"])
        self.assertFalse(c.uses_gui_modifier)

    def test_estimated_duration(self):
        c = compile_script("STRING ab\nDELAY 100", "us")
        # 打鍵2件 * CHAR_DELAY_MS + 100ms
        self.assertEqual(c.estimated_duration_ms, 2 * CHAR_DELAY_MS + 100)

    def test_plan_summary_shape(self):
        c = compile_script("GUI r", "us")
        s = c.plan_summary()
        for k in ("layout", "command_count", "key_event_count",
                  "delay_total_ms", "estimated_duration_ms", "combos",
                  "specials", "uses_gui_modifier"):
            self.assertIn(k, s)


class TestAtomicRejection(unittest.TestCase):
    """部分送信の排除: 問題があれば CompiledScript を返さず raise する。"""

    def test_unsupported_char_raises(self):
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("STRING abcあdef", "us")
        self.assertTrue(any("未対応文字" in e for e in ctx.exception.errors))

    def test_unsupported_char_reports_all_distinct(self):
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("STRING aあbい", "us")
        joined = " ".join(ctx.exception.errors)
        self.assertIn("あ", joined)
        self.assertIn("い", joined)

    def test_unknown_command_raises(self):
        with self.assertRaises(DuckyCompileError):
            compile_script("FOOBAR x", "us")

    def test_unknown_combo_key_raises(self):
        with self.assertRaises(DuckyCompileError):
            compile_script("CTRL あ", "us")

    def test_errors_do_not_leak_steps(self):
        # compile に失敗した場合、呼び出し側は steps を得られない
        try:
            compile_script("STRING ok\nBADCMD", "us")
            self.fail("should raise")
        except DuckyCompileError as e:
            self.assertTrue(len(e.errors) >= 1)


class TestLimits(unittest.TestCase):
    def test_input_too_large(self):
        limits = Limits(max_input_bytes=10)
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("STRING aaaaaaaaaaaaaaaa", "us", limits)
        self.assertTrue(any("大きすぎ" in e for e in ctx.exception.errors))

    def test_too_many_commands(self):
        limits = Limits(max_commands=3)
        script = "\n".join(["ENTER"] * 10)
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script(script, "us", limits)
        self.assertTrue(any("コマンド数" in e for e in ctx.exception.errors))

    def test_string_too_long(self):
        limits = Limits(max_string_len=5)
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("STRING abcdefgh", "us", limits)
        self.assertTrue(any("STRING" in e for e in ctx.exception.errors))

    def test_single_delay_over_limit(self):
        limits = Limits(max_single_delay_ms=1000)
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("DELAY 5000", "us", limits)
        self.assertTrue(any("DELAY" in e for e in ctx.exception.errors))

    def test_negative_delay(self):
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("DELAY -1", "us")
        self.assertTrue(any("負値" in e for e in ctx.exception.errors))

    def test_non_integer_delay(self):
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("DELAY abc", "us")
        self.assertTrue(any("不正" in e for e in ctx.exception.errors))

    def test_total_delay_over_limit(self):
        limits = Limits(max_single_delay_ms=1000, max_total_delay_ms=1500)
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("DELAY 1000\nDELAY 1000", "us", limits)
        self.assertTrue(any("合計" in e for e in ctx.exception.errors))

    def test_key_events_over_limit(self):
        limits = Limits(max_key_events=3, max_string_len=1000)
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("STRING abcdef", "us", limits)
        self.assertTrue(any("打鍵イベント" in e for e in ctx.exception.errors))

    def test_duration_over_limit(self):
        limits = Limits(max_total_duration_ms=50)
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_script("DELAY 100", "us", limits)
        self.assertTrue(any("所要時間" in e for e in ctx.exception.errors))

    def test_unknown_layout(self):
        with self.assertRaises(DuckyCompileError):
            compile_script("STRING a", "dvorak")

    def test_within_limits_ok(self):
        # 既定上限の範囲内は通る
        c = compile_script("STRING hello world\nENTER", "us")
        self.assertGreater(c.key_event_count, 0)


class TestDetailRows(unittest.TestCase):
    def test_key_rows_have_usage_id(self):
        c = compile_script("STRING aA", "us")
        rows, omitted = c.detail_rows()
        self.assertEqual(omitted, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["usage_id"], "0x04")   # a
        self.assertEqual(rows[0]["modifier"], "-")
        self.assertEqual(rows[1]["usage_id"], "0x04")   # A は同じキー＋Shift
        self.assertEqual(rows[1]["modifier"], "SHIFT")

    def test_delay_row(self):
        c = compile_script("DELAY 200", "us")
        rows, _ = c.detail_rows()
        self.assertEqual(rows[0]["kind"], "delay")
        self.assertIn("200", rows[0]["label"])
        self.assertEqual(rows[0]["usage_id"], "-")

    def test_combo_row(self):
        c = compile_script("GUI r", "us")
        rows, _ = c.detail_rows()
        self.assertEqual(rows[0]["usage_id"], "0x15")
        self.assertIn("GUI", rows[0]["modifier"])

    def test_limit_and_omitted(self):
        c = compile_script("STRING " + "a" * 10, "us")
        rows, omitted = c.detail_rows(limit=4)
        self.assertEqual(len(rows), 4)
        self.assertEqual(omitted, 6)


if __name__ == "__main__":
    unittest.main()
