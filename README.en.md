# HIDBeam - Wi-Fi Controlled HID Keyboard Bridge

**Day113 - Security Tools with Generative AI 200**

HIDBeam turns a Raspberry Pi Zero W into a **USB HID keyboard you can drive over Wi-Fi**. Text or Ducky Script sent from your main PC's browser is typed by the Pi Zero W into the adjacent machine as a USB keyboard.

It provides an input channel that **can do nothing but "act as a keyboard"** toward an air-gapped machine that has no network connectivity. It repurposes the same HID emulation technique as the BadUSB attack tool for defensive, operator-controlled use.

> This tool is intended for data input into isolated environments that you own and operate (legitimate use). It is not meant for unauthorized use against other people's devices.

---

## 📖 Relation to the book

This tool is the independent, continuously improved version of the software explained in **Chapter 7, "Wi-Fi HID Bridge"** of the doujin book *Air-Gap Bridge: Data Input Techniques for Isolated Environments* (by IPUSIRON).

- The code printed in the book is a **snapshot at the time of writing** that readers can reproduce for as long as the book exists.
- This repository is the **home of the improved version**. The book's sample repository ([sample_AirGapInput](https://github.com/ipusiron/sample_AirGapInput)) points here as well.

---

## ✨ What's different

Most existing Wi-Fi HID tools (Key Mime Pi, P4wnP1 A.L.O.A., Rubber Ducky, …) are attack-oriented and "fire and forget." This tool is the opposite: its focus is getting **data into** an isolated environment correctly and verifiably.

- **Verified data entry (a feature the others lack)**: it shows the SHA-256 of the payload plus a human-comparable word fingerprint (e.g. `gold-fern-lake-mint`). One-way keystroke injection can be silently corrupted by layout mismatch or dropped keys; by computing the same value on the target afterwards, the operator can verify the data arrived intact. Attack-oriented HID tools have no such check.
- **File inject and layout test**: inject a small file as base64 with on-target self-verification (`sha256sum`), and diagnose layout match before the real payload. Both are about getting data in *correctly* — absent from attack-oriented tools.
- **Preview and atomic execution**: the whole script is validated before sending, each keystroke's HID Usage ID is shown, and it is sent in a two-step "preview → confirm." Partial sends (typing halfway, then failing) cannot happen.
- **Disarmed-by-default safety**: nothing is sent unless armed locally; privilege separation, token auth and AP-only binding back it up.
- **Auditable by minimal implementation**: the parse/validation core is compact and meant to be read end to end — important for defensive use.
- **Remote HID keyboard over Wi-Fi**: type into an isolated machine from a browser without physically re-plugging a keyboard.
- **No third-party host dependency**: the source lives in the book and this repository, unaffected by any external service shutting down.

---

## 🔧 Features

- **Ducky Script 1.0 subset interpreter**: `STRING` / `ENTER` / `TAB` / `DELAY` / `GUI`/`CTRL`/`ALT`/`SHIFT` modifier combos / `REM`.
- **Whole-script validation and preview before sending**: the entire script is parsed and validated first, then a keystroke plan (command count, key events, estimated duration, modifiers) is shown. It also lists each keystroke's HID Usage ID and modifier, so you can see exactly what will be typed before sending. Nothing is sent until you approve. Partial sends (typing halfway, then failing) cannot happen.
- **Verified data-entry mode**: a mode that types raw data verbatim instead of DuckyScript. It shows an integrity fingerprint (SHA-256 + word tokens) of the payload so you can cross-check it on the target afterwards. For reliably getting config files, keys or base64 blobs into an isolated machine.
- **File inject mode**: base64- or hex-encodes a small file and types a script that restores it with `base64 -d` / `xxd -r -p` and verifies with a trailing `sha256sum`. hex uses only `0-9a-f` and works under both US and JIS. Gets config files or keys into an isolated machine that has no USB storage.
- **Layout test (matrix)**: types a string of symbols that sit on different keys across layouts, so you can confirm the target shows the reference before sending real data. It also shows a matrix of what that string would display under US / JIS / UK, so you can identify the target's actual layout from the row that matches — heading off the most common keystroke-injection failure.
- **Token authentication**: only someone who knows the per-boot token can operate it. Joining the AP is not enough to send.
- **Disarmed by default**: sending is disabled by default. It is enabled only for a short window when you run `hidbeam_arm.py` on the Pi; it auto-disarms when the window expires.
- **Physical arm/abort buttons (optional)**: with `hidbeam_gpio.py`, a GPIO button arms the device and another button aborts immediately (requires wiring).
- **Privilege separation**: the bridge runs as a dedicated non-root user and writes only to `/dev/hidg0`, under a systemd sandbox.
- **AP-interface-only binding**: it does not listen on all interfaces (0.0.0.0). Host/Origin checks and limits on request rate, input size and total duration are enforced.
- **Browser UI (bilingual, responsive)**: a Flask web UI (port 8080) that toggles Japanese/English and works at phone width.
- **US / JIS / UK layout switching**: selectable from the web UI (UK follows the standard ISO layout; verify any new layout with the layout test before real use).
- **AP mode**: with hostapd + dnsmasq the Pi Zero W becomes its own Wi-Fi access point, isolated from existing networks. A preflight check stops startup if the AP passphrase is left at its default.
- **Audit log**: records only the time, counts and result of each send — never the input text or the token.
- **systemd service**: the HID gadget and the bridge start automatically at boot.

---

## 🧩 Files

