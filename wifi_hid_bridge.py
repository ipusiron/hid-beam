#!/usr/bin/env python3
"""Wi-Fi HID Bridge - 『エアギャップ・ブリッジ 隔離環境のデータ入力技法』第7章

Raspberry Pi Zero W上で動作する自作DuckyScriptインタープリター。
ブラウザーから受け取ったDuckyScript 1.0サブセットを hid_core で解析・検証し、
検証を通った打鍵計画だけをUSB HIDキーボードとして送信する。

本ファイルはHID書き込みとWeb UIを担う。解析・検証・上限はすべて hid_core 側にあり、
送信前にスクリプト全体を検証するため、部分送信（途中まで打ってから失敗）は起こらない。

== 信頼境界について（重要） ==
キーボード入力は、接続先での任意コマンド実行やデータ操作につながり得る。したがって
本ツールが「安全」であることは証明できない。本ツールは次の多層防御で、意図しない／
無断の送信を起こりにくくするだけである。

 1. APインターフェース限定で待ち受ける（0.0.0.0では待ち受けない）。
 2. 起動ごとのトークンを知る者だけが操作できる（AP参加だけでは不可）。
 3. 既定は武装解除。Pi上でローカルに arm しない限り送信しない（hidbeam_arm.py）。
 4. 送信は必ず「プレビュー → 確認」の2段階。単発リクエストでは送信されない。
 5. Origin/Host検証、入力サイズ・頻度・総量の上限。
 6. 監査ログには結果の要約のみを残し、入力文字列・トークンは保存しない。

起動（Pi上、専用ユーザーで）：
  python3 wifi_hid_bridge.py
  → トークンは journal（systemd）と $HIDBEAM_RUN_DIR/token に出る
  → 送信前に Pi 上で:  python3 hidbeam_arm.py --minutes 2
  → ブラウザーで http://192.168.100.1:8080/ を開き、トークンを入力して操作する
"""

import os
import sys
import json
import time
import hmac
import secrets
import threading
from collections import deque
from datetime import datetime

from flask import (
    Flask, request, session, redirect, url_for, render_template_string, abort
)

import hid_core
from hid_core import compile_script, compile_payload, DuckyCompileError, DEFAULT_LIMITS


# ---------- 設定（環境変数で上書き可能。既定は安全側） ----------

def _env(name, default):
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


HID_DEVICE = _env("HIDBEAM_HID_DEVICE", "/dev/hidg0")
# 既定はAPインターフェースのIPのみ。0.0.0.0（全インターフェース）は使わない。
LISTEN_HOST = _env("HIDBEAM_LISTEN_HOST", "192.168.100.1")
LISTEN_PORT = int(_env("HIDBEAM_LISTEN_PORT", "8080"))
# Hostヘッダー許可リスト（DNSリバインディング対策）。ホスト名でのアクセスは拒否する。
_default_hosts = f"{LISTEN_HOST}:{LISTEN_PORT},{LISTEN_HOST}"
ALLOWED_HOSTS = {h.strip() for h in _env("HIDBEAM_ALLOWED_HOSTS", _default_hosts).split(",") if h.strip()}
# 実行時の武装を必須とするか（既定: 必須）。
REQUIRE_ARM = _env("HIDBEAM_REQUIRE_ARM", "1") != "0"
# 状態変更POSTでOrigin/Refererが無い場合に拒否するか（既定: 拒否＝フェイルクローズ）。
# curl等での検証時のみ HIDBEAM_REQUIRE_ORIGIN=0 で緩められる。
REQUIRE_ORIGIN = _env("HIDBEAM_REQUIRE_ORIGIN", "1") != "0"
# per-bootの状態を置くディレクトリ（Piでは tmpfs の /run を想定）。
RUN_DIR = _env("HIDBEAM_RUN_DIR", "/run/wifi-hid-bridge")
TOKEN_FILE = os.path.join(RUN_DIR, "token")
ARMED_FILE = os.path.join(RUN_DIR, "armed")
# 監査ログ（結果の要約のみ。内容・トークンは残さない）。
AUDIT_LOG = _env("HIDBEAM_AUDIT_LOG", os.path.join(RUN_DIR, "audit.log"))

