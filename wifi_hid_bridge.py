#!/usr/bin/env python3
"""Wi-Fi HID Bridge - 『エアギャップ・ブリッジ 隔離環境のデータ入力技法』第7章

Raspberry Pi Zero W上で動作する自作DuckyScriptインタープリター。
ブラウザーから受け取ったDuckyScript 1.0サブセットをパースし、
USB HIDキーボードとしてMac Miniへキーストロークを送信する。

前提：
 - Raspberry Pi OS Lite (Bookworm以降を想定)
 - configfs + libcompositeでHIDキーボードガジェットを構成済み
 - /dev/hidg0 に書き込めること（root権限またはudevルールで調整）
 - Flaskがインストール済み（pip install flask）

起動：
 sudo python3 wifi_hid_bridge.py
 → メインPCのブラウザーで http://<Pi Zero WのIP>:8080/ にアクセス
"""

import os
import threading
import time
from flask import Flask, request, render_template_string

HID_DEVICE = "/dev/hidg0"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080
CHAR_DELAY_SEC = 0.01


# ---------- HID Usage ID テーブル ----------
# USB HID Usage Tables 1.12, Keyboard/Keypad Page (0x07) に基づく。
# 英字（a-z）と数字（0-9）はUS/JIS共通、記号キーの配置が異なる。

KEYMAP_US_NOSHIFT = {
    "a": 0x04, "b": 0x05, "c": 0x06, "d": 0x07, "e": 0x08,
    "f": 0x09, "g": 0x0A, "h": 0x0B, "i": 0x0C, "j": 0x0D,
    "k": 0x0E, "l": 0x0F, "m": 0x10, "n": 0x11, "o": 0x12,
    "p": 0x13, "q": 0x14, "r": 0x15, "s": 0x16, "t": 0x17,
    "u": 0x18, "v": 0x19, "w": 0x1A, "x": 0x1B, "y": 0x1C, "z": 0x1D,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    " ": 0x2C, "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30,
    "\\": 0x31, ";": 0x33, "'": 0x34, "`": 0x35,
    ",": 0x36, ".": 0x37, "/": 0x38,
}

KEYMAP_US_SHIFT = {
    "A": 0x04, "B": 0x05, "C": 0x06, "D": 0x07, "E": 0x08,
    "F": 0x09, "G": 0x0A, "H": 0x0B, "I": 0x0C, "J": 0x0D,
    "K": 0x0E, "L": 0x0F, "M": 0x10, "N": 0x11, "O": 0x12,
    "P": 0x13, "Q": 0x14, "R": 0x15, "S": 0x16, "T": 0x17,
    "U": 0x18, "V": 0x19, "W": 0x1A, "X": 0x1B, "Y": 0x1C, "Z": 0x1D,
    "!": 0x1E, "@": 0x1F, "#": 0x20, "$": 0x21, "%": 0x22,
    "^": 0x23, "&": 0x24, "*": 0x25, "(": 0x26, ")": 0x27,
    "_": 0x2D, "+": 0x2E, "{": 0x2F, "}": 0x30,
    "|": 0x31, ":": 0x33, '"': 0x34, "~": 0x35,
    "<": 0x36, ">": 0x37, "?": 0x38,
}

# JIS配列（日本語キーボード）
# 0x32 = Non-US # (JISでは「]」キー)
# 0x87 = International1 (JISでは「\_」キー、カーソル右)
# 0x89 = International3 (JISでは「¥|」キー、BS左)

KEYMAP_JIS_NOSHIFT = {
    "a": 0x04, "b": 0x05, "c": 0x06, "d": 0x07, "e": 0x08,
    "f": 0x09, "g": 0x0A, "h": 0x0B, "i": 0x0C, "j": 0x0D,
    "k": 0x0E, "l": 0x0F, "m": 0x10, "n": 0x11, "o": 0x12,
    "p": 0x13, "q": 0x14, "r": 0x15, "s": 0x16, "t": 0x17,
    "u": 0x18, "v": 0x19, "w": 0x1A, "x": 0x1B, "y": 0x1C, "z": 0x1D,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    " ": 0x2C, "-": 0x2D, "^": 0x2E, "@": 0x2F, "[": 0x30,
    "]": 0x32, ";": 0x33, ":": 0x34,
    ",": 0x36, ".": 0x37, "/": 0x38,
    "\\": 0x87, "¥": 0x89,
}

