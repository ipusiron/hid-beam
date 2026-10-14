#!/bin/bash
#
# hidbeam-preflight.sh - 起動前チェック（AP設定の安全確認）
# 『エアギャップ・ブリッジ 隔離環境のデータ入力技法』第7章 / HIDBeam
#
# wifi-hid-bridge.service の ExecStartPre から呼ばれ、AP の hostapd 設定が
# 初期パスフレーズ・弱いパスフレーズのままなら、ブリッジの起動を失敗させる。
#
# 配置：/usr/local/bin/hidbeam-preflight.sh（実行権限を付与）
# AP を使わず既存ネットワーク上で動かす構成では hostapd conf が無いので素通りする。

set -euo pipefail

CONF="${HIDBEAM_HOSTAPD_CONF:-/etc/hostapd/hostapd.conf}"

if [ ! -f "$CONF" ]; then
    echo "[preflight] hostapd conf が見つかりません ($CONF)。AP未使用とみなして続行します。"
    exit 0
fi

# 初期パスフレーズのまま → 失敗
if grep -q 'CHANGE_ME_STRONG_PASSPHRASE' "$CONF"; then
    echo "[preflight] 失敗: hostapd の wpa_passphrase が初期値のままです。変更してください。" >&2
    exit 1
fi

# WPA2 のパスフレーズは8〜63文字。範囲外なら失敗。
PASS="$(sed -n 's/^[[:space:]]*wpa_passphrase=//p' "$CONF" | head -n1)"
if [ -n "$PASS" ]; then
    LEN=${#PASS}
    if [ "$LEN" -lt 8 ] || [ "$LEN" -gt 63 ]; then
        echo "[preflight] 失敗: wpa_passphrase の長さが不正です（${LEN}文字）。8〜63文字にしてください。" >&2
        exit 1
    fi
fi

echo "[preflight] OK"
exit 0
