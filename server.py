#!/usr/bin/env python3
"""同桌：零依赖的局域网文本与文件共享服务。"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
SHARED = ROOT / "shared_files"
TEXT_FILE = ROOT / "shared_text.txt"
MAX_UPLOAD = 512 * 1024 * 1024
STATE_LOCK = threading.Lock()
STATE_VERSION = 0


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name).strip(" .")
    return name[:180] or "未命名文件"


def unique_path(name: str) -> Path:
    candidate = SHARED / safe_filename(name)
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while True:
        candidate = SHARED / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def local_ips(port: int) -> tuple[list[str], str | None]:
    ips = set()
    primary_ip = None
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except socket.gaierror:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        primary_ip = sock.getsockname()[0]
        ips.add(primary_ip)
        sock.close()
    except OSError:
        pass
    urls = [f"http://{ip}:{port}" for ip in sorted(ips)]
    primary_url = f"http://{primary_ip}:{port}" if primary_ip else (urls[0] if urls else None)
    return urls, primary_url


def terminal_qr(text: str) -> str | None:
    try:
        result = subprocess.run(
            ["qrencode", "-t", "ANSIUTF8", "-m", "1", text],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "TongZhuo/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")

    def json_response(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, filename: str, content_type: str) -> None:
        path = WEB / filename
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            return self.serve_static("index.html", "text/html; charset=utf-8")
        if parsed.path == "/app.css":
            return self.serve_static("app.css", "text/css; charset=utf-8")
        if parsed.path == "/app.js":
            return self.serve_static("app.js", "text/javascript; charset=utf-8")
        if parsed.path == "/api/state":
            return self.get_state()
        if parsed.path == "/api/qr":
            return self.get_qr(parsed.query)
        if parsed.path.startswith("/files/"):
            return self.download_file(urllib.parse.unquote(parsed.path[7:]))
        self.send_error(404, "没有这个页面")

    def get_qr(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        text = params.get("text", [""])[0]
        if not text or len(text) > 2048:
            return self.send_error(400, "二维码内容不正确")
        try:
            result = subprocess.run(
                ["qrencode", "-t", "PNG", "-s", "7", "-m", "2", "-o", "-", text],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return self.send_error(503, "系统缺少 qrencode")
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(result.stdout)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(result.stdout)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/text":
            return self.save_text()
        if path == "/api/upload":
            return self.upload_files()
        self.send_error(404, "没有这个接口")

    def get_state(self) -> None:
        files = []
        for path in sorted(SHARED.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.is_file() and not path.name.startswith("."):
                stat = path.stat()
                files.append({
                    "name": path.name,
                    "size": stat.st_size,
                    "modified": int(stat.st_mtime),
                    "url": "/files/" + urllib.parse.quote(path.name),
                })
        with STATE_LOCK:
            version = STATE_VERSION
            text = TEXT_FILE.read_text("utf-8") if TEXT_FILE.exists() else ""
        self.json_response({"text": text, "files": files, "version": version})

    def save_text(self) -> None:
        global STATE_VERSION
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2 * 1024 * 1024:
            return self.json_response({"error": "文本不能超过 2 MB"}, 413)
        try:
            data = json.loads(self.rfile.read(length))
            text = data["text"]
            if not isinstance(text, str):
                raise ValueError
        except (json.JSONDecodeError, KeyError, ValueError):
            return self.json_response({"error": "文本格式不正确"}, 400)
        with STATE_LOCK:
            TEXT_FILE.write_text(text, "utf-8")
            STATE_VERSION += 1
            version = STATE_VERSION
        self.json_response({"ok": True, "version": version})

    def upload_files(self) -> None:
        global STATE_VERSION
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD:
            return self.json_response({"error": "上传内容为空或超过 512 MB"}, 413)
        content_type = self.headers.get("Content-Type", "")
        match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
        if not content_type.startswith("multipart/form-data") or not match:
            return self.json_response({"error": "上传格式不正确"}, 400)
        boundary = (match.group(1) or match.group(2)).encode()
        remaining = length
        body = bytearray()
        while remaining:
            chunk = self.rfile.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            body.extend(chunk)
            remaining -= len(chunk)
        saved = []
        delimiter = b"--" + boundary
        for part in bytes(body).split(delimiter):
            if b"\r\n\r\n" not in part:
                continue
            headers, content = part.split(b"\r\n\r\n", 1)
            filename_match = re.search(
                br'filename\*?=(?:UTF-8\'\'|")?([^";\r\n]+)', headers, re.I
            )
            if not filename_match:
                continue
            raw_name = filename_match.group(1).rstrip(b'"')
            name = urllib.parse.unquote(raw_name.decode("utf-8", "replace"))
            content = content.removesuffix(b"\r\n")
            target = unique_path(name)
            target.write_bytes(content)
            saved.append(target.name)
        if not saved:
            return self.json_response({"error": "没有收到文件"}, 400)
        with STATE_LOCK:
            STATE_VERSION += 1
        self.json_response({"ok": True, "files": saved})

    def download_file(self, name: str) -> None:
        target = SHARED / safe_filename(name)
        if not target.is_file() or target.parent != SHARED:
            return self.send_error(404, "文件不存在")
        size = target.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        encoded = urllib.parse.quote(target.name)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded}")
        self.end_headers()
        with target.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                self.wfile.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser(description="局域网共享文本和文件")
    parser.add_argument("--port", type=int, default=8787, help="监听端口（默认 8787）")
    args = parser.parse_args()
    SHARED.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("\n同桌已启动。请让其他设备连接同一个 Wi-Fi，然后打开：")
    urls, primary_url = local_ips(args.port)
    print("\n".join(f"  {url}" for url in urls) or f"  http://本机IP:{args.port}")
    if primary_url:
        qr = terminal_qr(primary_url)
        if qr:
            print(f"\n手机扫码打开（{primary_url}）：\n")
            print(qr)
        else:
            print("\n未找到 qrencode，暂时无法在终端显示二维码。")
    print(f"\n文件保存位置：{SHARED}\n按 Ctrl+C 停止。\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
