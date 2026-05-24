#!/usr/bin/env python3
"""
Client-side stream helpers for the drone families found in Tercaso Fly.

Usage examples:
  python3 drone_streamer.py auto --drone-ip 172.19.100.1
  python3 drone_streamer.py fh --drone-ip 172.19.100.1
  python3 drone_streamer.py xr872 --drone-ip 192.168.28.1
  python3 drone_streamer.py jllw --drone-ip 192.168.0.1 --listen-port 7070
  python3 drone_streamer.py mr100 --drone-ip 192.168.218.1
"""

import argparse
import shutil
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from threading import Thread


COMMON_RTSP_PATHS = (
    "",
    "/",
    "/live",
    "/live.sdp",
    "/live/ch00_0",
    "/live/ch00_1",
    "/ch0_0.h264",
    "/ch0_1.h264",
    "/11",
    "/1",
    "/stream0",
    "/stream1",
    "/videoMain",
    "/media/video1",
)

XR872_VIDEO_START = bytes([0xCC, 0x5A, 0x01, 0x82, 0x02, 0x36, 0xB7])
XR872_VIDEO_STOP = bytes([0xCC, 0x5A, 0x01, 0x82, 0x02, 0x37, 0xB6])
XR872_RXTX_PORT = 7080


def have(cmd):
    return shutil.which(cmd) is not None


def tcp_open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def rtsp_url(host, path, port=554):
    port_part = "" if port == 554 else f":{port}"
    return f"rtsp://{host}{port_part}{path}"


def try_ffplay(url):
    if not have("ffplay"):
        print("ffplay not found. Install it to open the stream.")
        print(f"Try manually after installing: ffplay -fflags nobuffer -flags low_delay {url}")
        return 2
    cmd = ["ffplay", "-fflags", "nobuffer", "-flags", "low_delay", "-framedrop", url]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


def probe_rtsp(host, ports=(554, 8554, 7070, 8080), paths=COMMON_RTSP_PATHS):
    if not have("ffprobe"):
        print("ffprobe not found. Install it in order to allow the program to probe the stream.")
        return None

    for port in ports:
        if not tcp_open(host, port):
            continue
        for path in paths:
            url = rtsp_url(host, path, port)
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-rtsp_transport",
                "tcp",
                "-timeout",
                "1500000",
                "-show_streams",
                url,
            ]
            print(f"Probing {url}")
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                print(f"Found RTSP stream: {url}")
                return url
    return None


class MjpegHandler(BaseHTTPRequestHandler):
    frames = None
    stats = None

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path == "/status":
            last_frame_at = self.stats.get("last_frame_at", 0.0) if self.stats else 0.0
            last_battery_at = self.stats.get("last_battery_at", 0.0) if self.stats else 0.0
            age = None if last_frame_at == 0.0 else time.time() - last_frame_at
            battery_age = None if last_battery_at == 0.0 else time.time() - last_battery_at
            body = {
                "frames": self.stats.get("frames", 0) if self.stats else 0,
                "last_frame_age_seconds": age,
                "battery_percent": self.stats.get("battery_percent") if self.stats else None,
                "battery_raw": self.stats.get("battery_raw") if self.stats else None,
                "battery_gauge": battery_gauge(self.stats.get("battery_percent") if self.stats else None),
                "last_battery_age_seconds": battery_age,
            }
            payload = (str(body) + "\n").encode("ascii")
            self.send_response(200)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(200)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        while True:
            jpg = self.frames.get()
            self.wfile.write(b"--frame\r\n")
            self.wfile.write(b"Content-Type: image/jpeg\r\n")
            self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode("ascii"))
            self.wfile.write(jpg + b"\r\n")


def serve_mjpeg(frames, host, port, stats):
    MjpegHandler.frames = frames
    MjpegHandler.stats = stats
    server = ThreadingHTTPServer((host, port), MjpegHandler)
    print(f"HTTP MJPEG server listening on http://{host}:{port}/", flush=True)
    print(f"Status: http://{host}:{port}/status", flush=True)
    server.serve_forever()