CHAR_DELAY_SEC = hid_core.CHAR_DELAY_MS / 1000.0

# 頻度・試行回数の上限
MAX_EXEC_PER_MIN = int(_env("HIDBEAM_MAX_EXEC_PER_MIN", "12"))
MAX_AUTH_FAILS = int(_env("HIDBEAM_MAX_AUTH_FAILS", "5"))
AUTH_LOCKOUT_SEC = int(_env("HIDBEAM_AUTH_LOCKOUT_SEC", "60"))
# リクエストボディの上限（hid_coreの入力上限＋フォーム余白）
MAX_CONTENT_BYTES = DEFAULT_LIMITS.max_input_bytes + 4096
# プレビューで発行する確認ノンスの有効期間
PREVIEW_TTL_SEC = 120


# ---------- トークン（起動ごと） ----------

def _init_token():
    """env指定があればそれを、なければ生成してRUN_DIR/tokenへ0600で書き出す。

    トークンはAP参加だけでは得られない秘密として機能する。運用者はPiの
    journal か token ファイルから取得してブラウザーに入力する。
    """
    env_token = os.environ.get("HIDBEAM_TOKEN")
    if env_token:
        return env_token
    token = secrets.token_urlsafe(32)
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
    except OSError as e:
        # 書き出せなくても起動は続ける（journalには出す）
        sys.stderr.write(f"[hidbeam] token file 書き出し失敗: {e}\n")
    return token


ACCESS_TOKEN = None  # main()で確定


def _check_token(supplied):
    if not ACCESS_TOKEN or not supplied:
        return False
    return hmac.compare_digest(str(supplied), str(ACCESS_TOKEN))


# ---------- 武装状態 ----------

def armed_until():
    """arm期限のepoch秒を返す。武装していなければNone。"""
    if not REQUIRE_ARM:
        return float("inf")
    try:
        with open(ARMED_FILE) as f:
            expiry = float(f.read().strip())
    except (OSError, ValueError):
        return None
    if expiry <= time.time():
        return None
    return expiry


def is_armed():
    return armed_until() is not None


def disarm():
    try:
        os.remove(ARMED_FILE)
    except OSError:
        pass


# ---------- Origin / Host 検証 ----------

def _host_ok():
    return request.host in ALLOWED_HOSTS


def _origin_ok():
    """状態変更リクエストのCSRF対策。Originがあれば許可ホストと一致必須。
    Origin も Referer も無い場合は、既定でフェイルクローズ（REQUIRE_ORIGIN）。"""
    origin = request.headers.get("Origin")
    if origin is not None:
        return any(origin == f"http://{h}" for h in ALLOWED_HOSTS)
    ref = request.headers.get("Referer", "")
    if ref:
        return any(ref.startswith(f"http://{h}/") or ref == f"http://{h}" for h in ALLOWED_HOSTS)
    return not REQUIRE_ORIGIN


# ---------- レート制限 / ロックアウト ----------

_exec_times = deque()
_auth_fail_times = deque()
_rate_lock = threading.Lock()


def _rate_allow_exec():
    now = time.time()
    with _rate_lock:
        while _exec_times and now - _exec_times[0] > 60:
            _exec_times.popleft()
        if len(_exec_times) >= MAX_EXEC_PER_MIN:
            return False
        _exec_times.append(now)
        return True


def _auth_locked():
    now = time.time()
    with _rate_lock:
        while _auth_fail_times and now - _auth_fail_times[0] > AUTH_LOCKOUT_SEC:
            _auth_fail_times.popleft()
        return len(_auth_fail_times) >= MAX_AUTH_FAILS


