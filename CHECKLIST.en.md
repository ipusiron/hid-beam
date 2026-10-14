# HIDBeam on-device checklist

Verification steps on real hardware (Raspberry Pi Zero W). The software unit tests
pass without hardware, but the USB HID gadget, systemd sandbox, udev and hostapd can
only be checked on a device. **Do not call it "safe" until all of this passes.**

The target must be an isolated environment that you own and operate.

---

## 1. Prerequisites

- [ ] Raspberry Pi OS Lite (Bookworm) boots
- [ ] `dtoverlay=dwc2` in `/boot/firmware/config.txt`
- [ ] `modules-load=dwc2` after `rootwait` in `/boot/firmware/cmdline.txt`
- [ ] `libcomposite` in `/etc/modules`
- [ ] `pip install -r requirements.txt` (Flask)
- [ ] Dedicated user created:
      `sudo groupadd --system hidbeam`
      `sudo useradd --system --no-create-home --shell /usr/sbin/nologin --gid hidbeam hidbeam`

## 2. USB HID gadget

- [ ] `setup-hid-gadget.sh` placed in `/usr/local/bin/` and executable
- [ ] `/dev/hidg0` appears after running it
- [ ] `/dev/hidg0` is group `hidbeam`, mode `0660` (after the udev rule) — `ls -l /dev/hidg0`
- [ ] The Mac/PC recognizes it as a keyboard device over USB

## 3. Privilege separation (do not run as root)

- [ ] `99-hidbeam-hidg.rules` placed in `/etc/udev/rules.d/`, reloaded and triggered
- [ ] The service starts as `User=hidbeam`
- [ ] It is not running as root — `systemctl show -p MainPID wifi-hid-bridge` then `ps -o user= -p <PID>` is `hidbeam`
- [ ] The systemd sandbox does not block startup. If it does, read the journal and relax
      `SystemCallFilter` / `MemoryDenyWriteExecute` / `DeviceAllow` one at a time to find the minimal set

## 4. Preflight

- [ ] AP mode: changed `wpa_passphrase` from the default
- [ ] With the default `CHANGE_ME_STRONG_PASSPHRASE`, the service fails to start (expected)
- [ ] A passphrase outside 8–63 chars also fails to start
- [ ] Without AP, preflight passes and `HIDBEAM_LISTEN_HOST` / `HIDBEAM_ALLOWED_HOSTS` are set for the network

## 5. Token and reachability

- [ ] The per-boot token is readable from the journal or `/run/wifi-hid-bridge/token` (0600)
- [ ] The browser at `http://192.168.100.1:8080/` shows the token entry screen
- [ ] A wrong token fails; repeated failures lock out
- [ ] Unreachable from another interface (e.g. wired LAN IP)
- [ ] A mismatched Host header returns 400

## 6. Arming and sending (two-step)

- [ ] The send button is absent while disarmed
- [ ] `sudo -u hidbeam python3 hidbeam_arm.py --minutes 2` arms it
- [ ] Preview shows the keystroke plan and HID Usage ID breakdown (nothing sent yet)
- [ ] Run a layout test first and confirm the target shows the reference string
- [ ] For data entry / file inject, confirm the integrity fingerprint, then verify it on the target afterwards (`sha256sum`)
- [ ] Review the preview on the target, then press "Send this"
- [ ] A multi-line script (e.g. `DELAY 500` / `STRING ...` / `ENTER`) is typed as intended
- [ ] Symbols are correct under US / JIS layout switching
- [ ] After the arm window (2 min) expires, sending is refused
- [ ] `hidbeam_arm.py --disarm` returns to the no-send state

## 7. GPIO physical arm/abort (optional, only if wired)

- [ ] The ARM button arms (with `hidbeam_gpio.py` running)
- [ ] The ABORT button disarms immediately
- [ ] Chatter does not cause multiple arms (bouncetime)

## 8. Error paths and audit

- [ ] A script with unsupported characters is rejected at preview; nothing is sent
- [ ] Over-limit (input size, command count, DELAY, key events, duration) is rejected
- [ ] If a write fails mid-send (e.g. cable unplugged), keys are not left pressed
- [ ] The audit log (`/run/wifi-hid-bridge/audit.log`) keeps only time, counts and result — no input text or token
- [ ] After reboot, the token is new and the device is disarmed

---

Once everything passes, record the date, OS version and any relaxed systemd directives
in the implementation result under `business/research/try200_audit/`.
