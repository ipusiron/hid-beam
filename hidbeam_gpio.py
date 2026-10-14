#!/usr/bin/env python3
"""hidbeam_gpio - GPIOの物理ボタンで arm / abort するデーモン（任意・要配線）

監査の推奨「物理操作で短時間だけ武装する」「中止の物理スイッチ」を実装する。
ARMボタンを押すと短時間だけ武装し、ABORTボタン（任意）で即時に武装解除する。
武装・武装解除の実体は hidbeam_arm と同一（同じ armed ファイルを読み書きする）ため、
Web UI 側の挙動と完全に一致する。

このモジュールは Raspberry Pi 上でのみ動作し、RPi.GPIO と実配線を要する。配線や
ピン番号は環境依存のため、GPIO接続部分は本体のユニットテスト対象外である（呼び出す
arm/disarm のロジックは tests/test_hidbeam_arm.py で、本モジュールのコールバックは
tests/test_hidbeam_gpio.py で検証している）。実機での動作は未検証。

配線例（BCM番号・ボタンは GND へ落とす・内部プルアップ）:
  ARM ボタン   -> GPIO17 と GND
  ABORT ボタン -> GPIO27 と GND（任意）

環境変数:
  HIDBEAM_GPIO_ARM_PIN    arm ボタンの BCM ピン（既定 17）
  HIDBEAM_GPIO_ABORT_PIN  abort ボタンの BCM ピン（既定 27、0 で無効）
  HIDBEAM_GPIO_ARM_MIN    1回の武装の分数（既定 2）
  HIDBEAM_RUN_DIR         armed ファイルのディレクトリ（hidbeam_arm と共有）
"""

import os
import sys
import time

import hidbeam_arm

ARM_PIN = int(os.environ.get("HIDBEAM_GPIO_ARM_PIN") or 17)
ABORT_PIN = int(os.environ.get("HIDBEAM_GPIO_ABORT_PIN") or 27)
ARM_MIN = int(os.environ.get("HIDBEAM_GPIO_ARM_MIN") or 2)


def _on_arm(channel):
    """ARMボタン押下。hidbeam_arm と同じ方式で短時間武装する。"""
    try:
        hidbeam_arm.arm(ARM_MIN)
    except SystemExit as e:
        # arm() は不正値で SystemExit する。デーモンは落とさず記録だけする。
        print(f"[gpio] arm失敗: {e}", file=sys.stderr)


def _on_abort(channel):
    """ABORTボタン押下。即時に武装解除する。"""
    hidbeam_arm.disarm()
    print("[gpio] abort: 武装解除")


def main():
    try:
        import RPi.GPIO as GPIO
    except ImportError:
        sys.exit("RPi.GPIO が無い環境です。Raspberry Pi 上で実行してください。")

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(ARM_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(ARM_PIN, GPIO.FALLING, callback=_on_arm, bouncetime=500)
    if ABORT_PIN:
        GPIO.setup(ABORT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(ABORT_PIN, GPIO.FALLING, callback=_on_abort, bouncetime=500)

    print(f"[gpio] 監視開始: ARM=GPIO{ARM_PIN} "
          f"ABORT=GPIO{ABORT_PIN if ABORT_PIN else '無効'} ({ARM_MIN}分武装)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