def _record_auth_fail():
    with _rate_lock:
        _auth_fail_times.append(time.time())


# ---------- 監査ログ（内容を残さない） ----------

def audit(event, **fields):
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        "remote": request.remote_addr if request else None,
    }
    # 入力文字列・トークンは意図的に含めない（CLAUDE.md ルール12準拠）
    rec.update(fields)
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------- プレビュー確認ノンス（2段階実行の結合） ----------
# プレビューしたスクリプトをサーバー側に保持し、実行はノンスだけで引き当てる。
# スクリプト本文をHTMLの隠しフィールドで往復させないため、改行(CRLF)正規化で
# 内容が変わる問題が起きず、クライアントによる改ざんもできない。

_nonces = {}  # nonce -> (script, layout, mode, expiry)
_nonce_lock = threading.Lock()


def _compile_for_mode(script, layout, mode):
    """入力モードに応じてコンパイルする。payload=生データ逐語投入、それ以外=DuckyScript。"""
    if mode == "payload":
        return compile_payload(script, layout)
    return compile_script(script, layout)


def _issue_nonce(script, layout, mode):
    now = time.time()
    nonce = secrets.token_urlsafe(16)
    with _nonce_lock:
        # 期限切れを掃除
        for k in [k for k, v in _nonces.items() if v[3] <= now]:
            del _nonces[k]
        _nonces[nonce] = (script, layout, mode, now + PREVIEW_TTL_SEC)
    return nonce


def _consume_nonce(nonce):
    """ノンスを単回で引き当て、(script, layout, mode) を返す。無効ならNone。"""
    now = time.time()
    with _nonce_lock:
        item = _nonces.pop(nonce, None)
    if not item:
        return None
    script, layout, mode, expiry = item
    if expiry <= now:
        return None
    return (script, layout, mode)


# ---------- HIDレポート送信 ----------
# 標準ブートキーボードレポートは8バイト固定：
#   [modifier, reserved, key1, key2, key3, key4, key5, key6]

RELEASE_REPORT = bytes(8)

_hid_lock = threading.Lock()
_hid_file = None
_hid_available = False


def _open_hid_device():
    global _hid_file, _hid_available
    try:
        _hid_file = open(HID_DEVICE, "rb+", buffering=0)
        _hid_available = True
    except OSError as e:
        _hid_file = None
        _hid_available = False
        sys.stderr.write(f"[hidbeam] HIDデバイスを開けません: {e}\n")


def _build_report(modifier=0, keycode=0):
    return bytes([modifier, 0, keycode, 0, 0, 0, 0, 0])


def _write_report(report):
    _hid_file.write(report)


def execute_compiled(compiled):
    """検証済みの CompiledScript を実行する。失敗時も必ずキーを離す。

    返り値: エラーメッセージのリスト（空なら成功）。
    """
    if not _hid_available or _hid_file is None:
        return ["HIDデバイスが使用できません（ガジェット未構成、または権限不足）"]
    errors = []
    with _hid_lock:
        try:
            for step in compiled.steps:
                if isinstance(step, hid_core.KeyEvent):
                    _write_report(_build_report(step.modifier, step.keycode))
                    _write_report(RELEASE_REPORT)
                    time.sleep(CHAR_DELAY_SEC)
                elif isinstance(step, hid_core.DelayEvent):
                    time.sleep(step.ms / 1000.0)
        except (OSError, PermissionError) as e:
            errors.append(f"HIDデバイス書き込み失敗: {e}")
        finally:
            # 異常終了でもキーを押しっぱなしにしない
            try:
                _write_report(RELEASE_REPORT)
            except OSError:
                pass
    return errors


# ---------- 文言（日英） ----------

