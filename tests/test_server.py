"""wifi_hid_bridge のセキュリティゲートを Flask test_client で検証する。

実機HIDは使わず、_hid_file を差し替えて送信バイトを捕捉する。ここで検証するのは
「認証・武装・プレビュー結合・Host/Origin・頻度」の各ゲートが、いずれか1つでも
欠ければ1バイトも送信されないことである。
"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# import 前に環境変数を設定（モジュール定数は import 時に確定する）
_TMP = tempfile.TemporaryDirectory()
os.environ["HIDBEAM_TOKEN"] = "testtoken"
os.environ["HIDBEAM_RUN_DIR"] = _TMP.name
os.environ["HIDBEAM_REQUIRE_ARM"] = "1"
os.environ["HIDBEAM_ALLOWED_HOSTS"] = "localhost"

import wifi_hid_bridge as srv  # noqa: E402


class FakeHID:
    """送信された8バイトレポートを捕捉するダミーデバイス。"""
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))


def _nonce_from(html):
    m = re.search(r'name="nonce" value="([^"]+)"', html)
    return m.group(1) if m else None


class ServerTestBase(unittest.TestCase):
    def setUp(self):
        srv.app.config["TESTING"] = True
        srv.app.secret_key = "test-secret"
        srv.ACCESS_TOKEN = "testtoken"
        # 既定はフェイルクローズだが、test_client は Origin を付けないため
        # 大半のテストでは緩める（Origin検証は TestOriginGate で明示的に検証する）。
        srv.REQUIRE_ORIGIN = False
        # 共有状態をリセット
        srv._exec_times.clear()
        srv._auth_fail_times.clear()
        srv._nonces.clear()
        srv.disarm()
        # 既定はHID利用不可。必要なテストだけ有効化する
        srv._hid_available = False
        srv._hid_file = None
        self.client = srv.app.test_client()

    def _login(self):
        r = self.client.post("/auth", data={"token": "testtoken"})
        self.assertEqual(r.status_code, 302)  # redirect on success

    def _arm(self, minutes=2):
        import time
        with open(srv.ARMED_FILE, "w") as f:
            f.write(str(time.time() + minutes * 60))


class TestAuthGate(ServerTestBase):
    def test_index_shows_token_form_when_unauthed(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="token"', r.get_data(as_text=True))

    def test_preview_requires_auth(self):
        r = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
        self.assertEqual(r.status_code, 403)

    def test_execute_requires_auth(self):
        r = self.client.post("/execute", data={"script": "STRING a", "layout": "us", "nonce": "x"})
        self.assertEqual(r.status_code, 403)

    def test_bad_token_rejected(self):
        r = self.client.post("/auth", data={"token": "wrong"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("一致しません", r.get_data(as_text=True))

    def test_lockout_after_repeated_failures(self):
        for _ in range(srv.MAX_AUTH_FAILS):
            self.client.post("/auth", data={"token": "wrong"})
        r = self.client.post("/auth", data={"token": "testtoken"})
        self.assertIn("多すぎます", r.get_data(as_text=True))


class TestHostGate(ServerTestBase):
    def test_bad_host_rejected(self):
        r = self.client.get("/", headers={"Host": "evil.example"})
        self.assertEqual(r.status_code, 400)

    def test_allowed_host_ok(self):
        r = self.client.get("/", headers={"Host": "localhost"})
        self.assertEqual(r.status_code, 200)


class TestArmGate(ServerTestBase):
    def test_execute_blocked_when_disarmed(self):
        self._login()
        # 武装チェックはノンスより先に行われるため、武装解除中は
        # どんなノンスでも「武装」メッセージで弾かれる（UIはノンス欄自体を出さない）
        r = self.client.post("/execute", data={"script": "STRING a", "layout": "us", "nonce": "anything"})
        self.assertIn("武装", r.get_data(as_text=True))

    def test_preview_shows_no_send_button_when_disarmed(self):
        self._login()
        pv = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
        html = pv.get_data(as_text=True)
        # 武装解除中は送信フォームを出さない
        self.assertNotIn('action="/execute"', html)


class TestNonceBinding(ServerTestBase):
    def test_execute_rejects_missing_nonce(self):
        self._login()
        self._arm()
        r = self.client.post("/execute", data={"nonce": "bogus"})
        self.assertIn("有効期限", r.get_data(as_text=True))

    def test_nonce_is_single_use(self):
        self._login()
        self._arm()
        srv._hid_available = True
        srv._hid_file = FakeHID()
        pv = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        r1 = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("送信完了", r1.get_data(as_text=True))
        # 同じノンスは二度使えない
        r2 = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("有効期限", r2.get_data(as_text=True))

    def test_execute_uses_previewed_script_not_posted(self):
        # 実行対象はプレビューした内容そのもの。送信値の別スクリプトは無視される。
        self._login()
        self._arm()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        r = self.client.post("/execute", data={"script": "STRING ZZZZZ", "layout": "us", "nonce": nonce})
        self.assertIn("送信完了", r.get_data(as_text=True))
        # "a" 1キー = press/release + finally release = 3 writes（5文字なら11になるはず）
        self.assertEqual(len(fake.writes), 3)

    def test_multiline_script_roundtrips(self):
        # 複数行スクリプト（CRLF往復でダイジェストがずれていた回帰の防止）
        self._login()
        self._arm()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={"script": "DELAY 10\r\nSTRING ab\r\nENTER", "layout": "us"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        self.assertIsNotNone(nonce)
        r = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("送信完了", r.get_data(as_text=True))
        self.assertGreater(len(fake.writes), 0)


class TestExecuteSendsBytes(ServerTestBase):
    def test_full_flow_writes_hid_reports(self):
        self._login()
        self._arm()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={"script": "STRING ab", "layout": "us"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        r = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("送信完了", r.get_data(as_text=True))
        # 2文字 → press/release が各2回 + finally release。全て8バイト。
        self.assertGreaterEqual(len(fake.writes), 4)
        self.assertTrue(all(len(w) == 8 for w in fake.writes))

    def test_no_bytes_without_arm(self):
        self._login()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={"script": "STRING ab", "layout": "us"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        self.client.post("/execute", data={"nonce": nonce})
        self.assertEqual(len(fake.writes), 0)  # 武装していないので1バイトも出ない

    def test_preview_shows_usage_id_detail(self):
        self._login()
        pv = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
        html = pv.get_data(as_text=True)
        self.assertIn("Usage ID", html)
        self.assertIn("0x04", html)  # 'a' の HID Usage ID

    def test_invalid_script_preview_rejected_no_nonce(self):
        self._login()
        pv = self.client.post("/preview", data={"script": "STRING aあb", "layout": "us"})
        html = pv.get_data(as_text=True)
        self.assertIn("未対応文字", html)
        self.assertIsNone(_nonce_from(html))  # 不正スクリプトにノンスは発行されない


class TestRateLimit(ServerTestBase):
    def test_exec_rate_limited(self):
        self._login()
        self._arm(minutes=5)
        srv._hid_available = True
        srv._hid_file = FakeHID()
        # 上限まで送信
        for _ in range(srv.MAX_EXEC_PER_MIN):
            pv = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
            nonce = _nonce_from(pv.get_data(as_text=True))
            self.client.post("/execute", data={"nonce": nonce})
        # 次は頻度制限で弾かれる
        pv = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        r = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("多すぎ", r.get_data(as_text=True))


class TestOriginGate(ServerTestBase):
    def setUp(self):
        super().setUp()
        srv.REQUIRE_ORIGIN = True  # フェイルクローズを明示的に検証する

    def test_no_origin_rejected_when_required(self):
        self._login()
        r = self.client.post("/preview", data={"script": "STRING a", "layout": "us"})
        self.assertEqual(r.status_code, 403)

    def test_bad_origin_rejected(self):
        self._login()
        r = self.client.post("/preview", data={"script": "STRING a", "layout": "us"},
                             headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_good_origin_ok(self):
        self._login()
        r = self.client.post("/preview", data={"script": "STRING a", "layout": "us"},
                             headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 200)


class TestPayloadAndHeaders(ServerTestBase):
    def test_413_on_oversized_body(self):
        self._login()
        big = "A" * (srv.MAX_CONTENT_BYTES + 100)
        r = self.client.post("/preview", data={"script": big, "layout": "us"})
        self.assertEqual(r.status_code, 413)

    def test_security_headers_present(self):
        r = self.client.get("/")
        csp = r.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'none'", csp)   # JS無し: scriptはdefault-srcで封じる
        self.assertIn("form-action 'self'", csp)
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")


class TestPayloadMode(ServerTestBase):
    def test_preview_payload_shows_fingerprint(self):
        self._login()
        pv = self.client.post("/preview", data={"script": "secret-data", "layout": "us", "mode": "payload"})
        html = pv.get_data(as_text=True)
        self.assertIn("フィンガープリント", html)
        self.assertIn(srv.hid_core.syllable_fingerprint("secret-data")["short"], html)

    def test_payload_execute_types_data(self):
        self._login()
        self._arm()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={"script": "ab", "layout": "us", "mode": "payload"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        r = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("送信完了", r.get_data(as_text=True))
        self.assertGreaterEqual(len(fake.writes), 2)

    def test_payload_rejects_unsupported(self):
        self._login()
        pv = self.client.post("/preview", data={"script": "aあb", "layout": "us", "mode": "payload"})
        html = pv.get_data(as_text=True)
        self.assertIn("入力できない", html)
        self.assertIsNone(_nonce_from(html))

    def test_payload_not_parsed_as_ducky(self):
        # payload モードでは "ENTER" はコマンドでなく 5 文字として扱う（送信バイトで確認）
        self._login()
        self._arm()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={"script": "ENTER", "layout": "us", "mode": "payload"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        self.client.post("/execute", data={"nonce": nonce})
        # 5文字 → press/release 各5 + finally release = 11 writes（ENTERキー1発なら3）
        self.assertEqual(len(fake.writes), 11)


class TestFileInjectMode(ServerTestBase):
    def test_file_preview_shows_file_fingerprint(self):
        self._login()
        self._arm()  # 送信フォーム（ノンス欄）は武装時のみ描画される
        content = "key=1\nhost=2\n"
        pv = self.client.post("/preview", data={
            "script": content, "layout": "us", "mode": "file", "filename": "app.conf"})
        html = pv.get_data(as_text=True)
        fp = srv.hid_core.syllable_fingerprint(content)
        self.assertIn(fp["short"], html)
        self.assertIsNotNone(_nonce_from(html))

    def test_file_bad_filename_rejected(self):
        self._login()
        pv = self.client.post("/preview", data={
            "script": "x", "layout": "us", "mode": "file", "filename": "a b"})
        html = pv.get_data(as_text=True)
        self.assertIn("ファイル名", html)
        self.assertIsNone(_nonce_from(html))

    def test_file_execute_types_generated_script(self):
        self._login()
        self._arm()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={
            "script": "hi", "layout": "us", "mode": "file", "filename": "f.txt"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        r = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("送信完了", r.get_data(as_text=True))
        self.assertGreater(len(fake.writes), 10)  # 生成スクリプトはそれなりに長い

    def test_file_hex_mode_typeable_in_jis(self):
        # hex は 0-9a-f のみ。JIS 選択でもプレビューが通り、ノンスが発行される
        self._login()
        self._arm()
        fake = FakeHID()
        srv._hid_available = True
        srv._hid_file = fake
        pv = self.client.post("/preview", data={
            "script": "hi", "layout": "jis", "mode": "file",
            "filename": "f.bin", "encoding": "hex"})
        nonce = _nonce_from(pv.get_data(as_text=True))
        self.assertIsNotNone(nonce)
        r = self.client.post("/execute", data={"nonce": nonce})
        self.assertIn("送信完了", r.get_data(as_text=True))


class TestLayoutTest(ServerTestBase):
    def test_layout_test_renders_diag(self):
        self._login()
        self._arm()  # 送信フォーム（ノンス欄）は武装時のみ描画される
        r = self.client.get("/layout-test?layout=us")
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("診断文字列", html)       # diag ノート固有（リンク文言ではない）
        self.assertIn("layout check", html)      # 診断文字列の先頭（エスケープされない部分）
        self.assertIsNotNone(_nonce_from(html))

    def test_layout_test_requires_auth(self):
        r = self.client.get("/layout-test?layout=us")
        self.assertEqual(r.status_code, 403)

    def test_layout_test_shows_mismatch_prediction(self):
        self._login()
        r = self.client.get("/layout-test?layout=us")
        html = r.get_data(as_text=True)
        self.assertIn("実は jis", html)   # 不一致予測の対象レイアウト
        self.assertIn("配列なら", html)


if __name__ == "__main__":
    unittest.main()
