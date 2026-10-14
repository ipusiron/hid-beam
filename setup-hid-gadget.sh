#!/bin/bash
#
# setup-hid-gadget.sh - USB HIDキーボードガジェットのconfigfsセットアップ
# 『エアギャップ・ブリッジ 隔離環境のデータ入力技法』第7章
#
# Raspberry Pi Zero W上で標準ブートキーボードHIDガジェットを作成し、
# /dev/hidg0 経由でHIDレポートを受け付けるキーボードとして動作させる。
#
# 前提：
#  - /boot/firmware/config.txt に dtoverlay=dwc2
#  - /boot/firmware/cmdline.txt に modules-load=dwc2
#  - /etc/modules に libcomposite
#
# 配置：/usr/local/bin/setup-hid-gadget.sh
# 実行権限：chmod +x /usr/local/bin/setup-hid-gadget.sh

set -e

GADGET=/sys/kernel/config/usb_gadget/hid_keyboard

# ガジェットが既に存在する場合は何もしない（二重バインド防止）
if [ -d "$GADGET" ]; then
    echo "Gadget already exists at $GADGET"
    exit 0
fi

mkdir -p "$GADGET"
cd "$GADGET"

# USBデバイスディスクリプター
echo 0x1d6b > idVendor    # Linux Foundation
echo 0x0104 > idProduct   # Multifunction Composite Gadget
echo 0x0100 > bcdDevice   # v1.0.0
echo 0x0200 > bcdUSB      # USB 2.0
echo 0x00   > bDeviceClass
echo 0x00   > bDeviceSubClass
echo 0x00   > bDeviceProtocol
echo 0x40   > bMaxPacketSize0

# 文字列情報（英語ロケール 0x409）
mkdir -p strings/0x409
echo "0000000000000001"         > strings/0x409/serialnumber
echo "AirGapBridge"              > strings/0x409/manufacturer
echo "Wi-Fi HID Bridge Keyboard" > strings/0x409/product

# 設定（Configuration #1）
mkdir -p configs/c.1/strings/0x409
echo "HID Keyboard Config" > configs/c.1/strings/0x409/configuration
echo 250                   > configs/c.1/MaxPower

# HIDキーボードファンクション（Boot Protocol）
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol       # 1 = Keyboard (Boot Protocol)
echo 1 > functions/hid.usb0/subclass       # 1 = Boot Interface Subclass
echo 8 > functions/hid.usb0/report_length  # 8バイト固定レポート

# 標準ブートキーボード用HID Report Descriptor（63バイト）
# USB HID Usage Tables 1.12, Keyboard/Keypad Page (0x07) 準拠
echo -ne \
'\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01'\
'\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x01\x95\x05\x75\x01'\
'\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x01\x95\x06'\
'\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' \
> functions/hid.usb0/report_desc

# ファンクションを設定にリンク
ln -s functions/hid.usb0 configs/c.1/

# UDC（USB Device Controller）にバインドして有効化
# これによりUSBホスト側（Mac Mini）にデバイスとして現れる
UDC=$(ls /sys/class/udc | head -n1)
echo "$UDC" > UDC

# /dev/hidg0 を専用グループ hidbeam が書けるようにする（udev ルールのフォールバック）。
# これにより wifi-hid-bridge を root ではなく hidbeam ユーザーで動かせる。
if getent group hidbeam >/dev/null 2>&1; then
    chgrp hidbeam /dev/hidg0 2>/dev/null || true
    chmod 0660 /dev/hidg0 2>/dev/null || true
fi

echo "HID gadget activated: /dev/hidg0 (UDC=$UDC)"
