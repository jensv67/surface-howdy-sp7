#!/usr/bin/env python3
# IR-Sensor-Stromsparwaechter: laesst die Kamera-Bridge nur laufen,
# wenn es kuerzlich Eingaben gab. Ohne Eingabe -> Sensor in Hardware-Standby.
import glob, os, select, subprocess, time

IDLE_STOP = int(os.environ.get("IDLE_STOP_SECONDS", "180"))
CHECK = 30
BRIDGE = "surface-camera-bridge.service"

def log(msg): print(msg, flush=True)
def running():
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", BRIDGE]).returncode == 0
def start():
    subprocess.run(["systemctl", "--user", "start", BRIDGE])
    log("Bridge gestartet (Eingabe erkannt)")
def stop():
    subprocess.run(["systemctl", "--user", "stop", BRIDGE])
    log("Bridge gestoppt - Sensor in Standby ({} s ohne Eingabe)".format(IDLE_STOP))

def open_inputs():
    fds, failed = {}, 0
    for p in glob.glob("/dev/input/event*"):
        try:
            fds[os.open(p, os.O_RDONLY | os.O_NONBLOCK)] = p
        except OSError:
            failed += 1
    return fds, failed

inputs, failed = open_inputs()
if not inputs:
    log("WARNUNG: kein einziges Eingabegeraet lesbar ({} Fehler). "
        "Fix: 'sudo usermod -aG input jens' + ab/anmelden.".format(failed))
elif failed:
    log("Hinweis: {} Eingabegeraete nicht lesbar (ignoriert)".format(failed))
is_running = running()
last_input = time.monotonic()
log("Waechter aktiv: {} Eingabegeraete, Bridge laeuft: {}".format(len(inputs), is_running))

while True:
    r, _, _ = select.select(list(inputs), [], [], CHECK)
    now = time.monotonic()
    if r:
        last_input = now
        for fd in r:
            try: os.read(fd, 4096)
            except OSError: pass
        if not is_running:
            start()
            is_running = True
    if is_running and now - last_input >= IDLE_STOP:
        stop()
        is_running = False
    expected = len(glob.glob("/dev/input/event*"))
    if len(inputs) != expected:  # Hotplug oder Berechtigungsproblem
        for fd in list(inputs): os.close(fd)
        inputs, failed = open_inputs()
        if inputs:
            log("Eingabegeraete neu geladen: {}".format(len(inputs)))
        else:
            log("WARNUNG: Eingabegeraete nicht mehr lesbar ({} Fehler)".format(failed))
