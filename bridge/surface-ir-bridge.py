#!/usr/bin/env python3
import os, signal, subprocess, sys
import numpy as np

CAP = sys.argv[1] if len(sys.argv) > 1 else "/dev/video42"
W, H = 640, 480
GAIN = float(os.environ.get("IR_GAIN", "2"))
ROT = int(os.environ.get("IR_ROT", "1"))
FRAME_IN = W * H * 2

if ROT % 2:
    OW, OH = H, W
else:
    OW, OH = W, H
print("bridge: GRAY8", OW, "x", OH, "-> ffmpeg v4l2 stage", file=sys.stderr, flush=True)

ctl = subprocess.Popen(
    ["v4l2-ctl", "-d", CAP,
     "--set-fmt-video=width=%d,height=%d,pixelformat=Y10 " % (W, H),
     "--stream-mmap=4", "--stream-to=/dev/stdout"],
    stdout=subprocess.PIPE)

def stop(*_):
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
while True:
    data = read_exact(FRAME_IN)
    if data is None:
        print("v4l2-ctl stream ended", file=sys.stderr, flush=True)
        break
    g8 = (np.frombuffer(data, dtype="<u2") * GAIN).clip(0, 255).astype(np.uint8)
    img = np.rot90(g8.reshape(H, W), ROT)
    out.write(img.tobytes())
    out.flush()