STRINGS = {
    "ja": {
        "title": "Wi-Fi HID Bridge",
        "lang_other": "English",
        "token_prompt": "アクセストークンを入力してください（Piのjournalまたはtokenファイルに表示）",
        "token_label": "トークン",
        "token_submit": "認証",
        "token_bad": "トークンが一致しません。",
        "token_locked": "認証試行が多すぎます。しばらく待ってから再試行してください。",
        "logout": "ログアウト",
        "armed": "武装中（{ts}まで）",
        "disarmed": "武装解除（Piで hidbeam_arm.py を実行して武装してください）",
        "hw_ok": "HIDデバイス: 利用可能",
        "hw_ng": "HIDデバイス: 未検出（送信できません）",
        "hint": "対応コマンド: STRING / ENTER / TAB / DELAY / GUI・CTRL・ALT・SHIFT修飾キー組合せ / REM",
        "layout": "接続先キーレイアウト",
        "preview_btn": "プレビュー（送信しない）",
        "send_btn": "この内容で送信",
        "disarm_btn": "武装解除（中止）",
        "plan_head": "打鍵プレビュー（まだ送信していません）",
        "plan_commands": "コマンド数",
        "plan_keys": "打鍵イベント数",
        "plan_duration": "推定所要時間",
        "plan_combos": "修飾キー組合せ",
        "plan_specials": "特殊キー",
        "plan_gui_warn": "GUI（Windows/Command）修飾キーを含みます。ランチャー起動などに注意してください。",
        "detail_head": "打鍵の内訳（HID Usage ID）",
        "col_step": "操作",
        "col_usage": "Usage ID",
        "col_mod": "修飾キー",
        "detail_more": "ほか {n} 件（表示は先頭のみ）",
        "mode_label": "入力モード",
        "mode_ducky": "DuckyScript",
        "mode_payload": "データ投入（検証付き）",
        "mode_payload_hint": "生データをそのまま逐語入力します。改行はENTER。未対応文字があれば送信せず拒否します。",
        "fp_head": "完全性フィンガープリント",
        "fp_sha": "SHA-256（先頭8桁）",
        "fp_words": "音節トークン",
        "fp_hint": "投入後、対向機で同じデータの sha256 を出し（例: sha256sum）、先頭8桁と音節を突き合わせてください。一致すれば壊れずに入っています。",
        "need_arm_to_send": "送信するには、先にPi上で武装してください。",
        "sent_ok": "送信完了（レイアウト: {layout}、打鍵 {keys} 件）",
        "preview_expired": "プレビューの有効期限が切れました。もう一度プレビューしてください。",
        "rate_limited": "送信が多すぎます。少し待ってください。",
        "bad_host": "不正なHostヘッダーです。",
        "trust_note": "注意: キー入力は任意コマンド実行につながり得ます。送信前に必ずプレビューで内容を確認してください。",
    },
    "en": {
        "title": "Wi-Fi HID Bridge",
        "lang_other": "日本語",
        "token_prompt": "Enter the access token (shown in the Pi's journal or token file)",
        "token_label": "Token",
        "token_submit": "Authenticate",
        "token_bad": "Token mismatch.",
        "token_locked": "Too many attempts. Please wait and retry.",
        "logout": "Log out",
        "armed": "ARMED (until {ts})",
        "disarmed": "DISARMED (run hidbeam_arm.py on the Pi to arm)",
        "hw_ok": "HID device: available",
        "hw_ng": "HID device: not detected (cannot send)",
        "hint": "Commands: STRING / ENTER / TAB / DELAY / GUI/CTRL/ALT/SHIFT combos / REM",
        "layout": "Target keyboard layout",
        "preview_btn": "Preview (no send)",
        "send_btn": "Send this",
        "disarm_btn": "Disarm (abort)",
        "plan_head": "Keystroke preview (nothing sent yet)",
        "plan_commands": "Commands",
        "plan_keys": "Key events",
        "plan_duration": "Estimated duration",
        "plan_combos": "Modifier combos",
        "plan_specials": "Special keys",
        "plan_gui_warn": "Contains a GUI (Windows/Command) modifier. Watch for launcher actions.",
        "detail_head": "Keystroke breakdown (HID Usage ID)",
        "col_step": "Action",
        "col_usage": "Usage ID",
        "col_mod": "Modifier",
        "detail_more": "+{n} more (showing the first rows only)",
        "mode_label": "Input mode",
        "mode_ducky": "DuckyScript",
        "mode_payload": "Data entry (verified)",
        "mode_payload_hint": "Types raw data verbatim. Newlines become ENTER. Rejected (not sent) if any character is unsupported.",
        "fp_head": "Integrity fingerprint",
        "fp_sha": "SHA-256 (first 8)",
        "fp_words": "Syllables",
        "fp_hint": "After entry, compute sha256 of the same data on the target (e.g. sha256sum) and compare the first 8 hex and the syllables. A match means it arrived intact.",
        "need_arm_to_send": "Arm the device on the Pi before sending.",
        "sent_ok": "Sent (layout: {layout}, {keys} key events)",
        "preview_expired": "Preview expired. Please preview again.",
        "rate_limited": "Too many sends. Please wait.",
        "bad_host": "Invalid Host header.",
        "trust_note": "Note: keystrokes can trigger arbitrary command execution. Always review the preview before sending.",
    },
}


