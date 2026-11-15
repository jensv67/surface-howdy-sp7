#!/usr/bin/env python3
import os, signal, subprocess, sys, time
import numpy as np

CAP = sys.argv[1] if len(sys.argv) > 1 else "/dev/video42"
W, H = 640, 480
GAIN = float(os.environ.get("IR_GAIN", "2"))
ROT = int(os.environ.get("IR_ROT", "1"))
FRAME_IN = W * H * 2
MAX_FAILS = int(os.environ.get("IR_START_TRIES", "5"))

if ROT % 2:
    OW, OH = H, W
else:
    OW, OH = W, H
print("bridge: GRAY8", OW, "x", OH, "-> ffmpeg v4l2 stage", file=sys.stderr, flush=True)

ctl = None
def stop(*_):
    if ctl is not None:
        ctl.terminate()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

def read_exact(n):
    buf = bytearray()
    while len(buf) < n:
        chunk = ctl.stdout.read(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

out = sys.stdout.buffer
fails = 0
try:
    while True:
        ctl = subprocess.Popen(
            ["v4l2-ctl", "-d", CAP,
             "--set-fmt-video=width=%d,height=%d,pixelformat=Y10 " % (W, H),
             "--stream-mmap=4", "--stream-to=/dev/stdout"],
            stdout=subprocess.PIPE)
        frames = 0
        data = read_exact(FRAME_IN)
        while data is not None:
            g8 = (np.frombuffer(data, dtype="<u2") * GAIN).clip(0, 255).astype(np.uint8)
            img = np.rot90(g8.reshape(H, W), ROT)
            out.write(img.tobytes())
            out.flush()
            frames += 1
            data = read_exact(FRAME_IN)
        ctl.terminate()
        ctl.wait()
        ctl = None
        if frames >= 30:
            print("v4l2-ctl stream ended after", frames, "frames", file=sys.stderr, flush=True)
            fails = 0
        else:
            fails += 1
            print("capture start failed (wake race?), retry", fails, "of", MAX_FAILS, file=sys.stderr, flush=True)
        if fails >= MAX_FAILS:
            print("capture failed", fails, "times, giving up", file=sys.stderr, flush=True)
            sys.exit(1)
        time.sleep(2)
except BrokenPipeError:
    sys.exit(0)