| File | Role |
|---|---|
| `wifi_hid_bridge.py` | Web UI and HID sending (Python + Flask): auth, arming, preview confirmation, limits, audit |
| `hid_core.py` | Ducky Script parse/validation core (hardware-independent); validates the whole script before sending |
| `hidbeam_arm.py` | Local command to arm for a short window (cannot arm over the network) |
| `hidbeam_gpio.py` | Daemon that arms/aborts via GPIO buttons (optional, requires wiring) |
| `hidbeam-preflight.sh` | Preflight check; blocks startup on a default/weak AP passphrase |
| `setup-hid-gadget.sh` | configfs script that creates the USB HID keyboard gadget |
| `99-hidbeam-hidg.rules` | udev rule letting the dedicated group `hidbeam` write to `/dev/hidg0` |
| `hid-gadget.service` | systemd unit running the script above at boot |
| `wifi-hid-bridge.service` | systemd unit running the bridge as a dedicated user (sandboxed) |
| `hostapd-ap.conf` | hostapd config to run the Pi Zero W as a Wi-Fi AP |
| `dnsmasq-ap.conf` | DHCP config for AP clients (no gateway/DNS handed out) |
| `dhcpcd-ap.conf` | static IP for wlan0 (192.168.100.1/24) |
| `requirements.txt` | Python dependency (Flask) |
| `tests/` | unit tests for `hid_core`, `hidbeam_arm`, `hidbeam_gpio` and the web gates |
| `CHECKLIST.md` / `CHECKLIST.en.md` | on-device (Pi Zero W) verification checklist (JA / EN) |

---

## 🖥 Requirements

- Raspberry Pi Zero W (USB OTG / DWC2 capable)
- Raspberry Pi OS (verified on Bookworm)
- Python 3 + Flask (`pip install -r requirements.txt`)

See Chapter 7 of the book and the in-file comments for detailed setup.

---

## ⚙️ Setup and operation

Run this once to create the dedicated user.

```bash
sudo groupadd --system hidbeam
sudo useradd  --system --no-create-home --shell /usr/sbin/nologin --gid hidbeam hidbeam
sudo cp 99-hidbeam-hidg.rules /etc/udev/rules.d/
sudo install -m 0755 hidbeam-preflight.sh /usr/local/bin/hidbeam-preflight.sh
sudo udevadm control --reload && sudo udevadm trigger
```

Each send follows this order.

1. Start the bridge (already running if installed as a systemd service). Read the per-boot token from the Pi's journal (`journalctl -u wifi-hid-bridge`) or from `/run/wifi-hid-bridge/token`.
2. Just before sending, arm on the Pi: `python3 hidbeam_arm.py --minutes 2` (max 10).
3. On your main PC, open `http://192.168.100.1:8080/` and enter the token.
4. Write the script, press "Preview", and review the keystroke plan.
5. If it looks right, press "Send this". The send button does not appear unless armed.
6. When done, disarm: `python3 hidbeam_arm.py --disarm`.

> Arming/disarming can only be done on the Pi; it cannot be done over the network. Even if the token leaks, sending will not start unless you arm locally.

---

## ✅ Tests

The parse/validation core, the arming logic and the web UI security gates are covered by unit tests that run without hardware.

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
```

GitHub Actions (`.github/workflows/ci.yml`) runs the tests on Python 3.10–3.12 and lints the shell scripts.

---

## 🛡 Security positioning

HIDBeam is functionally the same mechanism as BadUSB (HID injection). Because keystrokes can lead to arbitrary command execution or data manipulation on the target, **this tool cannot be proven "safe."** It only layers the following defenses so that unintended or unauthorized sends are less likely.

- Listens only on the AP interface (never on 0.0.0.0).
- Only someone who knows the per-boot token can operate it (AP membership is not enough).
- Disarmed by default; sends only when armed locally on the Pi.
- Sending is a two-step "preview → confirm"; a single request never sends.
- Host/Origin checks and limits on input size, send rate and total duration.
- Privilege separation (dedicated user + systemd sandbox).
- The audit log keeps only result summaries, never the input text or the token.

The tool assumes defensive, legitimate operation:

- The target is an isolated environment that you own and operate.
- Unlike an attacker's BadUSB, the device's identity and behavior are known to the operator.

Using it against other people's devices without authorization may violate laws such as the Unauthorized Computer Access Act (in Japan). Do not call it "safe" until you have verified connection, privileges, stop, restart and error paths on real hardware.

---

## 🔎 Known constraints and assumptions

- **Plain HTTP**: no TLS, because it assumes a local AP. The token travels in the clear within the AP. Connect only the operator's single device to the AP; with multiple devices, another device on the same AP could sniff the token.
- **AP-only binding by default**: it listens only on `192.168.100.1` by default. To run on an existing network instead of the AP, set `HIDBEAM_LISTEN_HOST` and `HIDBEAM_ALLOWED_HOSTS` for your environment (otherwise the bind IP and allowed host won't match and it won't be reachable).
- **systemd sandbox needs on-device tuning**: `SystemCallFilter`, `MemoryDenyWriteExecute`, `DeviceAllow` etc. are deliberately strict defaults. If it fails to start on real hardware, relax them one at a time while watching the journal to find the minimal set.
- **Development server**: it runs on Flask's development server. It targets single-operator, low-load LAN use; do not expose it to a public network.
- **Not verified on hardware**: connection, privileges, stop, restart and error paths on a real Raspberry Pi Zero W are unverified. Do not call it "safe" until they are. Use the steps in `CHECKLIST.md` for on-device verification.

---

## 📄 License

MIT License. See `LICENSE`.

---

## 🔗 Links

- Book sample repository: https://github.com/ipusiron/sample_AirGapInput
- Sister book (output edition) sample: https://github.com/ipusiron/sample_AirGapOutput