def push_frame(frames, jpg, stats=None):
    if frames.full():
        try:
            frames.get_nowait()
        except Exception:
            pass
    if stats is not None:
        stats["frames"] = stats.get("frames", 0) + 1
        stats["last_frame_at"] = time.time()
    frames.put(jpg)


def battery_gauge(percent):
    if percent is None:
        return "[----------] waiting"
    percent = max(0, min(100, int(percent)))
    filled = round(percent / 10)
    return f"[{'#' * filled}{'-' * (10 - filled)}] {percent}%"


def update_battery(stats, raw_value):
    if stats is None:
        return
    percent = max(0, min(100, raw_value & 0xFF))
    stats["battery_raw"] = raw_value & 0xFF
    stats["battery_percent"] = percent
    stats["last_battery_at"] = time.time()


def xr872_reader(drone_ip, video_port, frames, stats):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("", video_port))
    try:
        sock.sendto(XR872_VIDEO_START, (drone_ip, XR872_RXTX_PORT))
        print(f"Sent XR872 video start to {drone_ip}:{XR872_RXTX_PORT}", flush=True)
    except OSError:
        pass

    buf = bytearray()
    frame_id = None
    last_pkt = 0
    last_frame_at = 0.0

    try:
        while True:
            pkt, _ = sock.recvfrom(2048)
            if len(pkt) < 4:
                continue

            update_battery(stats, pkt[3])
            is_last = pkt[1] == 1
            if len(pkt) != 1472 and not is_last:
                continue

            fid = pkt[0]
            pkt_no = pkt[2]
            if pkt_no == 1:
                buf = bytearray()
                frame_id = fid
                last_pkt = 1
            elif frame_id != fid or ((last_pkt + 1) & 0xFF) != pkt_no:
                continue
            last_pkt = pkt_no

            buf.extend(pkt[4:])
            if is_last and len(buf) > 4 and buf[:2] == b"\xff\xd8" and buf[-2:] == b"\xff\xd9":
                push_frame(frames, bytes(buf), stats)
                now = time.time()
                if now - last_frame_at > 5:
                    print(f"XR872 receiving JPEG frames, last size {len(buf)} bytes", flush=True)
                    last_frame_at = now
    finally:
        try:
            sock.sendto(XR872_VIDEO_STOP, (drone_ip, XR872_RXTX_PORT))
        except OSError:
            pass
        sock.close()


def jllw_reader(listen_port, frames, stats):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", listen_port))

    buf = bytearray()
    last_pkt = 0
    last_frame_at = 0.0

    while True:
        pkt, _ = sock.recvfrom(2048)
        size = len(pkt)
        if size >= 54 and pkt[0:3] == b"cc\x03":
            is_last = not (pkt[52] == 120 and pkt[53] == 5)
            if size != 1454 and not is_last:
                continue

            pkt_no = pkt[48]
            if pkt_no == 1:
                buf = bytearray()
                last_pkt = 1
            elif ((last_pkt + 1) & 0xFF) != pkt_no:
                last_pkt = -1
                continue
            last_pkt = pkt_no

            buf.extend(pkt[54:])
            if is_last and len(buf) > 2 and buf[:2] == b"\xff\xd8":
                push_frame(frames, bytes(buf), stats)
                now = time.time()
                if now - last_frame_at > 5:
                    print(f"JLLW receiving JPEG frames, last size {len(buf)} bytes")
                    last_frame_at = now


def run_mjpeg_mode(reader, args):
    frames = Queue(maxsize=1000)
    stats = {"frames": 0, "last_frame_at": 0.0, "battery_percent": None, "battery_raw": None, "last_battery_at": 0.0}
    Thread(target=reader, args=args, daemon=True).start()
    serve_mjpeg(frames, "127.0.0.1", args[-1], stats)


