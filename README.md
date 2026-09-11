<!--
---
id: day113
slug: hid-beam

title: "HIDBeam"

subtitle_ja: "Wi-Fi HIDキーボードブリッジ"
subtitle_en: "Wi-Fi Controlled HID Keyboard Bridge"

description_ja: "Raspberry Pi Zero W を Wi-Fi 経由で操作できる USB HID キーボードに変え、エアギャップ環境へ安全にキーストロークを送り込む自作ブリッジ"
description_en: "Turns a Raspberry Pi Zero W into a Wi-Fi controllable USB HID keyboard, injecting keystrokes into an air-gapped machine as a legitimate input channel"

category_ja:
  - エアギャップ
  - 物理セキュリティ
  - HID
category_en:
  - Air Gap
  - Physical Security
  - HID

difficulty: 3

tags:
  - air-gap
  - hid
  - usb-gadget
  - raspberry-pi
  - wifi
  - ducky-script
  - keystroke-injection
  - defensive-security

repo_url: "https://github.com/ipusiron/hid-beam"

hub: true
---
-->

# HIDBeam - Wi-Fi HIDキーボードブリッジ

![GitHub Repo stars](https://img.shields.io/github/stars/ipusiron/hid-beam?style=social)
![GitHub forks](https://img.shields.io/github/forks/ipusiron/hid-beam?style=social)
![GitHub last commit](https://img.shields.io/github/last-commit/ipusiron/hid-beam)
![GitHub license](https://img.shields.io/github/license/ipusiron/hid-beam)

**Day113 - 生成AIで作るセキュリティツール200**

**HIDBeam**は、Raspberry Pi Zero W を **Wi-Fi 経由で操作できる USB HID キーボード**に変えるブリッジソフトウェアです。
メインPCのブラウザーから送ったテキストや Ducky Script を、Pi Zero W が USB キーボードとして隣のマシンに打ち込みます。

ネットワーク接続を一切持たないエアギャップ環境に対して、**「キーボードとして振る舞う」以外のことができない**入力チャネルを提供します。これは攻撃ツール BadUSB と同じ HID エミュレーション技術を、正当な運用者が管理する前提で防御・運用に転用したものです。

> 本ツールは、自分が所有・管理する隔離環境へのデータ入力（正規運用）を目的としています。他者の機器への無断使用を意図するものではありません。

---

## 📖 書籍との関係

本ツールは、同人誌『エアギャップ・ブリッジ　隔離環境のデータ入力技法』（IPUSIRON 著）の**第7章 Wi-Fi HIDブリッジ方式**で解説したソフトウェアを、単体プロジェクトとして独立させ、継続的に機能改善しているものです。

- 書籍に収録したコードは、書籍が残るかぎり読者が再現できる**執筆時点のスナップショット**です。
- 本リポジトリーは、その**改良版の受け皿**です。書籍のサンプルリポジトリー（[sample_AirGapInput](https://github.com/ipusiron/sample_AirGapInput)）からも本プロジェクトへ誘導しています。

書籍側にはあえてこのリポジトリーの URL を印刷せず、書き換え可能なサンプルリポジトリー経由で本プロジェクトへ辿れるようにしています。書籍の記述と最新版のあいだにギャップが生じても、読者が最新版へ到達できるようにするためです。

---

## ✨ 新規性

既存の Wi-Fi HID 系ツール（Key Mime Pi、P4wnP1 A.L.O.A. など）に対して、本ツールは次の方針で設計しています。

- **Wi-Fi で遠隔操作できる HID キーボード**：物理的にキーボードを繋ぎ替えることなく、ブラウザーから隔離マシンへ入力できる。
- **最小実装による監査性**：Ducky Script インタープリター本体は約300行。防御用途で使う以上、全体を読み通せることを重視した。
- **第三者ホスト依存の排除**：ソースを書籍とリポジトリーに収録し、外部サービスの閉鎖に左右されない。

---

## 🔧 主な機能

- **Ducky Script 1.0 サブセットのインタープリター**：`STRING` / `ENTER` / `TAB` / `DELAY` / `GUI`・`CTRL`・`ALT`・`SHIFT` の修飾キー組み合わせ / `REM` に対応。
- **ブラウザー UI**：Flask 製の Web UI（ポート 8080）。スマートフォンや PC のブラウザーからスクリプトを送信できる。
- **US / JIS レイアウト切り替え**：Web UI から選択可能。
- **APモード対応**：hostapd + dnsmasq により Pi Zero W 自体を独立 Wi-Fi アクセスポイントにでき、既存ネットワークから分離した運用ができる。
- **systemd 常駐化**：起動時に HID ガジェットとブリッジサービスを自動起動。

---

## 🧩 構成ファイル

| ファイル | 役割 |
|---|---|
| `wifi_hid_bridge.py` | Ducky Script インタープリター本体（Python + Flask） |
| `setup-hid-gadget.sh` | USB HID キーボードガジェットを生成する configfs スクリプト |
| `hid-gadget.service` | 上記スクリプトを起動時に実行する systemd unit |
| `wifi-hid-bridge.service` | ブリッジ本体を常駐サービス化する systemd unit |
| `hostapd-ap.conf` | Pi Zero W を Wi-Fi AP として動作させる hostapd 設定 |
| `dnsmasq-ap.conf` | AP クライアントへの DHCP 配布設定（ゲートウェイ・DNS を配布しない） |
| `dhcpcd-ap.conf` | wlan0 への固定 IP 割り当て（192.168.100.1/24） |

---

## 🖥 動作環境

- Raspberry Pi Zero W（USB OTG / DWC2 対応機）
- Raspberry Pi OS（Bookworm 系で動作確認）
- Python 3 + Flask

セットアップ手順の詳細は書籍第7章、および本リポジトリーの各設定ファイル内コメントを参照してください。

---

## 🛡 セキュリティ上の位置づけ

HIDBeam は機能的には BadUSB（HID インジェクション）と同じ仕組みです。本ツールは以下を前提とした**防御・正規運用**のためのものです。

- 対象マシンは、**利用者自身が所有・管理する**隔離環境であること。
- 攻撃者の BadUSB と異なり、**デバイスの素性と挙動が運用者に既知**であること。

他者の機器に無断で使用する行為は、不正アクセス禁止法などに抵触するおそれがあります。

---

## 📄 ライセンス

MIT License。詳細は `LICENSE` を参照してください。

---

## 🔗 関連リンク

- 書籍サンプルリポジトリー: https://github.com/ipusiron/sample_AirGapInput
- 姉妹書（出力編）サンプル: https://github.com/ipusiron/sample_AirGapOutput
