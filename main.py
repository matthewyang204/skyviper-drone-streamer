#!/usr/bin/env python3
"""
Helper for SkyViper SE / JieLi-based video stream.

The app uses a CTP control channel on TCP 3333 and RTSP video on port 554.
This script sends the same OPEN_RT_STREAM command, then launches ffplay.
"""

import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time


CTP_PORT = 3333
DEFAULT_IP = "192.168.80.1"
XR872_NET_PREFIX = "192.168.28."

URLS = {
    ("front", "h264", "sd"): "rtsp://{ip}:554/264_pcm_rt/XXX.sd",
    ("front", "h264", "hd"): "rtsp://{ip}:554/264_pcm_rt/XXX.hd",
    ("front", "h264", "fhd"): "rtsp://{ip}:554/264_pcm_rt/XXX.fhd",
    ("rear", "h264", "sd"): "rtsp://{ip}:554/264_pcm_rt/rear.sd",
    ("rear", "h264", "hd"): "rtsp://{ip}:554/264_pcm_rt/rear.hd",
    ("rear", "h264", "fhd"): "rtsp://{ip}:554/264_pcm_rt/rear.fhd",
    ("front", "jpeg", "sd"): "rtsp://{ip}:554/avi_pcm_rt/front.sd",
    ("front", "jpeg", "hd"): "rtsp://{ip}:554/avi_pcm_rt/front.hd",
    ("front", "jpeg", "fhd"): "rtsp://{ip}:554/avi_pcm_rt/front.fhd",
    ("rear", "jpeg", "sd"): "rtsp://{ip}:554/avi_pcm_rt/rear.sd",
    ("rear", "jpeg", "hd"): "rtsp://{ip}:554/avi_pcm_rt/rear.hd",
    ("rear", "jpeg", "fhd"): "rtsp://{ip}:554/avi_pcm_rt/rear.fhd",
}

RESOLUTION = {
    "sd": (640, 480),
    "hd": (1280, 720),
    "fhd": (1920, 1080),
}

def checkIfListInStr(string, list):
    for item in list:
        if item in string:
            return True
    return False

def ctp_packet(topic, op=None, params=None):
    topic_b = topic.encode("ascii")
    if op is None:
        payload_b = b""
    else:
        payload = {"op": op}
        if params is not None:
            payload["param"] = params
        payload_b = json.dumps(payload, separators=(",", ":")).encode("ascii")
    return b"CTP:" + struct.pack("<H", len(topic_b)) + topic_b + struct.pack("<I", len(payload_b)) + payload_b


def send_ctp(ip, topic, params=None, timeout=2.0):
    packet = ctp_packet(topic, "PUT", params)
    with socket.create_connection((ip, CTP_PORT), timeout=timeout) as sock:
        sock.sendall(packet)
        sock.settimeout(timeout)
        try:
            return sock.recv(4096)
        except socket.timeout:
            return b""


def open_stream(ip, camera, fmt, res, fps):
    width, height = RESOLUTION[res]
    topic = "OPEN_PULL_RT_STREAM" if camera == "rear" else "OPEN_RT_STREAM"
    params = {
        "format": "1" if fmt == "h264" else "0",
        "w": str(width),
        "h": str(height),
        "fps": str(fps),
    }
    print(f"Sending {topic} to {ip}:{CTP_PORT} with {params}")
    try:
        reply = send_ctp(ip, topic, params)
        if reply:
            print("CTP reply:", reply[:200])
        else:
            print("No CTP reply before timeout; continuing to RTSP.")
    except OSError as exc:
        print(f"CTP control connection failed: {exc}")
        print("Continuing anyway; some firmware exposes RTSP without the open command.")


def close_stream(ip, camera):
    topic = "CLOSE_PULL_RT_STREAM" if camera == "rear" else "CLOSE_RT_STREAM"
    try:
        send_ctp(ip, topic, {"status": "1"}, timeout=1.0)
    except OSError:
        pass


def have(cmd):
    return shutil.which(cmd) is not None


def ffplay(url):
    if not have("ffplay"):
        print("ffplay not found. Install it to be able to open the stream.")
        print("Then try:", url)
        return 2
    cmd = [
        "ffplay",
        "-rtsp_transport",
        "tcp",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-framedrop",
        url,
    ]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


def xr872_drone_ip(ip):
    if ip.startswith(XR872_NET_PREFIX) and not ip.endswith(".1"):
        return XR872_NET_PREFIX + "1"
    return ip


def run_xr872_browser_stream(ip, http_host, http_port):
    drone_ip = xr872_drone_ip(ip)
    if drone_ip != ip:
        print(f"{ip} looks like the Mac/client address; using XR872 drone address {drone_ip}.")
    print("192.168.28.x uses the XR872 UDP-JPEG stream, not SkyViper/Jieli RTSP.")
    print(f"Open http://{http_host}:{http_port}/ in your browser after frames start.")
    helper = os.path.join(os.path.dirname(sys.executable), "drone_streamer.py")
    helperEXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drone_streamer")
    cmd = companion_app_cmd(helper, helperEXE, "xr872", drone_ip, http_host, http_port)
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)

def companion_app_cmd(helper, helperEXE, drone_model, drone_ip, http_host, http_port):
    http_port = str(http_port)
    if checkIfListInStr(sys.executable, ["python", "Python"]):
        cmd = [
            sys.executable,
            helper,
            drone_model,
            "--drone-ip",
            drone_ip,
            "--http-host",
            http_host,
            "--http-port",
            http_port,
            ]
    else:
        cmd = [
            helperEXE,
            drone_model,
            "--drone-ip",
            drone_ip,
            "--http-host",
            http_host,
            "--http-port",
            http_port,
            ]
    return cmd

def print_version():
    print("SkyViper Drone FPV Streamer, version 0.1.0")
    print("(C) 2026 Matthew Yang (杨佳明)")
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", "-v", action="store_true", help="Show version and exit")
    parser.add_argument("--ip", default=DEFAULT_IP)
    parser.add_argument("--camera", choices=("front", "rear"), default="front")
    parser.add_argument("--format", choices=("h264", "jpeg"), default="h264")
    parser.add_argument("--res", choices=("sd", "hd", "fhd"), default="sd")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--no-open-command", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--http-host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8090)
    args = parser.parse_args()

    if args.version:
        print_version()
    if args.ip.startswith(XR872_NET_PREFIX):
        return run_xr872_browser_stream(args.ip, args.http_host, args.http_port)

    url = args.url or URLS[(args.camera, args.format, args.res)].format(ip=args.ip)
    if not args.no_open_command:
        open_stream(args.ip, args.camera, args.format, args.res, args.fps)
        time.sleep(0.4)

    try:
        return ffplay(url)
    finally:
        if not args.no_open_command:
            close_stream(args.ip, args.camera)


if __name__ == "__main__":
    sys.exit(main())
