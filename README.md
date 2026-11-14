# Howdy Face Unlock on the Surface Pro 7 (IPU4P / OV7251)

Howdy IR face authentication for the **Surface Pro 7 (Intel Ice Lake, IPU4P, 8086:8a19)** under Arch Linux with the [linux-surface](https://github.com/linux-surface/linux-surface) kernel — including a Y10→GRAY8 v4l2loopback bridge, a systemd user service, hardened Howdy PAM glue, and optional IR sensor power management.

Inspired by the Surface Pro 5 PoC (IPU3/OV7251) in [linux-surface issue #2197](https://github.com/linux-surface/linux-surface/issues/2197) and [lksmasin/surface-howdy-sp5](https://github.com/lksmasin/surface-howdy-sp5). Differences vs. that approach: IPU4 instead of IPU3, out-of-tree `ipu7-drivers` patched for the SP7, and a persistent Python bridge as a systemd user service instead of a C bridge spawned on demand.

Tested on: Arch Linux, linux-surface kernel 7.2.x, Plasma 6 / KDE (Wayland).

## Pipeline

```
ov7251 IR sensor (Y10, 640x480, mounted 90° portrait)
  → IPU4 media pipeline (media-ctl: sensor → CSI-2 → BE SOC capture 0)
  → /dev/video42 (raw Y10 in a 16-bit container)
  → v4l2-ctl --stream-mmap=4 --stream-to=stdout
  → Python bridge (numpy: 10→8-bit gain, rotate 90° CCW)
  → v4l2loopback /dev/video60 (GRAY8 480x640, ~30 fps)
  → Howdy (device_path=/dev/video60) → PAM (sudo, kscreenlocker, polkit)
```

## Requirements

- Arch Linux (or adapt package names) with linux-surface kernel + headers
- `base-devel`, `v4l-utils`, `python-numpy`, `v4l2loopback` (dkms or prebuilt modules)
- AUR: `howdy` (v2 layout), `pam-python-git` (py3 fork, installs as `pam_python3.so`)

## Repository layout

| Path | Purpose |
| --- | --- |
| `bridge/surface-camera-bridge-run` | Runner: configures the IPU4 media pipeline, execs the converter |
| `bridge/surface-ir-bridge.py` | Y10→GRAY8 converter (numpy gain + 90° rotation), writes to the loopback |
| `systemd/surface-camera-bridge.service` | systemd **user** unit for the bridge |
| `howdy/pam.py` | Hardened Howdy PAM glue (timeout, clean fallback, robust conversation) |
| `power/ir-sensor-power.py` + `.service` | Optional: input-driven watchdog, puts the IR sensor into runtime-PM standby when idle |
| `v4l2loopback.conf` | modprobe config for the loopback device |
| `drivers/` | The patched ipu7-driver tree for the SP7 (see step 1) |

## Setup

### 1. Camera drivers (the hard part)

Stock `ipu7-drivers` don't know the SP7's ov7251 IR sensor. Required patches: ov7251 Y10 + exposure/gain/STROBE (IR LED), the IPU4P 8086:8a19 PCI ID, and an ACPI/INT3472 power-resource glue module (`surfacepro7_acpi`). Build against your running kernel:

```bash
# /lib/modules/$(uname -r)/build must exist (kernel headers)
make modules -j"$(nproc)" KERNEL_VERSION="$(uname -r)" KCFLAGS="-O2 -march=x86-64 -mtune=generic -fno-strict-aliasing"
sudo install -m 644 *.ko "/usr/lib/modules/$(uname -r)/extra/"
sudo depmod
```

Options matter: kernel modules must not be stripped or LTO'd (`!strip !debug !lto` in makepkg). The PKGBUILD in `drivers/` packages this so a pacman hook rebuilds it on every kernel update.

### 2. v4l2loopback

```ini
# /etc/modprobe.d/v4l2loopback.conf
options v4l2loopback video_nr=60 card_label="Surface Pro 7 IR Bridge" exclusive_caps=1
```

`exclusive_caps=1` makes the loopback advertise capture-only caps — required for GStreamer/OpenCV to accept it as a camera.

### 3. The bridge

```bash
install -m 755 bridge/surface-camera-bridge-run bridge/surface-ir-bridge.py ~/.local/bin/
```

`IR_GAIN` (default 2) controls brightness — the IR sensor delivers 10-bit values around 11–160, so the gain matters for Howdy's `dark_threshold`. `IR_ROT` (default 1 = 90° CCW) fixes the portrait mounting.

### 4. systemd user service

```bash
install -Dm644 systemd/surface-camera-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now surface-camera-bridge.service
```

Verify with `journalctl --user -u surface-camera-bridge.service -f` — a healthy bridge prints an fps line (~29–30) about once per second. **If those lines stop while the unit still shows `active (running)`, the bridge is wedged** (see Gotchas).

### 5. Howdy + PAM

```bash
sudo sed -i 's|device_path.*|device_path = /dev/video60|' /usr/lib/security/howdy/config.ini
sudo howdy add
sudo chmod -R a+rX /usr/lib/security/howdy/
```

PAM line (identical everywhere you want face auth):

```
auth    sufficient    pam_python3.so    /usr/lib/security/howdy/pam.py
```

- `sudo`: insert as line 2 of `/etc/pam.d/sudo`
- `kscreenlocker` (Plasma 6): copy `/usr/lib/pam.d/kde` to `/etc/pam.d/kde` and add the line (the /etc copy shadows the package file)
- `polkit`: same procedure for `/etc/pam.d/polkit-1`

**Plasma 6 trap:** `kscreenlocker_greet` runs the PAM conversation as your uid, not root — if `/usr/lib/security/howdy/` isn't world-readable/traversable you get "Can not open module: permission denied", and every Howdy package update resets those permissions. Automate with a pacman hook.

### 6. Hardened pam.py

Stock `pam.py` has three bugs that bite exactly when the camera misbehaves:

1. Unknown compare.py exit codes (dead camera → exit 1) return `PAM_SYSTEM_ERR` and can poison pam_unix's password prompt ("a password is required" instead of a clean retry).
2. `subprocess.call` has no timeout — a wedged camera hangs sudo *forever*.
3. Unprotected `pamh.conversation` calls can abort the whole stack.

This repo ships the hardened version (15 s timeout + kill, all errors → clean `PAM_AUTH_ERR`, conversation wrapped in try/except):

```bash
sudo cp howdy/pam.py /usr/lib/security/howdy/pam.py
sudo chmod -R a+rX /usr/lib/security/howdy/
```

With that in place the password fallback works even with the camera dead — tested.

### 7. Optional: IR sensor power management

The bridge costs ~20% of a core while streaming. A small watchdog (user service) stops the bridge after 3 min without input — the sensor drops into runtime-PM standby (`/sys/class/video4linux/video42/device/power/runtime_status` → `suspended`) — and restarts it on the first keypress. Face unlock after wake still works; if Howdy loses the ~1–2 s warmup race, press Enter on the empty password prompt to re-trigger the scan.

```bash
# Wayland: the session user has no read access to /dev/input/event* by default
sudo usermod -aG input "$USER"   # then log out and back in
install -m 755 power/ir-sensor-power.py ~/.local/bin/
install -Dm644 power/ir-sensor-power.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ir-sensor-power.service
```

`IDLE_STOP_SECONDS` (default 180) configures the idle timeout.

### 8. Kernel updates — the install-line trap

If you use a modprobe `install` line for the IR strobe parameter (as this SP7 setup does, so `experimental_strobe_output=1` is passed to the IR LED), do **not** hardcode the kernel version in the module path. After a kernel update the stale path makes `insmod` fail with ENOENT, the ov7251 never binds, and the bridge crash-loops into systemd's `start-limit-hit` — while ov5693/ov8865 (which load normally via depmod) keep working, which makes it look like a random failure. Version-safe form:

```ini
# /etc/modprobe.d/surfacepro7-camera-ir.conf
install ov7251 /usr/sbin/insmod /usr/lib/modules/$(uname -r)/extra/surfacepro7-camera-ir/ov7251.ko experimental_strobe_output=1
```

modprobe runs install commands through the shell, so `$(uname -r)` expands on every call and the line survives every kernel update. Recovery if you already hit the start limit: `systemctl --user reset-failed surface-camera-bridge.service` before restarting the unit.

## Gotchas (these cost me days — save yours)

1. **Y10 is supported by almost nothing in userspace** (GStreamer, ffmpeg, OpenCV V4L2 backend) — hence the custom bridge; the loopback is GRAY8.
2. **The IPU4 capture driver accepts nearly any format at S_FMT** — wrong formats only blow up at STREAMON with EPIPE/ENOLINK. Validate early, not late.
3. **Wedge behavior:** after a failed STREAMON the ISYS is poisoned (EBUSY); only a reboot clears it quickly. A wedged-but-running bridge looks healthy in systemd — the journal fps counter is your truth.
4. **Don't hardcode v4l2 ioctl structs** — struct sizes vary across kernels (ENOTTY); let `v4l2-ctl` set formats.
5. Frequent stop/start cycles (power management) hit the wedge occasionally — the `Restart=always` unit recovers automatically.

## Credits

- [lksmasin/surface-howdy-sp5](https://github.com/lksmasin/surface-howdy-sp5) and [linux-surface issue #2197](https://github.com/linux-surface/linux-surface/issues/2197) for the original SP5 PoC and the inspiration
- The [linux-surface](https://github.com/linux-surface/linux-surface) project for the kernel that makes this device usable at all
- This setup — the bridge, the PAM hardening, and the automation scripts — was developed with the help of **Vibe**, an AI agent by [Mistral AI](https://mistral.ai)