def _lang():
    lang = session.get("lang")
    if lang not in STRINGS:
        lang = "ja"
    return lang


# ---------- HTML ----------

PAGE = """<!doctype html>
<html lang="{{ lang }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ t.title }}</title>
<style>
  :root { --fg:#1a1a1a; --bg:#fff; --muted:#666; --line:#ccc;
          --ok-bg:#e0f0e0; --err-bg:#fce0e0; --warn-bg:#fff4d6; --armed:#1a7f37; --disarmed:#b00020; }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) { --fg:#e8e8e8; --bg:#1c1c1e; --muted:#aaa; --line:#444;
          --ok-bg:#13351a; --err-bg:#3a1414; --warn-bg:#3a300f; --armed:#4ade80; --disarmed:#ff6b6b; } }
  * { box-sizing: border-box; }
  body { font-family: ui-monospace, Menlo, Consolas, monospace; color: var(--fg); background: var(--bg);
         max-width: 720px; margin: 0 auto; padding: 16px; line-height: 1.6; }
  h1 { font-size: 18px; margin: 8px 0; }
  .bar { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center;
         font-size: 13px; color: var(--muted); }
  .state { font-weight: bold; }
  .armed { color: var(--armed); }
  .disarmed { color: var(--disarmed); }
  textarea { width: 100%; height: 240px; font: 14px ui-monospace, monospace; padding: 8px;
             border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); }
  button, input[type=submit] { font-size: 16px; padding: 12px 20px; min-height: 44px; margin-top: 8px;
             border: 1px solid var(--line); border-radius: 6px; cursor: pointer; background: var(--bg); color: var(--fg); }
  input[type=password], input[type=text] { font-size: 16px; padding: 10px; min-height: 44px; width: 100%;
             border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); }
  .msg { margin-top: 12px; padding: 10px; border-radius: 6px; white-space: pre-wrap; }
  .ok { background: var(--ok-bg); } .err { background: var(--err-bg); } .warn { background: var(--warn-bg); }
  .hint, .trust { color: var(--muted); font-size: 13px; margin: 8px 0; }
  .trust { border-left: 3px solid var(--line); padding-left: 10px; }
  .layout label { margin-right: 16px; display: inline-block; min-height: 44px; line-height: 44px; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; margin-top: 8px; }
  td { border-bottom: 1px solid var(--line); padding: 6px 4px; }
  td:first-child { color: var(--muted); width: 45%; }
  th { text-align: left; border-bottom: 1px solid var(--line); padding: 6px 4px; color: var(--muted); font-weight: normal; }
  table.detail td:first-child { width: 3em; color: var(--muted); }
  details { margin-top: 8px; } summary { cursor: pointer; color: var(--muted); font-size: 13px; }
  form.inline { display: inline; }
  @media (max-width: 480px) { body { padding: 12px; } h1 { font-size: 16px; } }
</style>
</head>
<body>
<div class="bar">
  <span>{{ t.title }}</span>
  <span>
    <a href="{{ url_for('toggle_lang') }}">{{ t.lang_other }}</a>
    {% if authed %} · <a href="{{ url_for('logout') }}">{{ t.logout }}</a>{% endif %}
  </span>
</div>
<h1>{{ t.title }}</h1>

{% if not authed %}
  <p class="hint">{{ t.token_prompt }}</p>
  {% if message %}<div class="msg err">{{ message }}</div>{% endif %}
  <form method="POST" action="{{ url_for('auth') }}">
    <label>{{ t.token_label }}<br><input type="password" name="token" autocomplete="off" autofocus></label>
    <br><input type="submit" value="{{ t.token_submit }}">
  </form>
{% else %}
  <div class="bar">
    <span class="state {{ 'armed' if armed else 'disarmed' }}">
      {% if armed %}{{ t.armed.format(ts=armed_ts) }}{% else %}{{ t.disarmed }}{% endif %}
    </span>
    <span>{{ t.hw_ok if hw else t.hw_ng }}</span>
  </div>
  <p class="trust">{{ t.trust_note }}</p>
  <p class="hint">{{ t.hint }}</p>

  <form method="POST" action="{{ url_for('preview') }}">
    <div class="layout">{{ t.mode_label }}:
      <label><input type="radio" name="mode" value="ducky" {% if mode!='payload' %}checked{% endif %}>{{ t.mode_ducky }}</label>
      <label><input type="radio" name="mode" value="payload" {% if mode=='payload' %}checked{% endif %}>{{ t.mode_payload }}</label>
    </div>
    {% if mode=='payload' %}<p class="hint">{{ t.mode_payload_hint }}</p>{% endif %}
    <div class="layout">{{ t.layout }}:
      <label><input type="radio" name="layout" value="us" {% if layout=='us' %}checked{% endif %}>US</label>
      <label><input type="radio" name="layout" value="jis" {% if layout=='jis' %}checked{% endif %}>JIS</label>
    </div>
    <textarea name="script" placeholder="DELAY 500&#10;STRING Hello&#10;ENTER">{{ prev }}</textarea>
    <br><button type="submit">{{ t.preview_btn }}</button>
  </form>

  {% if plan %}
    <div class="msg ok"><strong>{{ t.plan_head }}</strong>
    <table>
      <tr><td>{{ t.plan_commands }}</td><td>{{ plan.command_count }}</td></tr>
      <tr><td>{{ t.plan_keys }}</td><td>{{ plan.key_event_count }}</td></tr>
      <tr><td>{{ t.plan_duration }}</td><td>{{ '%.1f'|format(plan.estimated_duration_ms/1000) }} s</td></tr>
      {% if plan.combos %}<tr><td>{{ t.plan_combos }}</td><td>{{ plan.combos|join(', ') }}</td></tr>{% endif %}
      {% if plan.specials %}<tr><td>{{ t.plan_specials }}</td><td>{{ plan.specials|join(', ') }}</td></tr>{% endif %}
    </table>
    </div>
    {% if plan.fingerprint %}
    <div class="msg ok"><strong>{{ t.fp_head }}</strong>
      <table>
        <tr><td>{{ t.fp_sha }}</td><td><code>{{ plan.fingerprint.short }}</code></td></tr>
        <tr><td>{{ t.fp_words }}</td><td><code>{{ plan.fingerprint.words }}</code></td></tr>
      </table>
      <p class="hint">{{ t.fp_hint }}</p>
    </div>
    {% endif %}
    {% if detail_rows %}
    <details open>
      <summary>{{ t.detail_head }}</summary>
      <table class="detail">
        <tr><th>#</th><th>{{ t.col_step }}</th><th>{{ t.col_usage }}</th><th>{{ t.col_mod }}</th></tr>
        {% for r in detail_rows %}
        <tr><td>{{ loop.index }}</td><td>{{ r.label }}</td><td>{{ r.usage_id }}</td><td>{{ r.modifier }}</td></tr>
        {% endfor %}
      </table>
      {% if detail_omitted %}<p class="hint">{{ t.detail_more.format(n=detail_omitted) }}</p>{% endif %}
    </details>
    {% endif %}
    {% if plan.uses_gui_modifier %}<div class="msg warn">{{ t.plan_gui_warn }}</div>{% endif %}
    {% if armed %}
      <form method="POST" action="{{ url_for('execute') }}">
        <input type="hidden" name="nonce" value="{{ nonce }}">
        <button type="submit">{{ t.send_btn }}</button>
      </form>
    {% else %}
      <div class="msg warn">{{ t.need_arm_to_send }}</div>
    {% endif %}
    <form class="inline" method="POST" action="{{ url_for('disarm_route') }}">
      <button type="submit">{{ t.disarm_btn }}</button>
    </form>
  {% endif %}

  {% if message %}<div class="msg {{ msg_class }}">{{ message }}</div>{% endif %}
{% endif %}
</body>
</html>
"""


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_BYTES
# セッションCookieの堅牢化（Origin/Host検証と併せてCSRFを多層で防ぐ）。
# httpのローカルAP運用のためSECUREは課さない（Originチェックで補う）。
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)
# 起動時フォールバックの秘密鍵（main() が再設定する）。WSGIサーバーから
# app を直接ロードしてもセッションが機能するようにしておく。
app.secret_key = secrets.token_bytes(32)