KEYMAP_JIS_SHIFT = {
    "A": 0x04, "B": 0x05, "C": 0x06, "D": 0x07, "E": 0x08,
    "F": 0x09, "G": 0x0A, "H": 0x0B, "I": 0x0C, "J": 0x0D,
    "K": 0x0E, "L": 0x0F, "M": 0x10, "N": 0x11, "O": 0x12,
    "P": 0x13, "Q": 0x14, "R": 0x15, "S": 0x16, "T": 0x17,
    "U": 0x18, "V": 0x19, "W": 0x1A, "X": 0x1B, "Y": 0x1C, "Z": 0x1D,
    "!": 0x1E, '"': 0x1F, "#": 0x20, "$": 0x21, "%": 0x22,
    "&": 0x23, "'": 0x24, "(": 0x25, ")": 0x26,
    "=": 0x2D, "~": 0x2E, "`": 0x2F, "{": 0x30, "}": 0x32,
    "+": 0x33, "*": 0x34,
    "<": 0x36, ">": 0x37, "?": 0x38,
    "_": 0x87, "|": 0x89,
}

LAYOUTS = {
    "us": (KEYMAP_US_NOSHIFT, KEYMAP_US_SHIFT),
    "jis": (KEYMAP_JIS_NOSHIFT, KEYMAP_JIS_SHIFT),
}
DEFAULT_LAYOUT = "us"

SPECIAL_KEYS = {
    "ENTER": 0x28, "RETURN": 0x28,
    "ESC": 0x29, "ESCAPE": 0x29,
    "BACKSPACE": 0x2A,
    "TAB": 0x2B,
    "SPACE": 0x2C,
    "CAPSLOCK": 0x39,
    "F1": 0x3A, "F2": 0x3B, "F3": 0x3C, "F4": 0x3D,
    "F5": 0x3E, "F6": 0x3F, "F7": 0x40, "F8": 0x41,
    "F9": 0x42, "F10": 0x43, "F11": 0x44, "F12": 0x45,
    "DELETE": 0x4C, "DEL": 0x4C,
    "HOME": 0x4A, "END": 0x4D,
    "PAGEUP": 0x4B, "PAGEDOWN": 0x4E,
    "RIGHT": 0x4F, "LEFT": 0x50, "DOWN": 0x51, "UP": 0x52,
}

MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08

MODIFIER_NAMES = {
    "CTRL": MOD_LCTRL, "CONTROL": MOD_LCTRL,
    "SHIFT": MOD_LSHIFT,
    "ALT": MOD_LALT, "OPTION": MOD_LALT,
    "GUI": MOD_LGUI, "WINDOWS": MOD_LGUI, "WIN": MOD_LGUI,
    "COMMAND": MOD_LGUI, "CMD": MOD_LGUI, "SUPER": MOD_LGUI,
}


# ---------- HIDレポート送信 ----------
# 標準ブートキーボードレポートは8バイト固定：
#   [modifier, reserved, key1, key2, key3, key4, key5, key6]

RELEASE_REPORT = bytes(8)

_hid_lock = threading.Lock()
_hid_file = None


def _open_hid_device():
    global _hid_file
    _hid_file = open(HID_DEVICE, "rb+", buffering=0)


def build_report(modifier=0, keycode=0):
    return bytes([modifier, 0, keycode, 0, 0, 0, 0, 0])


def write_report(report):
    with _hid_lock:
        _hid_file.write(report)


def send_key(keycode, modifier=0):
    write_report(build_report(modifier, keycode))
    write_report(RELEASE_REPORT)


def char_to_keycode(ch, layout=DEFAULT_LAYOUT):
    """1文字をHIDキーコードと修飾キーに変換する。未対応ならNoneを返す。"""
    noshift, shift = LAYOUTS[layout]
    if ch in noshift:
        return noshift[ch], 0
    if ch in shift:
        return shift[ch], MOD_LSHIFT
    if ch == "\n":
        return 0x28, 0
    if ch == "\t":
        return 0x2B, 0
    return None, 0


# ---------- DuckyScriptパーサー ----------

class DuckyParseError(Exception):
    pass


