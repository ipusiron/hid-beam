<!--
---
id: day113
slug: hid-beam

title: "HIDBeam"

subtitle_ja: "Wi-Fi HIDキーボードブリッジ"
subtitle_en: "Wi-Fi Controlled HID Keyboard Bridge"

description_ja: "Raspberry Pi Zero W を Wi-Fi 経由で操作できる USB HID キーボードに変える自作ブリッジ。既定は武装解除で、トークン認証・プレビュー確認・権限分離で運用する"
description_en: "Turns a Raspberry Pi Zero W into a Wi-Fi controllable USB HID keyboard for operator-controlled input to an air-gapped machine; disarmed by default, with token auth, preview confirmation and privilege separation"

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
demo_url: ""

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
- 本リポジトリは、その**改良版の受け皿**です。書籍のサンプルリポジトリ（[sample_AirGapInput](https://github.com/ipusiron/sample_AirGapInput)）からも本プロジェクトへ誘導しています。

書籍側にはあえてこのリポジトリの URL を印刷せず、書き換え可能なサンプルリポジトリ経由で本プロジェクトへ辿れるようにしています。書籍の記述と最新版のあいだにギャップが生じても、読者が最新版へ到達できるようにするためです。

---

## ✨ 新規性

既存の Wi-Fi HID 系ツール（Key Mime Pi、P4wnP1 A.L.O.A.、Rubber Ducky など）の多くは攻撃志向で、キーを「撃ちっぱなし」にします。本ツールは逆に、隔離環境への **データ入力（投入）** を正しく・検証可能に行うことを主眼に設計しています。

- **検証付きデータ投入（他ツールにない独自機能）**：投入データの SHA-256 と、人間が読み上げて突き合わせられる語フィンガープリント（例 `gold-fern-lake-mint`）を表示する。一方向のキー入力はレイアウト不一致や取りこぼしで黙って壊れるが、投入後に対向機で同じ値を出して比べれば、壊れずに入ったかを運用者が検証できる。攻撃志向の HID ツールはこの検証を持たない。
- **ファイル投入とレイアウト診断**：小さなファイルを base64 で投入して対向機で `sha256sum` 自己検証し、本番前にレイアウト一致を診断する。いずれも「正しく入れる」ための独自機能で、攻撃志向のツールには無い。
- **送信前プレビューと原子的実行**：スクリプト全体を送信前に検証し、各打鍵の HID Usage ID まで提示してから「プレビュー → 確認」の2段階で送る。途中まで送って失敗する部分送信が起きない。
- **武装解除を既定とする安全設計**：ローカルで武装しないかぎり1バイトも送らない。権限分離・トークン認証・AP 限定待受を備える。
- **最小実装による監査性**：解析コアはコンパクトで、防御用途で使う以上、全体を読み通せることを重視した。
- **Wi-Fi で遠隔操作できる HID キーボード**：物理的にキーボードを繋ぎ替えることなく、ブラウザーから隔離マシンへ入力できる。
- **第三者ホスト依存の排除**：ソースを書籍とリポジトリに収録し、外部サービスの閉鎖に左右されない。

---

## 🔧 主な機能

- **Ducky Script 1.0 サブセットのインタープリター**：`STRING` / `ENTER` / `TAB` / `DELAY` / `GUI`・`CTRL`・`ALT`・`SHIFT` の修飾キー組み合わせ / `REM` に対応。
- **送信前の全体検証とプレビュー**：スクリプト全体を解析・検証してから打鍵計画（コマンド数・打鍵数・推定所要時間・修飾キー）を表示する。各打鍵の HID Usage ID と修飾キーも一覧表示するので、何が入力されるかを送信前に確認できる。承認するまで1バイトも送信しない。途中まで送って失敗する部分送信は起きない。
- **データ投入モード（検証付き）**：DuckyScript ではなく生データをそのまま逐語入力するモード。投入データの完全性フィンガープリント（SHA-256＋語トークン）を表示し、投入後に対向機で突き合わせられる。設定ファイル・鍵・base64 文字列などを隔離マシンへ確実に入れる用途向け。
- **ファイル投入モード**：小さなファイルを base64 または hex 化してキーストロークで投入し、対向機で `base64 -d`／`xxd -r -p` 復元・末尾の `sha256sum` 検証まで行うスクリプトを生成する。hex は `0-9a-f` のみで US/JIS 両対応。大きなファイルは分割送信でき、各ブロック後に対向機へ `chunk k/N` の進捗を表示するので、途中で止まっても到達点が分かる。USBストレージを使えない隔離環境へ設定ファイルや鍵を入れられる。
- **レイアウト診断（マトリクス）**：配置が異なる記号列を打ち、対向機の表示が参照と一致するか確認してから本番に進める。さらに、その記号列が US/JIS/UK それぞれでどう表示されるかをマトリクスで並記するので、対向機の表示と一致する行から実レイアウトを特定できる。キー入力最大の失敗要因（レイアウト不一致）を事前に潰す。
- **トークン認証**：起動ごとに生成するトークンを知る者だけが操作できる。AP に参加しただけでは送信できない。
- **武装解除を既定とする**：既定では送信できない。Pi 上で `hidbeam_arm.py` を実行して短時間だけ武装したときだけ送信できる。期限が切れると自動で武装解除される。
- **物理ボタンでの武装・中止（任意）**：`hidbeam_gpio.py` を使うと、GPIO に付けたボタンで武装し、別のボタンで即時中止できる（要配線）。
- **権限分離**：ブリッジは root ではなく専用ユーザーで動作し、`/dev/hidg0` だけに書き込む。systemd サンドボックスで権限を最小化する。
- **AP インターフェース限定の待ち受け**：全インターフェース（0.0.0.0）では待ち受けない。Host・Origin 検証と、送信回数・入力サイズ・総所要時間の上限を備える。
- **ブラウザー UI（日英・レスポンシブ）**：Flask 製の Web UI（ポート 8080）。日本語・英語を切り替えられ、スマートフォン幅でも使える。
- **US / JIS / UK レイアウト切り替え**：Web UI から選択可能（UK は ISO 配列の標準対応に基づく。新しい配列は本番前にレイアウト診断で確認する）。
- **AP モード対応**：hostapd + dnsmasq により Pi Zero W 自体を独立 Wi-Fi アクセスポイントにでき、既存ネットワークから分離した運用ができる。初期パスフレーズのままなら起動前チェックで停止する。
- **監査ログ**：送信の時刻・件数・結果だけを記録する。入力文字列やトークンは残さない。
- **systemd 常駐化**：起動時に HID ガジェットとブリッジサービスを自動起動。

---

## 🧩 構成ファイル

| ファイル | 役割 |
|---|---|
| `wifi_hid_bridge.py` | Web UI と HID 送信の本体（Python + Flask）。認証・武装・プレビュー確認・上限・監査を担う |
| `hid_core.py` | Ducky Script の解析・検証コア（ハードウェア非依存）。送信前にスクリプト全体を検証する |
| `hidbeam_arm.py` | Pi 上で短時間だけ武装するローカルコマンド（ネットワーク経由では武装できない） |
| `hidbeam_gpio.py` | GPIO の物理ボタンで武装・中止するデーモン（任意・要配線） |
| `hidbeam-preflight.sh` | 起動前チェック。AP の初期・弱いパスフレーズを検出して起動を止める |
| `setup-hid-gadget.sh` | USB HID キーボードガジェットを生成する configfs スクリプト |
| `99-hidbeam-hidg.rules` | `/dev/hidg0` を専用グループ hidbeam が書けるようにする udev ルール |
| `hid-gadget.service` | 上記スクリプトを起動時に実行する systemd unit |
| `wifi-hid-bridge.service` | ブリッジ本体を専用ユーザーで常駐サービス化する systemd unit（サンドボックス付き） |
| `hostapd-ap.conf` | Pi Zero W を Wi-Fi AP として動作させる hostapd 設定 |
| `dnsmasq-ap.conf` | AP クライアントへの DHCP 配布設定（ゲートウェイ・DNS を配布しない） |
| `dhcpcd-ap.conf` | wlan0 への固定 IP 割り当て（192.168.100.1/24） |
| `requirements.txt` | Python 依存（Flask） |
| `tests/` | `hid_core`・`hidbeam_arm`・`hidbeam_gpio`・Web ゲートの単体テスト |
| `CHECKLIST.md` / `CHECKLIST.en.md` | 実機（Pi Zero W）での動作確認チェックリスト（日英） |

---

## 🖥 動作環境

- Raspberry Pi Zero W（USB OTG / DWC2 対応機）
- Raspberry Pi OS（Bookworm 系で動作確認）
- Python 3 + Flask（`pip install -r requirements.txt`）

セットアップ手順の詳細は書籍第7章、および本リポジトリの各設定ファイル内コメントを参照してください。

---

## ⚙️ セットアップと運用

専用ユーザーで動かすため、初回だけ次を実行します。

```bash
sudo groupadd --system hidbeam
sudo useradd  --system --no-create-home --shell /usr/sbin/nologin --gid hidbeam hidbeam
sudo cp 99-hidbeam-hidg.rules /etc/udev/rules.d/
sudo install -m 0755 hidbeam-preflight.sh /usr/local/bin/hidbeam-preflight.sh
sudo udevadm control --reload && sudo udevadm trigger
```

日々の送信は次の順序でおこないます。

1. ブリッジを起動する（systemd 常駐なら起動済み）。起動時のトークンは Pi の journal（`journalctl -u wifi-hid-bridge`）か `/run/wifi-hid-bridge/token` で確認する。
2. 送信する直前に、Pi 上で武装する。`python3 hidbeam_arm.py --minutes 2`（最大10分）。
3. メイン PC のブラウザーで `http://192.168.100.1:8080/` を開き、トークンを入力する。
4. スクリプトを書いて「プレビュー」を押し、打鍵計画を確認する。
5. 内容に問題がなければ「この内容で送信」を押す。武装していなければ送信ボタンは出ない。
6. 使い終わったら武装解除する。`python3 hidbeam_arm.py --disarm`。

> 武装・武装解除は Pi 上でのみおこなえます。ネットワーク経由では武装できません。トークンが漏れても、手元で武装しないかぎり送信は始まりません。

---

## ✅ テスト

解析・検証コア、武装ロジック、Web UI のセキュリティゲートを単体テストで検証しています。実機がなくても実行できます。

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
```

GitHub Actions（`.github/workflows/ci.yml`）で、Python 3.10〜3.12 のテストとシェルスクリプトの検査を自動実行します。

---

## 🛡 セキュリティ上の位置づけ

HIDBeam は機能的には BadUSB（HID インジェクション）と同じ仕組みです。キーボード入力は接続先での任意コマンド実行やデータ操作につながり得るため、**本ツールが「安全」であることは証明できません**。意図しない送信・無断の送信が起こりにくくなるよう、次の多層防御を重ねているだけです。

- AP インターフェースだけで待ち受ける（0.0.0.0 では待ち受けない）。
- 起動ごとのトークンを知る者だけが操作できる（AP 参加だけでは不可）。
- 既定は武装解除。Pi 上でローカルに武装したときだけ送信できる。
- 送信は「プレビュー → 確認」の2段階。単発リクエストでは送信しない。
- Host・Origin 検証と、入力サイズ・送信回数・総所要時間の上限。
- 権限分離（専用ユーザー＋systemd サンドボックス）。
- 監査ログは結果の要約のみで、入力文字列やトークンは残さない。

本ツールは、以下を前提とした防御・正規運用のためのものです。

- 対象マシンは、利用者自身が所有・管理する隔離環境であること。
- 攻撃者の BadUSB と異なり、デバイスの素性と挙動が運用者に既知であること。

他者の機器に無断で使用する行為は、不正アクセス禁止法などに抵触するおそれがあります。実機での接続・権限・停止・再起動・異常系の確認が済むまでは、「安全」とは呼ばないでください。

---

## 🔎 既知の制約と前提

- **通信は平文 HTTP**：ローカル AP 前提のため TLS を張っていない。トークンは AP 内で平文で流れる。AP には運用者の1台だけをつなぐこと。複数台をつなぐと、同じ AP 上の端末からトークンを盗聴され得る。
- **AP 限定待受が既定**：既定で `192.168.100.1` だけで待ち受ける。AP を使わず既存ネットワーク上で動かす場合は、`HIDBEAM_LISTEN_HOST` と `HIDBEAM_ALLOWED_HOSTS` を自分の環境に合わせて設定する（未設定だと待受 IP と許可ホストが合わず届かない）。
- **systemd サンドボックスは実機調整前提**：`SystemCallFilter`・`MemoryDenyWriteExecute`・`DeviceAllow` などは強めの初期値である。実機で起動しない場合は、journal を見ながら該当指示を1つずつ緩めて最小集合を見つける。
- **開発用サーバー**：Flask の開発用サーバーで動かす。単一運用者・低負荷の LAN 用途を想定しており、公開ネットワークへは出さない。
- **実機未検証**：Raspberry Pi Zero W 実機での接続・権限・停止・再起動・異常系は未確認。確認が済むまで「安全」とは呼ばない。確認は `CHECKLIST.md` の手順でおこなう。

---

## 📄 ライセンス

MIT License。詳細は `LICENSE` を参照してください。

---

## 🔗 関連リンク

- 書籍サンプルリポジトリ: https://github.com/ipusiron/sample_AirGapInput
- 姉妹書（出力編）サンプル: https://github.com/ipusiron/sample_AirGapOutput