def render(**kw):
    t = STRINGS[_lang()]
    base = dict(
        lang=_lang(), t=t, authed=session.get("authed", False),
        armed=is_armed(), armed_ts="", hw=_hid_available,
        layout=hid_core.DEFAULT_LAYOUT, prev="", plan=None, nonce="",
        detail_rows=[], detail_omitted=0, mode="ducky",
        message=None, msg_class="ok",
    )
    au = armed_until()
    if au and au != float("inf"):
        base["armed_ts"] = datetime.fromtimestamp(au).strftime("%H:%M:%S")
    base.update(kw)
    return render_template_string(PAGE, **base)


@app.before_request
def _guard_host():
    # GET含め、許可ホスト以外は拒否（DNSリバインディング対策）
    if not _host_ok():
        abort(400, STRINGS[_lang()]["bad_host"])


@app.after_request
def _security_headers(resp):
    # このページはJavaScriptを一切使わない。script-src は default-src 'none' で封じ、
    # インラインCSSのみ style-src で許可する。フォーム送信は自オリジンのみ。
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.errorhandler(413)
def _too_large(e):
    # 本文がMAX_CONTENT_LENGTHを超えた場合（日英併記の簡易応答）
    return "入力が大きすぎます / Request entity too large\n", 413


@app.route("/", methods=["GET"])
def index():
    return render()