def execute_line(line, layout=DEFAULT_LAYOUT):
    stripped = line.strip()
    if not stripped:
        return
    upper = stripped.upper()
    if upper == "REM" or upper.startswith("REM "):
        return

    tokens = stripped.split(None, 1)
    command = tokens[0].upper()
    rest = tokens[1] if len(tokens) > 1 else ""

    if command == "STRING":
        unsupported = []
        seen = set()
        for ch in rest:
            kc, mod = char_to_keycode(ch, layout)
            if kc is None:
                if ch not in seen:
                    seen.add(ch)
                    unsupported.append(ch)
                continue
            send_key(kc, mod)
            time.sleep(CHAR_DELAY_SEC)
        if unsupported:
            chars = ", ".join(repr(c) for c in unsupported)
            raise DuckyParseError(f"STRING: 未対応文字をスキップしました: {chars}")
        return

    if command == "DELAY":
        try:
            ms = int(rest)
        except ValueError:
            raise DuckyParseError(f"DELAY: ミリ秒の値が不正: {rest!r}")
        if ms < 0:
            raise DuckyParseError(f"DELAY: 負値は指定できません: {ms}")
        time.sleep(ms / 1000.0)
        return

    if command in SPECIAL_KEYS:
        send_key(SPECIAL_KEYS[command])
        return

    if command in MODIFIER_NAMES:
        modifier = MODIFIER_NAMES[command]
        keycode = 0
        for tok in rest.split():
            tok_u = tok.upper()
            if tok_u in MODIFIER_NAMES:
                modifier |= MODIFIER_NAMES[tok_u]
            elif tok_u in SPECIAL_KEYS:
                keycode = SPECIAL_KEYS[tok_u]
            elif len(tok) == 1:
                kc, extra_mod = char_to_keycode(tok.lower(), layout)
                if kc is not None:
                    keycode = kc
                    modifier |= extra_mod
                else:
                    raise DuckyParseError(f"Unknown key in combo: {tok!r}")
            else:
                raise DuckyParseError(f"Unknown token in combo: {tok!r}")
        send_key(keycode, modifier)
        return

    raise DuckyParseError(f"Unknown command: {command!r}")


def execute_script(script, layout=DEFAULT_LAYOUT):
    errors = []
    for i, line in enumerate(script.splitlines(), 1):
        try:
            execute_line(line, layout)
        except DuckyParseError as e:
            errors.append(f"行 {i}: {e}")
        except (OSError, PermissionError) as e:
            errors.append(f"行 {i}: HIDデバイス書き込み失敗: {e}")
    return errors


# ---------- Web UI (Flask) ----------

INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Wi-Fi HID Bridge</title>
<style>
body { font-family: monospace; max-width: 720px; margin: 2em auto; padding: 0 1em; }
h1 { font-size: 18px; }
textarea { width: 100%; height: 280px; font-family: monospace; font-size: 14px; }
button { font-size: 16px; padding: 8px 24px; margin-top: 8px; }
.msg { margin-top: 12px; padding: 8px; border-radius: 4px; white-space: pre-wrap; }
.ok { background: #e0f0e0; }
.err { background: #fce0e0; }
.hint { color: #666; font-size: 13px; margin: 8px 0; }
.layout { margin: 8px 0; }
.layout label { margin-right: 16px; }
</style>
</head>
<body>
<h1>Wi-Fi HID Bridge - DuckyScript実行</h1>
<p class="hint">対応コマンド: STRING / ENTER / TAB / DELAY / GUI・CTRL・ALT・SHIFT修飾キー組み合わせ / REM</p>
<form method="POST" action="/execute">
<div class="layout">
  接続先キーレイアウト:
  <label><input type="radio" name="layout" value="us" {% if layout == "us" %}checked{% endif %}>US配列</label>
  <label><input type="radio" name="layout" value="jis" {% if layout == "jis" %}checked{% endif %}>JIS配列</label>
</div>
<textarea name="script" placeholder="DELAY 500&#10;STRING Hello from Wi-Fi HID Bridge&#10;ENTER">{{ prev }}</textarea>
<br>
<button type="submit">実行</button>
</form>
{% if result is not none %}
  {% if result %}
    <div class="msg err">{{ result | join('\n') }}</div>
  {% else %}
    <div class="msg ok">実行完了（レイアウト: {{ layout }}）</div>
  {% endif %}
{% endif %}
</body>
</html>
"""

app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    return render_template_string(
        INDEX_HTML, result=None, prev="", layout=DEFAULT_LAYOUT
    )


@app.route("/execute", methods=["POST"])
def execute():
    script = request.form.get("script", "")
    layout = request.form.get("layout", DEFAULT_LAYOUT)
    if layout not in LAYOUTS:
        layout = DEFAULT_LAYOUT
    errors = execute_script(script, layout)
    return render_template_string(
        INDEX_HTML, result=errors, prev=script, layout=layout
    )


def main():
    if not os.path.exists(HID_DEVICE):
        raise SystemExit(
            f"HIDデバイス {HID_DEVICE} が存在しません。"
            " libcompositeガジェットのセットアップを確認してください。"
        )
    _open_hid_device()
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False)


if __name__ == "__main__":
    main()
