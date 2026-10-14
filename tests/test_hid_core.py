"""hid_core の単体テスト（実機不要）。

安全性の核心は「送信前に全体を検証し、問題があれば1件も打鍵を生成しない」ことと
「上限を越えられない」ことである。ここを重点的に検証する。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hid_core
import base64 as _b64

from hid_core import (
    compile_script, compile_payload, syllable_fingerprint, build_file_inject,
    predict_display, LAYOUT_DIAG_TEXT, DuckyCompileError, Limits, KeyEvent,
    DelayEvent, char_to_keycode, CHAR_DELAY_MS, _FP_WORDS,
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


class TestFingerprint(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(syllable_fingerprint("hello"), syllable_fingerprint("hello"))

    def test_structure(self):
        fp = syllable_fingerprint("payload")
        self.assertEqual(len(fp["sha256"]), 64)
        self.assertEqual(fp["short"], fp["sha256"][:8])
        toks = fp["words"].split("-")
        self.assertEqual(len(toks), 4)
        for w in toks:
            self.assertIn(w, _FP_WORDS)

    def test_known_vector_empty(self):
        fp = syllable_fingerprint("")
        self.assertEqual(fp["short"], "e3b0c442")
        self.assertEqual(fp["words"], "lion-mint-arch-aura")

    def test_different_text_differs(self):
        self.assertNotEqual(syllable_fingerprint("a")["words"],
                            syllable_fingerprint("b")["words"])

    def test_no_offensive_tokens(self):
        # 語彙に不快語が無いことを明示的に固定する
        for bad in ("fag", "nig", "ass", "cum", "jew"):
            self.assertNotIn(bad, _FP_WORDS)


class TestCompilePayload(unittest.TestCase):
    def test_literal_chars(self):
        c = compile_payload("abc", "us")
        self.assertTrue(c.is_payload)
        self.assertEqual(c.key_event_count, 3)
        self.assertIsNotNone(c.fingerprint)

    def test_no_command_parsing(self):
        # "STRING x" はコマンドではなく 8 文字として逐語入力される
        c = compile_payload("STRING x", "us")
        self.assertEqual(c.key_event_count, 8)

    def test_newline_becomes_enter(self):
        c = compile_payload("a\nb", "us")
        self.assertEqual(c.key_event_count, 3)
        self.assertEqual(c.steps[1].keycode, 0x28)  # ENTER

    def test_crlf_normalized(self):
        a = compile_payload("a\r\nb", "us")
        b = compile_payload("a\nb", "us")
        self.assertEqual(a.key_event_count, b.key_event_count)
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_fingerprint_is_of_normalized_text(self):
        c = compile_payload("x\r\ny", "us")
        self.assertEqual(c.fingerprint, syllable_fingerprint("x\ny"))

    def test_unsupported_char_rejected_atomically(self):
        with self.assertRaises(DuckyCompileError) as ctx:
            compile_payload("abcあ", "us")
        self.assertTrue(any("入力できない" in e for e in ctx.exception.errors))

    def test_input_too_large(self):
        with self.assertRaises(DuckyCompileError):
            compile_payload("abcd", "us", Limits(max_input_bytes=2))


class TestFileInject(unittest.TestCase):
    def test_generates_decodable_script(self):
        r = build_file_inject("hello", "out.txt")
        self.assertIn("base64 -d > 'out.txt'", r["script"])
        self.assertIn(_b64.b64encode(b"hello").decode(), r["script"])
        self.assertIn("sha256sum 'out.txt'", r["script"])
        self.assertEqual(r["bytes"], 5)
        self.assertEqual(r["fingerprint"], syllable_fingerprint("hello"))

    def test_base64_roundtrips(self):
        content = "key=VALUE\nhost=10.0.0.1\n"
        r = build_file_inject(content, "conf")
        body = r["script"].split("<<'HIDBEAM_EOF'\n", 1)[1].split("\nHIDBEAM_EOF", 1)[0]
        decoded = _b64.b64decode(body.replace("\n", "")).decode()
        self.assertEqual(decoded, content)

    def test_crlf_normalized(self):
        r = build_file_inject("a\r\nb", "f")
        self.assertEqual(r["fingerprint"], syllable_fingerprint("a\nb"))
        body = r["script"].split("<<'HIDBEAM_EOF'\n", 1)[1].split("\nHIDBEAM_EOF", 1)[0]
        self.assertEqual(_b64.b64decode(body.replace("\n", "")), b"a\nb")

    def test_bad_filename_rejected(self):
        for bad in ("a'b", "a b", "../x", "", "a;b", "a$b"):
            with self.assertRaises(DuckyCompileError):
                build_file_inject("x", bad)

    def test_safe_filename_ok(self):
        r = build_file_inject("x", "dir/sub_file-1.txt")
        self.assertIn("'dir/sub_file-1.txt'", r["script"])

    def test_wrapping(self):
        r = build_file_inject("A" * 300, "f", wrap=76)
        body = r["script"].split("<<'HIDBEAM_EOF'\n", 1)[1].split("\nHIDBEAM_EOF", 1)[0]
        for line in body.split("\n"):
            self.assertLessEqual(len(line), 76)


class TestLayoutDiag(unittest.TestCase):
    def test_diag_typeable_in_both_layouts(self):
        for lay in ("us", "jis"):
            c = compile_payload(LAYOUT_DIAG_TEXT, lay)
            self.assertEqual(c.key_event_count, len(LAYOUT_DIAG_TEXT))


class TestFileInjectHex(unittest.TestCase):
    def test_hex_encoding(self):
        r = build_file_inject("hi", "f.bin", encoding="hex")
        self.assertIn("xxd -r -p > 'f.bin'", r["script"])
        self.assertIn(b"hi".hex(), r["script"])  # "6869"
        self.assertEqual(r["encoding"], "hex")

    def test_hex_roundtrips(self):
        content = "data=1\n"
        r = build_file_inject(content, "f", encoding="hex")
        body = r["script"].split("<<'HIDBEAM_EOF'\n", 1)[1].split("\nHIDBEAM_EOF", 1)[0]
        self.assertEqual(bytes.fromhex(body.replace("\n", "")).decode(), content)

    def test_hex_only_typeable_chars(self):
        # hex は 0-9a-f のみ。生成スクリプト全体が US でも JIS でも打てる
        r = build_file_inject("hello world", "f", encoding="hex")
        for lay in ("us", "jis"):
            compile_payload(r["script"], lay)  # 未対応文字があれば raise

    def test_unknown_encoding_rejected(self):
        with self.assertRaises(DuckyCompileError):
            build_file_inject("x", "f", encoding="rot13")


class TestUKLayout(unittest.TestCase):
    def test_at_quote_swap(self):
        # UK: @ は Shift+'(0x34)、" は Shift+2(0x1F)
        self.assertEqual(char_to_keycode("@", "uk"), (0x34, hid_core.MOD_LSHIFT))
        self.assertEqual(char_to_keycode('"', "uk"), (0x1F, hid_core.MOD_LSHIFT))

    def test_hash_and_tilde(self):
        self.assertEqual(char_to_keycode("#", "uk"), (0x32, 0))
        self.assertEqual(char_to_keycode("~", "uk"), (0x32, hid_core.MOD_LSHIFT))

    def test_backslash_pipe(self):
        self.assertEqual(char_to_keycode("\\", "uk"), (0x64, 0))
        self.assertEqual(char_to_keycode("|", "uk"), (0x64, hid_core.MOD_LSHIFT))

    def test_letters_same_as_us(self):
        for ch in "aZ5 ":
            self.assertEqual(char_to_keycode(ch, "uk"), char_to_keycode(ch, "us"))

    def test_diag_typeable_in_uk(self):
        c = compile_payload(LAYOUT_DIAG_TEXT, "uk")
        self.assertEqual(c.key_event_count, len(LAYOUT_DIAG_TEXT))

    def test_predict_us_at_on_uk(self):
        self.assertEqual(predict_display("@", "us", "uk"), '"')
        self.assertEqual(predict_display('"', "us", "uk"), "@")


class TestPredictDisplay(unittest.TestCase):
    def test_us_at_shows_as_doublequote_on_jis(self):
        # US で "@"(Shift+2) を打つと、実JISでは同じ Shift+2 位置の '"' に見える
        self.assertEqual(predict_display("@", "us", "jis"), '"')

    def test_letters_layout_independent(self):
        self.assertEqual(predict_display("Hello", "us", "jis"), "Hello")

    def test_same_layout_identity(self):
        self.assertEqual(predict_display(LAYOUT_DIAG_TEXT, "us", "us"), LAYOUT_DIAG_TEXT)

    def test_diag_differs_across_layouts(self):
        self.assertNotEqual(predict_display(LAYOUT_DIAG_TEXT, "us", "jis"), LAYOUT_DIAG_TEXT)

    def test_space_preserved(self):
        self.assertEqual(predict_display("a b", "us", "jis"), "a b")


if __name__ == "__main__":
    unittest.main()