@app.route("/lang", methods=["GET"])
def toggle_lang():
    session["lang"] = "en" if _lang() == "ja" else "ja"
    return redirect(url_for("index"))


@app.route("/auth", methods=["POST"])
def auth():
    t = STRINGS[_lang()]
    if _auth_locked():
        audit("auth_locked")
        return render(message=t["token_locked"], msg_class="err")
    supplied = request.form.get("token", "")
    if _check_token(supplied):
        session["authed"] = True
        audit("auth_ok")
        return redirect(url_for("index"))
    _record_auth_fail()
    audit("auth_fail")
    return render(message=t["token_bad"], msg_class="err")


@app.route("/logout", methods=["GET"])
def logout():
    session.pop("authed", None)
    return redirect(url_for("index"))


def _require_authed():
    if not session.get("authed"):
        abort(403)


@app.route("/preview", methods=["POST"])
def preview():
    _require_authed()
    if not _origin_ok():
        abort(403)
    t = STRINGS[_lang()]
    script = request.form.get("script", "")
    layout = request.form.get("layout", hid_core.DEFAULT_LAYOUT)
    if layout not in hid_core.LAYOUTS:
        layout = hid_core.DEFAULT_LAYOUT
    mode = "payload" if request.form.get("mode") == "payload" else "ducky"
    try:
        compiled = _compile_for_mode(script, layout, mode)
    except DuckyCompileError as e:
        audit("preview_reject", mode=mode, errors=len(e.errors))
        return render(prev=script, layout=layout, mode=mode,
                      message="\n".join(e.errors), msg_class="err")
    nonce = _issue_nonce(script, layout, mode)
    detail_rows, detail_omitted = compiled.detail_rows(100)
    audit("preview_ok", mode=mode, commands=compiled.command_count,
          keys=compiled.key_event_count)
    return render(prev=script, layout=layout, mode=mode, plan=compiled.plan_summary(),
                  nonce=nonce, detail_rows=detail_rows, detail_omitted=detail_omitted)


