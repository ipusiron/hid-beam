#!/usr/bin/env python3
"""hid_core - DuckyScriptサブセットの解析・検証コア（ハードウェア非依存）

このモジュールはHIDデバイスにもFlaskにも依存しない。DuckyScriptの文字列を
受け取り、送信前に**スクリプト全体を解析・検証**してから打鍵計画
(CompiledScript) を返す。検証に失敗した場合は1件も打鍵を生成せず
DuckyCompileError を送出する。

分離の目的:
 - PC上で単体テストできるようにする（実機不要）。
 - 「未対応文字や上限超過を見つけたら、それ以前のキーだけ送って失敗する」
   という部分送信を構造的に不可能にする（compile → preview → execute の順を強制）。

本モジュールは打鍵を生成するだけで、実際のHID書き込みは呼び出し側が行う。
"""

import re
import base64
import hashlib
from dataclasses import dataclass, field


# ---------- 上限（安全のための既定値） ----------
# いずれも運用者が明示的に緩めない限り越えられない。無制限のDELAYや巨大な
# スクリプトでホスト側を長時間占有する事態を防ぐ。

@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 8192        # スクリプト全体のバイト数
    max_commands: int = 500            # 行（コマンド）数
    max_string_len: int = 1024         # STRING 1命令あたりの文字数
    max_single_delay_ms: int = 60_000  # DELAY 1命令の上限（60秒）
    max_total_delay_ms: int = 300_000  # DELAY 合計の上限（5分）
    max_key_events: int = 20_000       # 打鍵イベント総数
    max_total_duration_ms: int = 600_000  # 推定総所要時間の上限（10分）


DEFAULT_LIMITS = Limits()

# 打鍵間の既定ディレイ（所要時間の見積りにも使う。実送信側と一致させる）
CHAR_DELAY_MS = 10


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

# UK（英国ISO）配列。US からの差分は確度の高い範囲のみ反映する。
#  - Shift+2 = "（US は @）、Shift+' = @（US は "）… 有名な入れ替え
#  - # は Non-US # キー(0x32)で単独、~ はその Shift
#  - \ | は Non-US \ キー(0x64)
#  - £ ¬ ¦ € 等の非ASCIIは扱わない（本ツールはASCII打鍵のみ）
# 新しい配列は実機で使う前に、必ずレイアウト診断で表示を確認すること。
KEYMAP_UK_NOSHIFT = dict(KEYMAP_US_NOSHIFT)
KEYMAP_UK_NOSHIFT["\\"] = 0x64
KEYMAP_UK_NOSHIFT["#"] = 0x32

KEYMAP_UK_SHIFT = dict(KEYMAP_US_SHIFT)
KEYMAP_UK_SHIFT["@"] = 0x34    # Shift+'
KEYMAP_UK_SHIFT['"'] = 0x1F    # Shift+2
KEYMAP_UK_SHIFT["~"] = 0x32    # Non-US # キーの Shift
KEYMAP_UK_SHIFT["|"] = 0x64    # Non-US \ キーの Shift
del KEYMAP_UK_SHIFT["#"]       # UK の Shift+3 は £（非ASCII）。# は 0x32 単独

LAYOUTS = {
    "us": (KEYMAP_US_NOSHIFT, KEYMAP_US_SHIFT),
    "jis": (KEYMAP_JIS_NOSHIFT, KEYMAP_JIS_SHIFT),
    "uk": (KEYMAP_UK_NOSHIFT, KEYMAP_UK_SHIFT),
}
DEFAULT_LAYOUT = "us"

# レイアウト診断用の文字列。US/JIS で物理キーが異なる記号だけを、両レイアウトの
# キーマップに共通して存在する範囲で並べてある。これを打って対向機の表示が
# 参照と一致しなければ、対向機の実レイアウトが選択と食い違っている。
LAYOUT_DIAG_TEXT = "layout check !\"#$%&'()~+*-_@[]{}|;:,.<>?/"

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

MOD_LABELS = [
    (MOD_LCTRL, "CTRL"),
    (MOD_LSHIFT, "SHIFT"),
    (MOD_LALT, "ALT"),
    (MOD_LGUI, "GUI"),
]


def _mod_label(modifier):
    """修飾ビットを "CTRL+SHIFT" のような表示に変換する。"""
    return "+".join(name for bit, name in MOD_LABELS if modifier & bit)


