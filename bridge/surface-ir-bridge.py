#!/usr/bin/env python3
import os, signal, subprocess, sys
import numpy as np

CAP = sys.argv[1] if len(sys.argv) > 1 else "/dev/video42"
OUT = "/dev/video60"
W, H = 640, 480
GAIN = float(os.environ.get("IR_GAIN", "2"))
ROT = int(os.environ.get("IR_ROT", "1"))
FRAME_IN = W * H * 2
if ROT % 2:
    OW, OH = H, W
else:
    OW, OH = W, H

out_fd = os.open(OUT, os.O_RDWR)

grey = subprocess.run(
    ["v4l2-ctl", "-d", OUT, "--set-fmt-video-out=width=%d,height=%d,pixelformat=GREY" % (OW, OH)],
    capture_output=True).returncode == 0
if not grey:
    subprocess.run(
        ["v4l2-ctl", "-d", OUT, "--set-fmt-video-out=width=%d,height=%d,pixelformat=YUYV" % (OW, OH)],
        check=True)
print("loopback format:", "GRAY8" if grey else "YUYV", OW, "x", OH, flush=True)

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

while True:
    data = read_exact(FRAME_IN)
    if data is None:
        print("v4l2-ctl stream ended", flush=True)
        break
    g8 = (np.frombuffer(data, dtype="<u2") * GAIN).clip(0, 255).astype(np.uint8)
    img = np.rot90(g8.reshape(H, W), ROT)
    if grey:
        os.write(out_fd, img.tobytes())
    else:
        g = img.reshape(-1)
        buf = np.empty((g.size // 2, 4), dtype=np.uint8)
        buf[:, 0] = g[0::2]
        buf[:, 1] = 128
        buf[:, 2] = g[1::2]
        buf[:, 3] = 128
        os.write(out_fd, buf.tobytes())
