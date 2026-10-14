# HIDBeam 実機チェックリスト

実機（Raspberry Pi Zero W）での動作確認手順である。ソフトウェアのユニットテストは
実機なしで通るが、USB HID ガジェット・systemd サンドボックス・udev・hostapd は
実機でしか確認できない。**このチェックリストがすべて通るまで「安全」とは呼ばない。**

対象マシンは、利用者自身が所有・管理する隔離環境に限る。

---

## 1. 準備

- [ ] Raspberry Pi OS Lite（Bookworm 系）を書き込み、起動できる
- [ ] `/boot/firmware/config.txt` に `dtoverlay=dwc2`
- [ ] `/boot/firmware/cmdline.txt` の `rootwait` の後に `modules-load=dwc2`
- [ ] `/etc/modules` に `libcomposite`
- [ ] `pip install -r requirements.txt`（Flask）
- [ ] 専用ユーザーを作成した
      `sudo groupadd --system hidbeam`
      `sudo useradd --system --no-create-home --shell /usr/sbin/nologin --gid hidbeam hidbeam`

## 2. USB HID ガジェット

- [ ] `setup-hid-gadget.sh` を `/usr/local/bin/` に置き、実行権限を付けた
- [ ] スクリプト実行後に `/dev/hidg0` が生成される
- [ ] `/dev/hidg0` のグループが `hidbeam`、モードが `0660`（udev ルール設置後）
      `ls -l /dev/hidg0`
- [ ] Mac/PC に USB 接続すると、キーボードデバイスとして認識される

## 3. 権限分離（root で動かさない）

- [ ] `99-hidbeam-hidg.rules` を `/etc/udev/rules.d/` に置き、reload・trigger した
- [ ] `wifi-hid-bridge.service` の `User=hidbeam` で起動する
- [ ] サービスが root で動いていない
      `systemctl show -p MainPID wifi-hid-bridge` → `ps -o user= -p <PID>` が `hidbeam`
- [ ] systemd サンドボックスで起動が失敗しないこと。失敗する場合は journal を見て
      `SystemCallFilter` / `MemoryDenyWriteExecute` / `DeviceAllow` を1つずつ緩めて最小集合を特定した
      `journalctl -u wifi-hid-bridge -b`

## 4. 起動前チェック（preflight）

- [ ] AP 運用時: `hostapd.conf` の `wpa_passphrase` を初期値から変更した
- [ ] 初期値 `CHANGE_ME_STRONG_PASSPHRASE` のままだとサービスが起動に失敗する（期待どおり）
- [ ] 8〜63文字外のパスフレーズでも起動に失敗する
- [ ] AP を使わない構成では preflight が素通りし、`HIDBEAM_LISTEN_HOST` と
      `HIDBEAM_ALLOWED_HOSTS` を環境に合わせて設定した

## 5. トークンと到達範囲

- [ ] 起動ごとのトークンが journal または `/run/wifi-hid-bridge/token`（0600）で取得できる
- [ ] ブラウザーで `http://192.168.100.1:8080/` を開くとトークン入力画面が出る
- [ ] 誤ったトークンでは認証できない。数回失敗するとロックされる
- [ ] 別インターフェース（例: 有線LAN側 IP）からは到達できない
- [ ] 別名ホスト（Host ヘッダー不一致）では 400 になる

## 6. 武装と送信（2段階）

- [ ] 武装していない状態では送信ボタンが出ない
- [ ] `sudo -u hidbeam python3 hidbeam_arm.py --minutes 2` で武装できる
- [ ] 「プレビュー」で打鍵計画と Usage ID の内訳が表示される（まだ送信されない）
- [ ] プレビュー内容を接続先で目視確認してから「この内容で送信」を押す
- [ ] 複数行スクリプト（例: `DELAY 500` / `STRING ...` / `ENTER`）が意図どおり入力される
- [ ] US / JIS レイアウト切り替えで記号が正しく入力される
- [ ] 武装の期限（2分）が切れると送信できなくなる
- [ ] `hidbeam_arm.py --disarm` で即時に送信不可へ戻る

## 7. GPIO 物理 arm/abort（任意・配線した場合のみ）

- [ ] ARM ボタン押下で武装される（`hidbeam_gpio.py` 起動中）
- [ ] ABORT ボタン押下で即時に武装解除される
- [ ] チャタリングで多重武装にならない（bouncetime）

## 8. 異常系・監査

- [ ] 未対応文字を含むスクリプトはプレビューで拒否され、1バイトも送信されない
- [ ] 上限（入力サイズ・コマンド数・DELAY・打鍵数・所要時間）超過が拒否される
- [ ] 送信中にケーブルを抜く等で書き込み失敗しても、キーが押しっぱなしにならない
- [ ] 監査ログ（`/run/wifi-hid-bridge/audit.log`）に時刻・件数・結果のみが残り、
      入力文字列やトークンが残っていない
- [ ] 再起動後、トークンが新しくなり、武装状態が解除されている

---

すべて確認できたら、確認日・OS バージョン・緩めた systemd 指示があればそれを
`business/research/try200_audit/` の実装結果へ追記する。