# ---------- 完全性フィンガープリント ----------
# 一方向のキーストローク注入は、レイアウト不一致・取りこぼし・対向側の補完で
# 黙ってデータが壊れる。投入したデータが意図どおりかを運用者が検証できるよう、
# データの SHA-256 と、人間が読み上げて突き合わせやすい音節トークンを計算する。

# 6ビット＝1語の厳選語彙（64語）。短く・相互に紛れにくく・不快語を含まない。
# 音節の機械生成だと差別語などを生み得るため、固定リストにしてある。
_FP_WORDS = [
    "able", "acid", "aqua", "arch", "aura", "bake", "bard", "bean",
    "bell", "bird", "blue", "bolt", "bone", "calm", "cave", "cell",
    "clay", "coal", "cold", "cork", "cove", "crop", "dawn", "deck",
    "dice", "disk", "dome", "dust", "echo", "edge", "fern", "film",
    "fire", "fish", "flag", "foam", "fork", "fuel", "gate", "gear",
    "gift", "gold", "gull", "hall", "harp", "hawk", "herb", "hill",
    "iron", "jade", "kelp", "kite", "lake", "lamp", "leaf", "lime",
    "lion", "loom", "maze", "mint", "mist", "moon", "moss", "nest",
]
assert len(_FP_WORDS) == 64