@app.route("/execute", methods=["POST"])
def execute():
    _require_authed()
    if not _origin_ok():
        abort(403)
    t = STRINGS[_lang()]
    nonce = request.form.get("nonce", "")

    # 武装チェックはノンス引き当てより先（未武装ならノンスを消費しない）
    if REQUIRE_ARM and not is_armed():
        audit("execute_reject", reason="disarmed")
        return render(message=t["need_arm_to_send"], msg_class="err")

    # プレビュー時にサーバーへ保持したスクリプトをノンスで引き当てる（単回）。
    # 実行対象はクライアントの送信値ではなく、プレビューしたものそのもの。
    data = _consume_nonce(nonce)
    if data is None:
        audit("execute_reject", reason="nonce")
        return render(message=t["preview_expired"], msg_class="err")
    script, layout, mode = data

    if not _rate_allow_exec():
        audit("execute_reject", reason="rate")
        return render(prev=script, layout=layout, mode=mode,
                      message=t["rate_limited"], msg_class="err")

    # 送信直前にもう一度、全体を検証（部分送信を構造的に排除）
    try:
        compiled = _compile_for_mode(script, layout, mode)
    except DuckyCompileError as e:
        audit("execute_reject", reason="compile", errors=len(e.errors))
        return render(prev=script, layout=layout, mode=mode,
                      message="\n".join(e.errors), msg_class="err")

    errors = execute_compiled(compiled)
    if errors:
        audit("execute_error")
        return render(prev=script, layout=layout,
                      message="\n".join(errors), msg_class="err")
    audit("execute_ok", commands=compiled.command_count,
          keys=compiled.key_event_count)
    return render(message=t["sent_ok"].format(layout=layout,
                  keys=compiled.key_event_count), msg_class="ok")


@app.route("/disarm", methods=["POST"])
def disarm_route():
    _require_authed()
    disarm()
    audit("disarm")
    return redirect(url_for("index"))


def main():
    global ACCESS_TOKEN
    app.secret_key = secrets.token_bytes(32)  # per-boot。再起動で全セッション無効
    ACCESS_TOKEN = _init_token()
    if not os.environ.get("HIDBEAM_TOKEN"):
        sys.stderr.write(
            f"[hidbeam] access token: {ACCESS_TOKEN}\n"
            f"[hidbeam] （このトークンをブラウザーに入力してください。token file: {TOKEN_FILE}）\n"
        )
    _open_hid_device()
    if not _hid_available:
        sys.stderr.write(
            f"[hidbeam] 警告: HIDデバイス {HID_DEVICE} を開けません。"
            " UIは起動しますが送信はできません。\n"
        )
    sys.stderr.write(
        f"[hidbeam] listening on http://{LISTEN_HOST}:{LISTEN_PORT}"
        f" (require_arm={REQUIRE_ARM})\n"
    )
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
