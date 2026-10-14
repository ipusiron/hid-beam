#!/usr/bin/env python3
"""hidbeam_arm - HIDBeam を短時間だけ武装（arm）するローカルコマンド

Web UI から送信できるのは、このコマンドで arm している短い時間だけである。
Pi 上でローカルに（SSH またはコンソールから）実行することを前提とする。
ネットワーク経由では arm できない。これにより、トークンが漏れても、運用者が
手元で arm しない限り送信は始まらない（多層防御の一層）。

使い方（Pi 上、サービスと同じユーザーまたは root で）:
  python3 hidbeam_arm.py --minutes 2     # 2分間だけ武装
  python3 hidbeam_arm.py --disarm        # 即時武装解除
  python3 hidbeam_arm.py --status        # 現在の状態を表示

環境変数:
  HIDBEAM_RUN_DIR   状態ディレクトリ（既定 /run/wifi-hid-bridge）
"""

import os
import sys
import time
import argparse

RUN_DIR = os.environ.get("HIDBEAM_RUN_DIR") or "/run/wifi-hid-bridge"
ARMED_FILE = os.path.join(RUN_DIR, "armed")

# arm できる最大時間（分）。長時間の武装状態を避ける。
MAX_MINUTES = 10


def arm(minutes):
    if minutes <= 0:
        sys.exit("エラー: --minutes は正の値を指定してください")
    if minutes > MAX_MINUTES:
        sys.exit(f"エラー: --minutes は最大 {MAX_MINUTES} 分までです")
    expiry = time.time() + minutes * 60
    os.makedirs(RUN_DIR, exist_ok=True)
    # 0644: 期限のepoch秒は機密ではない。RUN_DIR自体が0700のため実質 root と
    # サービスユーザーだけが読める。root で arm してもサービスユーザーが読める
    # ようにするため、ファイル自体は他者読み取り可にしておく（トークンは0600のまま）。
    fd = os.open(ARMED_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(f"{expiry:.0f}\n")
    ts = time.strftime("%H:%M:%S", time.localtime(expiry))
    print(f"ARMED: {minutes} 分間（{ts} まで）")


def disarm():
    try:
        os.remove(ARMED_FILE)
        print("DISARMED")
    except FileNotFoundError:
        print("すでに武装解除されています")


def status():
    try:
        with open(ARMED_FILE) as f:
            expiry = float(f.read().strip())
    except (OSError, ValueError):
        print("DISARMED")
        return
    remain = expiry - time.time()
    if remain <= 0:
        print("DISARMED（期限切れ）")
    else:
        ts = time.strftime("%H:%M:%S", time.localtime(expiry))
        print(f"ARMED: 残り {remain:.0f} 秒（{ts} まで）")


def main(argv=None):
    p = argparse.ArgumentParser(description="HIDBeam arm/disarm")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--minutes", type=int, help="武装する分数（1〜%d）" % MAX_MINUTES)
    g.add_argument("--disarm", action="store_true", help="即時武装解除")
    g.add_argument("--status", action="store_true", help="現在の状態を表示")
    args = p.parse_args(argv)

    if args.disarm:
        disarm()
    elif args.status:
        status()
    elif args.minutes is not None:
        arm(args.minutes)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