def syllable_fingerprint(text):
    """テキスト(UTF-8)の SHA-256 と、先頭バイト由来の語フィンガープリントを返す。

    返り値: {"sha256": 64桁hex, "short": 先頭8桁, "words": "gold-fern-lake-mint"}
    SHA-256 先頭3バイト(24ビット)を6ビットずつ4語に符号化する（各語は64語彙の1つ）。
    hex より読み上げ・聞き取りで取り違えにくい。投入後に対向機で同じ値を出して
    突き合わせれば、データが壊れずに入ったかを人が検証できる。
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    b0, b1, b2 = digest[0], digest[1], digest[2]
    idx = [
        b0 >> 2,
        ((b0 & 0x03) << 4) | (b1 >> 4),
        ((b1 & 0x0F) << 2) | (b2 >> 6),
        b2 & 0x3F,
    ]
    hexd = digest.hex()
    return {"sha256": hexd, "short": hexd[:8],
            "words": "-".join(_FP_WORDS[i] for i in idx)}


# ---------- 例外 ----------

class DuckyCompileError(Exception):
    """解析・検証段階のエラー。errors に全件の説明を保持する。"""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ---------- 打鍵計画のデータ構造 ----------

@dataclass(frozen=True)
class KeyEvent:
    keycode: int
    modifier: int
    label: str  # 表示用（"a" / "ENTER" / "GUI+r" など）


@dataclass(frozen=True)
class DelayEvent:
    ms: int


@dataclass
class CompiledScript:
    """検証済みの打鍵計画。executeはこの steps のみを実行する。"""
    layout: str
    steps: list = field(default_factory=list)  # KeyEvent | DelayEvent
    key_event_count: int = 0
    delay_total_ms: int = 0
    estimated_duration_ms: int = 0
    command_count: int = 0
    combos: list = field(default_factory=list)    # 使用された修飾キー組合せ
    specials: list = field(default_factory=list)  # 使用された特殊キー
    uses_gui_modifier: bool = False
    fingerprint: dict = None      # データ投入モードの完全性フィンガープリント
    is_payload: bool = False      # 生データ逐語投入か（DuckyScriptでない）

    def plan_summary(self):
        """プレビュー表示用の要約（実送信しない）。"""
        return {
            "layout": self.layout,
            "command_count": self.command_count,
            "key_event_count": self.key_event_count,
            "delay_total_ms": self.delay_total_ms,
            "estimated_duration_ms": self.estimated_duration_ms,
            "combos": list(self.combos),
            "specials": list(self.specials),
            "uses_gui_modifier": self.uses_gui_modifier,
            "fingerprint": self.fingerprint,
            "is_payload": self.is_payload,
        }

    def detail_rows(self, limit=100):
        """各ステップの打鍵詳細（HID Usage ID・修飾キー）を返す。

        プレビューで「DuckyScriptが実際にどのキーへ展開されるか」を可視化する。
        教材用途であると同時に、送信前に運用者が内容を目視確認する安全機能でもある。
        返り値: (rows, omitted) — rows は先頭 limit 件、omitted は省略件数。
        """
        rows = []
        for st in self.steps:
            if isinstance(st, KeyEvent):
                rows.append({
                    "kind": "key",
                    "label": st.label,
                    "usage_id": f"0x{st.keycode:02X}",
                    "modifier": _mod_label(st.modifier) or "-",
                })
            else:  # DelayEvent
                rows.append({
                    "kind": "delay",
                    "label": f"DELAY {st.ms}ms",
                    "usage_id": "-",
                    "modifier": "-",
                })
        omitted = max(0, len(rows) - limit)
        return rows[:limit], omitted


# ---------- 文字 → キーコード ----------

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


def _reverse_maps(layout):
    noshift, shift = LAYOUTS[layout]
    return ({v: k for k, v in noshift.items()},
            {v: k for k, v in shift.items()})


def predict_display(text, typed_layout, actual_layout):
    """typed_layout で打鍵した text を、実レイアウトが actual_layout の対向機が
    どう表示するかを予測して返す。

    レイアウト不一致を「検出」するだけでなく「どちらにずれているか」を運用者が
    特定できるようにするための関数。打鍵不能な文字や、そのキーに対応表示が無い
    場合は '?' で表す。
    """
    if typed_layout not in LAYOUTS or actual_layout not in LAYOUTS:
        raise DuckyCompileError(["未知のレイアウト"])
    noshift_rev, shift_rev = _reverse_maps(actual_layout)
    out = []
    for ch in text:
        kc, mod = char_to_keycode(ch, typed_layout)
        if kc is None:
            out.append("?")
            continue
        rev = shift_rev if (mod & MOD_LSHIFT) else noshift_rev
        out.append(rev.get(kc, "?"))
    return "".join(out)


# ---------- スクリプト全体の解析・検証 ----------

def compile_script(script, layout=DEFAULT_LAYOUT, limits=DEFAULT_LIMITS):
    """DuckyScript文字列を解析・検証し、CompiledScriptを返す。

    1件でも問題があれば DuckyCompileError を送出し、打鍵は一切生成しない。
    呼び出し側は、このCompiledScriptのstepsを順に実行することだけが許される
    （部分送信は構造的に起こり得ない）。
    """
    if layout not in LAYOUTS:
        raise DuckyCompileError([f"未知のレイアウト: {layout!r}"])

    errors = []

    # --- 入力サイズの上限 ---
    raw_bytes = script.encode("utf-8", errors="replace")
    if len(raw_bytes) > limits.max_input_bytes:
        errors.append(
            f"入力が大きすぎます: {len(raw_bytes)} bytes "
            f"(上限 {limits.max_input_bytes})"
        )
        # サイズ超過は即座に打ち切る（以降の解析は無意味）
        raise DuckyCompileError(errors)

    lines = script.splitlines()

    steps = []
    key_event_count = 0
    delay_total_ms = 0
    command_count = 0
    combos = []
    specials = []
    unsupported_chars = []  # (行番号, 文字) の順序保持
    seen_unsupported = set()
    uses_gui = False

    def add_key(keycode, modifier, label):
        nonlocal key_event_count, uses_gui
        steps.append(KeyEvent(keycode, modifier, label))
        key_event_count += 1
        if modifier & MOD_LGUI:
            uses_gui = True

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper == "REM" or upper.startswith("REM "):
            continue

        command_count += 1
        if command_count > limits.max_commands:
            errors.append(
                f"コマンド数が上限を超えました (上限 {limits.max_commands})"
            )
            break

        tokens = stripped.split(None, 1)
        command = tokens[0].upper()
        rest = tokens[1] if len(tokens) > 1 else ""

        if command == "STRING":
            if len(rest) > limits.max_string_len:
                errors.append(
                    f"行 {lineno}: STRINGが長すぎます: {len(rest)}文字 "
                    f"(上限 {limits.max_string_len})"
                )
                continue
            for ch in rest:
                kc, mod = char_to_keycode(ch, layout)
                if kc is None:
                    if ch not in seen_unsupported:
                        seen_unsupported.add(ch)
                        unsupported_chars.append((lineno, ch))
                    continue
                add_key(kc, mod, ch)
            continue

        if command == "DELAY":
            try:
                ms = int(rest)
            except ValueError:
                errors.append(f"行 {lineno}: DELAYのミリ秒が不正: {rest!r}")
                continue
            if ms < 0:
                errors.append(f"行 {lineno}: DELAYに負値は指定できません: {ms}")
                continue
            if ms > limits.max_single_delay_ms:
                errors.append(
                    f"行 {lineno}: DELAYが上限を超えました: {ms}ms "
                    f"(上限 {limits.max_single_delay_ms}ms)"
                )
                continue
            delay_total_ms += ms
            steps.append(DelayEvent(ms))
            continue

        if command in SPECIAL_KEYS:
            add_key(SPECIAL_KEYS[command], 0, command)
            if command not in specials:
                specials.append(command)
            continue

        if command in MODIFIER_NAMES:
            modifier = MODIFIER_NAMES[command]
            keycode = 0
            key_label = None
            combo_ok = True
            for tok in rest.split():
                tok_u = tok.upper()
                if tok_u in MODIFIER_NAMES:
                    modifier |= MODIFIER_NAMES[tok_u]
                elif tok_u in SPECIAL_KEYS:
                    keycode = SPECIAL_KEYS[tok_u]
                    key_label = tok_u
                elif len(tok) == 1:
                    kc, extra_mod = char_to_keycode(tok.lower(), layout)
                    if kc is not None:
                        keycode = kc
                        modifier |= extra_mod
                        key_label = tok.lower()
                    else:
                        errors.append(
                            f"行 {lineno}: 組合せ内の未知のキー: {tok!r}"
                        )
                        combo_ok = False
                else:
                    errors.append(
                        f"行 {lineno}: 組合せ内の未知のトークン: {tok!r}"
                    )
                    combo_ok = False
            if not combo_ok:
                continue
            label = _mod_label(modifier)
            if key_label:
                label = f"{label}+{key_label}" if label else key_label
            add_key(keycode, modifier, label)
            if label not in combos:
                combos.append(label)
            continue

        errors.append(f"行 {lineno}: 未知のコマンド: {command!r}")

    # --- 未対応文字（全件まとめて報告。部分送信を避ける） ---
    if unsupported_chars:
        shown = ", ".join(f"{repr(c)}(行{ln})" for ln, c in unsupported_chars)
        errors.append(f"未対応文字が含まれています: {shown}")

    # --- 集計上限 ---
    if key_event_count > limits.max_key_events:
        errors.append(
            f"打鍵イベントが多すぎます: {key_event_count} "
            f"(上限 {limits.max_key_events})"
        )
    if delay_total_ms > limits.max_total_delay_ms:
        errors.append(
            f"DELAY合計が上限を超えました: {delay_total_ms}ms "
            f"(上限 {limits.max_total_delay_ms}ms)"
        )

    estimated_duration_ms = delay_total_ms + key_event_count * CHAR_DELAY_MS
    if estimated_duration_ms > limits.max_total_duration_ms:
        errors.append(
            f"推定総所要時間が上限を超えました: {estimated_duration_ms}ms "
            f"(上限 {limits.max_total_duration_ms}ms)"
        )

    if errors:
        raise DuckyCompileError(errors)

    return CompiledScript(
        layout=layout,
        steps=steps,
        key_event_count=key_event_count,
        delay_total_ms=delay_total_ms,
        estimated_duration_ms=estimated_duration_ms,
        command_count=command_count,
        combos=combos,
        specials=specials,
        uses_gui_modifier=uses_gui,
    )


def compile_payload(text, layout=DEFAULT_LAYOUT, limits=DEFAULT_LIMITS):
    """生データを逐語入力する「データ投入モード」の検証・計画を作る。

    DuckyScript のコマンド解釈はしない。text の各文字をそのまま打鍵へ変換する
    （改行→ENTER、タブ→TAB）。未対応文字が1つでもあれば打鍵を生成せず拒否する
    （壊れたデータを黙って投入しないため）。改行は LF に正規化し、その正規化後の
    テキストに対して完全性フィンガープリントを付す。
    """
    if layout not in LAYOUTS:
        raise DuckyCompileError([f"未知のレイアウト: {layout!r}"])

    # 改行を LF に正規化（\r はキーに無い。投入される実体＝LF に揃える）
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    errors = []
    raw = text.encode("utf-8", errors="replace")
    if len(raw) > limits.max_input_bytes:
        raise DuckyCompileError([
            f"入力が大きすぎます: {len(raw)} bytes (上限 {limits.max_input_bytes})"
        ])

    steps = []
    key_event_count = 0
    unsupported = []
    seen = set()
    for ch in text:
        kc, mod = char_to_keycode(ch, layout)
        if kc is None:
            if ch not in seen:
                seen.add(ch)
                unsupported.append(ch)
            continue
        steps.append(KeyEvent(kc, mod, ch))
        key_event_count += 1

    if unsupported:
        shown = ", ".join(repr(c) for c in unsupported)
        errors.append(f"このレイアウトで入力できない文字があります: {shown}")
    if key_event_count > limits.max_key_events:
        errors.append(
            f"打鍵イベントが多すぎます: {key_event_count} (上限 {limits.max_key_events})"
        )
    estimated_duration_ms = key_event_count * CHAR_DELAY_MS
    if estimated_duration_ms > limits.max_total_duration_ms:
        errors.append(
            f"推定総所要時間が上限を超えました: {estimated_duration_ms}ms "
            f"(上限 {limits.max_total_duration_ms}ms)"
        )
    if errors:
        raise DuckyCompileError(errors)

    line_count = (text.count("\n") + 1) if text else 0
    return CompiledScript(
        layout=layout,
        steps=steps,
        key_event_count=key_event_count,
        delay_total_ms=0,
        estimated_duration_ms=estimated_duration_ms,
        command_count=line_count,
        combos=[],
        specials=[],
        uses_gui_modifier=False,
        fingerprint=syllable_fingerprint(text),
        is_payload=True,
    )


# ---------- ファイル投入（base64）スクリプト生成 ----------

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._/][A-Za-z0-9._/-]*$")


def build_file_inject(content, filename, wrap=76, encoding="base64"):
    """小さなファイルをキーストロークで投入するシェルスクリプトを生成する。

    USBストレージを使えない隔離環境（Unix系）へ、設定ファイルや鍵を確実に入れる
    用途。内容を base64 または hex でエンコードし、対向機で復元、最後に
    `sha256sum` で検証する一連のコマンドを文字列として返す。生成した文字列は
    compile_payload で逐語入力する。

    encoding:
      "base64" → `base64 -d` で復元（既定）。`=` を含むため US 配列向き。
      "hex"    → `xxd -r -p` で復元。0-9a-f のみで両レイアウトで打てる。base64 が
                 無い／`=` が打てない対向機向けの代替。

    返り値: {"script", "fingerprint"(元データLF正規化後), "bytes", "lines",
             "encoding", "decoder"}
    改行は LF に正規化してからエンコードする。filename は安全な文字（英数字と
    . _ - /）のみ許可し、シングルクオート脱出やコマンド注入を防ぐ。
    """
    if not filename or not _SAFE_FILENAME.match(filename):
        raise DuckyCompileError([
            f"ファイル名に使える文字は英数字と . _ - / のみです: {filename!r}"
        ])
    if ".." in filename:
        raise DuckyCompileError(["ファイル名に .. は使えません"])

    norm = content.replace("\r\n", "\n").replace("\r", "\n")
    raw = norm.encode("utf-8")
    if encoding == "base64":
        enc, decoder = base64.b64encode(raw).decode("ascii"), "base64 -d"
    elif encoding == "hex":
        enc, decoder = raw.hex(), "xxd -r -p"
    else:
        raise DuckyCompileError([f"未知のエンコーディング: {encoding!r}（base64 か hex）"])

    lines = [enc[i:i + wrap] for i in range(0, len(enc), wrap)] or [""]
    eof = "HIDBEAM_EOF"
    script = (
        f"{decoder} > '{filename}' <<'{eof}'\n"
        + "\n".join(lines) + "\n"
        + f"{eof}\n"
        + f"sha256sum '{filename}'\n"
    )
    return {
        "script": script,
        "fingerprint": syllable_fingerprint(norm),
        "bytes": len(raw),
        "lines": len(lines),
        "encoding": encoding,
        "decoder": decoder,
    }