def mode_xr872(args):
    frames = Queue(maxsize=1000)
    stats = {"frames": 0, "last_frame_at": 0.0, "battery_percent": None, "battery_raw": None, "last_battery_at": 0.0}
    Thread(target=xr872_reader, args=(args.drone_ip, args.video_port, frames, stats), daemon=True).start()
    serve_mjpeg(frames, args.http_host, args.http_port, stats)


def mode_jllw(args):
    frames = Queue(maxsize=1000)
    stats = {"frames": 0, "last_frame_at": 0.0, "battery_percent": None, "battery_raw": None, "last_battery_at": 0.0}
    Thread(target=jllw_reader, args=(args.listen_port, frames, stats), daemon=True).start()
    serve_mjpeg(frames, args.http_host, args.http_port, stats)


def mode_rtsp_family(args):
    url = args.url or probe_rtsp(args.drone_ip)
    if not url:
        print("No RTSP stream found with the common paths.")
        print("Run Wireshark or tcpdump while the Android app connects to learn the exact URL.")
        return 1
    return try_ffplay(url)


def mode_fh(args):
    print("FH family detected/selected.")
    print(f"App default FH login host is 172.19.10.1:8866, but you supplied {args.drone_ip}.")
    print("Credentials in the app: user=guanxukeji password=gxrdw60 aesKey=guanxukj@fh8620.")

    if tcp_open(args.drone_ip, args.port):
        print(f"TCP {args.drone_ip}:{args.port} is open. That matches the FH proprietary SDK login service.")
    else:
        print(f"TCP {args.drone_ip}:{args.port} did not answer from this Mac.")

    print("Checking whether this FH firmware also exposes a plain RTSP stream...")
    url = args.url or probe_rtsp(args.drone_ip)
    if url:
        return try_ffplay(url)

    print()
    print("No plain RTSP stream found. This model likely requires the FH native SDK path:")
    print("  FHDEV_NET_Init -> FHDEV_NET_SetCryptKey -> FHDEV_NET_Login -> startRealPlay")
    print("The Android app implements that in native lib FHExtraJni, not in portable Java/Python.")
    print("See the Android bridge source at:")
    print("  /private/tmp/tercaso-drone-streamers/fh_android_bridge/FhBrowserStreamer.java")
    return 1


def mode_auto(args):
    ip = args.drone_ip
    if ip.startswith("192.168.28."):
        args.video_port = 7070
        return mode_xr872(args)
    if ip.startswith("192.168.0."):
        args.listen_port = 7070
        return mode_jllw(args)
    if ip.startswith("192.168.218.") or ip.startswith("192.168.208.") or ip.startswith("192.168.201."):
        return mode_rtsp_family(args)
    if ip.startswith("172.19."):
        return mode_fh(args)
    print("Unknown family. Trying RTSP probe.")
    return mode_rtsp_family(args)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--drone-ip", required=True)
        p.add_argument("--http-host", default="127.0.0.1")
        p.add_argument("--http-port", type=int, default=8090)
        p.add_argument("--url")

    p = sub.add_parser("auto")
    common(p)
    p.add_argument("--port", type=int, default=8866)
    p.set_defaults(func=mode_auto)

    p = sub.add_parser("fh")
    common(p)
    p.add_argument("--port", type=int, default=8866)
    p.set_defaults(func=mode_fh)

    p = sub.add_parser("mr100")
    common(p)
    p.set_defaults(func=mode_rtsp_family)

    p = sub.add_parser("rtsp")
    common(p)
    p.set_defaults(func=mode_rtsp_family)

    p = sub.add_parser("xr872")
    common(p)
    p.add_argument("--video-port", type=int, default=7070)
    p.set_defaults(func=mode_xr872)

    p = sub.add_parser("jllw")
    common(p)
    p.add_argument("--listen-port", type=int, default=7070)
    p.set_defaults(func=mode_jllw)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
