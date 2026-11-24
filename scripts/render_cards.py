#!/usr/bin/env python3
"""Render warm-paper editorial product showcase cards to crisp @2x PNGs.

Uses Python 3.10+ standard library only and drives local headless Chrome,
Chromium, or Microsoft Edge directly via Chrome DevTools Protocol (CDP)
over an RFC 6455 WebSocket connection. Zero third-party pip or npm packages.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if sys.version_info < (3, 10):
    sys.stderr.write("oil-showcase requires Python 3.10 or higher.\n")
    sys.exit(1)

SKILL_ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = SKILL_ROOT / "assets" / "fonts"

REQUIRED_FONT_FILES = (
    "inter-400.woff2",
    "inter-500.woff2",
    "inter-600.woff2",
    "jetbrains-mono-400.woff2",
    "jetbrains-mono-500.woff2",
    "playfair-display-400.woff2",
    "playfair-display-500.woff2",
)

FONT_FACE_SPECS = (
    ("Inter", 400, "inter-400.woff2"),
    ("Inter", 500, "inter-500.woff2"),
    ("Inter", 600, "inter-600.woff2"),
    ("JetBrains Mono", 400, "jetbrains-mono-400.woff2"),
    ("JetBrains Mono", 500, "jetbrains-mono-500.woff2"),
    ("Playfair Display", 400, "playfair-display-400.woff2"),
    ("Playfair Display", 500, "playfair-display-500.woff2"),
)

RATIO_PRESETS: dict[str, tuple[int, int]] = {
    "16:9": (1600, 900),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "9:16": (1080, 1920),
    "1:1": (1200, 1200),
}

SUPPORTED_LAYOUTS = (
    "layered-hero",
    "layered-bento",
    "section-assembly",
    "module-bento",
    "gallery-wall",
    "window-callout",
    "before-after",
    "workflow-steps",
)

SUPPORTED_THEMES = (
    "warm-paper",
    "dark-studio",
    "cool-gallery",
)

SUPPORTED_DISPLAY_STYLES = ("serif", "sans")
SUPPORTED_WC_ARRANGEMENTS = ("split", "stacked")

THEME_DEFAULT_DISPLAY: dict[str, str] = {
    "warm-paper": "serif",
    "dark-studio": "sans",
    "cool-gallery": "sans",
}

THEME_DEFAULT_ACCENT: dict[str, str] = {
    "warm-paper": "#1b1c19",
    "dark-studio": "#f3f4f6",
    "cool-gallery": "#14171d",
}

CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
BG_COLOR_RE = re.compile(
    r"^(?:transparent|#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|"
    r"rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\))$",
    re.IGNORECASE,
)
CUSTOM_SIZE_RE = re.compile(r"^(\d{3,4})[xX×](\d{3,4})$")
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
VALID_FITS = ("cover", "contain")


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def contrast_ink_for_hex(hex_color: str) -> str:
    """Return crisp dark or light ink with strong contrast against hex_color."""
    raw = hex_color.lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#101114" if luminance > 0.60 else "#ffffff"


def parse_ratio(value: str) -> tuple[int, int]:
    raw = (value or "16:9").strip()
    if raw in RATIO_PRESETS:
        return RATIO_PRESETS[raw]
    match = CUSTOM_SIZE_RE.fullmatch(raw)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        if 400 <= width <= 4000 and 400 <= height <= 4000:
            return width, height
    raise ValueError(
        f"无效的画幅比例 '{value}'；请使用 16:9、4:3、3:4、9:16、1:1，或 400-4000 范围内的 WxH（例如 1600x900）。"
    )


def parse_viewport(value: str) -> tuple[int, int]:
    match = CUSTOM_SIZE_RE.fullmatch((value or "").strip())
    if not match:
        raise ValueError(
            f"无效的视口尺寸 '{value}'；请使用 WxH 格式，例如 1440x900。"
        )
    width, height = int(match.group(1)), int(match.group(2))
    if not (320 <= width <= 4096 and 320 <= height <= 4096):
        raise ValueError(f"视口尺寸超出范围：{width}x{height}（允许 320-4096）。")
    return width, height


def find_chrome() -> str | None:
    env_path = os.environ.get("CHROME_PATH", "").strip()
    if env_path and Path(env_path).is_file():
        return env_path

    system = platform.system().lower()
    candidates: list[Path] = []
    if system == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ]
        )
    elif system == "windows":
        roots = [
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        suffixes = [
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Chromium/Application/chrome.exe"),
            Path("Microsoft/Edge/Application/msedge.exe"),
            Path("BraveSoftware/Brave-Browser/Application/brave.exe"),
        ]
        for root in roots:
            if root:
                for suffix in suffixes:
                    candidates.append(Path(root) / suffix)

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "microsoft-edge-stable",
        "msedge",
        "brave-browser",
        "chrome",
    ):
        found = shutil.which(name)
        if found:
            return found

    if system == "linux":
        for candidate in (
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/snap/bin/chromium"),
            Path("/usr/bin/microsoft-edge"),
            Path("/usr/bin/microsoft-edge-stable"),
            Path("/usr/bin/brave-browser"),
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def get_binary_version(binary_path: str) -> str | None:
    try:
        proc = subprocess.run(
            [binary_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (proc.stdout or proc.stderr or "").strip()
    return output.splitlines()[0] if output else None


def read_png_dimensions(png_path: Path) -> tuple[int, int]:
    header = png_path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是有效的 PNG 文件：{png_path}")
    return struct.unpack(">II", header[16:24])


# ---------------------------------------------------------------------------
# Minimal RFC 6455 WebSocket + Chrome DevTools Protocol (CDP) client
# ---------------------------------------------------------------------------


class SimpleWebSocket:
    """Pure-stdlib RFC 6455 client for local Chrome DevTools Protocol."""

    def __init__(self, host: str, port: int, path: str, timeout: float = 30.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        self._handshake(host, port, path)

    def _handshake(self, host: str, port: int, path: str) -> None:
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败：连接提前关闭。")
            self._buf.extend(chunk)
        header_end = self._buf.index(b"\r\n\r\n") + 4
        raw_headers = bytes(self._buf[:header_end]).decode("iso-8859-1")
        del self._buf[:header_end]
        status_line = raw_headers.splitlines()[0] if raw_headers else ""
        if "101" not in status_line:
            raise RuntimeError(f"WebSocket 握手被拒绝：{status_line}")
        expected_accept = base64.b64encode(
            hashlib.sha1(
                (ws_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        if expected_accept not in raw_headers:
            raise RuntimeError("WebSocket 握手校验失败（Sec-WebSocket-Accept 不匹配）。")

    def _recv_exact(self, size: int) -> bytes:
        while len(self._buf) < size:
            chunk = self._sock.recv(max(4096, size - len(self._buf)))
            if not chunk:
                raise EOFError("WebSocket 连接已关闭。")
            self._buf.extend(chunk)
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray()
        header.append(0x80 | (opcode & 0x0F))
        length = len(payload)
        mask_bit = 0x80
        if length < 126:
            header.append(mask_bit | length)
        elif length < 65536:
            header.append(mask_bit | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(mask_bit | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self, timeout: float | None = None) -> str:
        previous_timeout = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            fragments: list[bytes] = []
            while True:
                b1, b2 = self._recv_exact(2)
                fin = bool(b1 & 0x80)
                opcode = b1 & 0x0F
                masked = bool(b2 & 0x80)
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack("!H", self._recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._recv_exact(8))[0]
                mask = self._recv_exact(4) if masked else b""
                payload = self._recv_exact(length)
                if masked:
                    payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
                if opcode == 0x8:
                    raise EOFError("Chrome DevTools WebSocket 已关闭。")
                if opcode == 0x9:
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x0, 0x1, 0x2):
                    fragments.append(payload)
                    if fin:
                        return b"".join(fragments).decode("utf-8")
        finally:
            if timeout is not None:
                self._sock.settimeout(previous_timeout)

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class ChromeSession:
    """Managed isolated headless Chrome instance speaking CDP."""

    def __init__(self, chrome_path: str, timeout: float = 35.0) -> None:
        self.chrome_path = chrome_path
        self.timeout = timeout
        self._profile_dir: Path | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._ws: SimpleWebSocket | None = None
        self._next_id = 1
        self._events: list[dict[str, Any]] = []

    def __enter__(self) -> "ChromeSession":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def start(self) -> None:
        self._profile_dir = Path(tempfile.mkdtemp(prefix="oil-showcase-"))
        cmd = [
            self.chrome_path,
            "--headless=new",
            "--enable-unsafe-swiftshader",
            "--remote-debugging-port=0",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--use-mock-keychain",
            "--password-store=basic",
            "--hide-scrollbars",
            "--mute-audio",
            "--disable-extensions",
            "--allow-file-access-from-files",
            "--disable-background-networking",
            "--disable-sync",
            "about:blank",
        ]
        if platform.system().lower() == "linux":
            cmd.insert(-1, "--disable-dev-shm-usage")
            in_ci = os.environ.get("CI", "").strip().lower() not in ("", "0", "false")
            no_sandbox_env = os.environ.get("CHROME_NO_SANDBOX", "").strip().lower() not in (
                "",
                "0",
                "false",
            )
            is_root = hasattr(os, "geteuid") and os.geteuid() == 0
            if in_ci or no_sandbox_env or is_root:
                cmd.insert(-1, "--no-sandbox")
                cmd.insert(-1, "--disable-setuid-sandbox")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        port_file = self._profile_dir / "DevToolsActivePort"
        deadline = time.monotonic() + self.timeout
        port: int | None = None
        ws_path: str | None = None
        while time.monotonic() < deadline:
            if port_file.is_file():
                lines = [
                    line.strip()
                    for line in port_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if line.strip()
                ]
                if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith("/devtools/"):
                    port = int(lines[0])
                    ws_path = lines[1]
                    break
            if self._proc.poll() is not None and time.monotonic() + 25.0 < deadline:
                # Some Chrome builds hand off to another process; wait briefly for port file.
                pass
            time.sleep(0.05)

        if port is None or ws_path is None:
            self.close()
            raise RuntimeError(
                "启动无头浏览器超时，未能获取 DevTools 端口。请确认本机浏览器可正常启动，或通过 CHROME_PATH 指定路径。"
            )
        self._ws = SimpleWebSocket("127.0.0.1", port, ws_path, timeout=self.timeout)

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("浏览器会话未启动。")
        msg_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id
        self._ws.send_text(json.dumps(payload))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            remaining = max(0.5, deadline - time.monotonic())
            raw = self._ws.recv_text(timeout=remaining)
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    err = data["error"]
                    raise RuntimeError(
                        f"CDP 调用 {method} 失败：{err.get('message', err)}"
                    )
                return data.get("result", {})
            if "method" in data:
                self._events.append(data)
        raise TimeoutError(f"CDP 调用 {method} 超时。")

    def wait_for_event(
        self,
        method: str,
        session_id: str | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("浏览器会话未启动。")
        for idx, event in enumerate(self._events):
            if event.get("method") == method and (
                session_id is None or event.get("sessionId") == session_id
            ):
                return self._events.pop(idx)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.2, deadline - time.monotonic())
            try:
                raw = self._ws.recv_text(timeout=remaining)
            except TimeoutError:
                break
            except socket.timeout:
                break
            data = json.loads(raw)
            if data.get("method") == method and (
                session_id is None or data.get("sessionId") == session_id
            ):
                return data
            if "method" in data:
                self._events.append(data)
        raise TimeoutError(f"等待事件 {method} 超时。")

    def capture_page(
        self,
        url: str,
        out_path: Path,
        width: int,
        height: int,
        scale: float = 2.0,
        selector: str | None = None,
        full_page: bool = False,
        wait_ms: int = 120,
        check_card_dom: bool = True,
    ) -> dict[str, Any]:
        created = self.send("Target.createTarget", {"url": "about:blank"})
        target_id = created["targetId"]
        attached = self.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = attached["sessionId"]
        try:
            self._events = [
                ev for ev in self._events if ev.get("sessionId") != session_id
            ]
            self.send("Page.enable", session_id=session_id)
            self.send("Runtime.enable", session_id=session_id)
            self.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": scale,
                    "mobile": False,
                },
                session_id=session_id,
            )
            self.send("Page.navigate", {"url": url}, session_id=session_id)
            try:
                self.wait_for_event("Page.loadEventFired", session_id=session_id, timeout=20.0)
            except TimeoutError:
                pass

            eval_js = f"""
            (async () => {{
              if (document.fonts && document.fonts.ready) {{
                await document.fonts.ready;
              }}
              const imgs = Array.from(document.querySelectorAll('img'));
              await Promise.all(imgs.map((img) => {{
                if (img.complete && img.naturalWidth > 0) {{
                  return img.decode ? img.decode().catch(() => {{}}) : Promise.resolve();
                }}
                return new Promise((resolve) => {{
                  const done = () => resolve();
                  img.addEventListener('load', done, {{ once: true }});
                  img.addEventListener('error', done, {{ once: true }});
                  setTimeout(done, 8000);
                }});
              }}));
              if (typeof window.__positionShowcaseConnectors === 'function') {{
                window.__positionShowcaseConnectors();
              }}
              await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
              if ({int(wait_ms)} > 0) {{
                await new Promise((r) => setTimeout(r, {int(wait_ms)}));
              }}
              if (typeof window.__positionShowcaseConnectors === 'function') {{
                window.__positionShowcaseConnectors();
                await new Promise((r) => requestAnimationFrame(r));
              }}

              const brokenImages = imgs
                .filter((img) => !img.complete || img.naturalWidth === 0)
                .map((img) => img.getAttribute('src') || '(unknown)');

              const warnings = [];
              if ({'true' if check_card_dom else 'false'}) {{
                const checkSelectors = [
                  '.sc-title',
                  '.sc-subtitle',
                  '.sc-legend',
                  '.sc-footer',
                  '.sc-step-text',
                  '.sc-section-caption',
                  '.wc-callout-title',
                  '.wc-callout-caption',
                  '.ba-caption',
                  '.ws-step-title',
                  '.ws-step-caption',
                ];
                for (const sel of checkSelectors) {{
                  for (const el of document.querySelectorAll(sel)) {{
                    const cs = getComputedStyle(el);
                    if (cs.overflowY === 'hidden' && el.scrollHeight - el.clientHeight > 3) {{
                      warnings.push(`文本纵向溢出 (${{sel}}): "${{el.textContent.trim().slice(0, 32)}}"`);
                    }}
                    if (cs.overflowX === 'hidden' && el.scrollWidth - el.clientWidth > 3) {{
                      warnings.push(`文本横向溢出 (${{sel}}): "${{el.textContent.trim().slice(0, 32)}}"`);
                    }}
                  }}
                }}
                const root = document.querySelector('.sc-canvas');
                if (root) {{
                  const rect = root.getBoundingClientRect();
                  for (const el of root.querySelectorAll('.sc-brand, .sc-title, .sc-subtitle, .sc-legend, .sc-footer')) {{
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0 && (r.right > rect.right + 2 || r.bottom > rect.bottom + 2)) {{
                      warnings.push(`文字超出画布边界 (${{el.className}}): "${{el.textContent.trim().slice(0, 32)}}"`);
                    }}
                  }}
                }}
              }}

              let clip = null;
              let selectorError = null;
              const selector = {json.dumps(selector)};
              if (selector) {{
                const target = document.querySelector(selector);
                if (!target) {{
                  selectorError = `未在页面中找到匹配选择器 "${{selector}}" 的元素。`;
                }} else {{
                  const r = target.getBoundingClientRect();
                  if (r.width < 1 || r.height < 1) {{
                    selectorError = `选择器 "${{selector}}" 匹配元素尺寸为空 (${{r.width}}x${{r.height}})。`;
                  }} else {{
                    clip = {{
                      x: Math.max(0, r.left + window.scrollX),
                      y: Math.max(0, r.top + window.scrollY),
                      width: r.width,
                      height: r.height,
                      scale: 1,
                    }};
                  }}
                }}
              }} else if ({'true' if full_page else 'false'}) {{
                const fullW = Math.max(document.documentElement.scrollWidth, {width});
                const fullH = Math.min(12000, Math.max(document.documentElement.scrollHeight, {height}));
                clip = {{ x: 0, y: 0, width: fullW, height: fullH, scale: 1 }};
              }}

              return {{
                imageCount: imgs.length,
                brokenImages,
                warnings,
                clip,
                selectorError,
                fontCount: document.fonts ? document.fonts.size : 0,
              }};
            }})()
            """
            eval_res = self.send(
                "Runtime.evaluate",
                {
                    "expression": eval_js,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
                session_id=session_id,
            )
            if "exceptionDetails" in eval_res:
                raise RuntimeError(
                    f"页面自检脚本执行异常：{eval_res['exceptionDetails']}"
                )
            info = eval_res.get("result", {}).get("value") or {}
            if info.get("selectorError"):
                raise RuntimeError(info["selectorError"])
            if info.get("brokenImages"):
                raise RuntimeError(
                    "检测到未加载成功的图片：" + ", ".join(info["brokenImages"])
                )

            shot_params: dict[str, Any] = {
                "format": "png",
                "fromSurface": True,
            }
            if info.get("clip"):
                shot_params["clip"] = info["clip"]
                shot_params["captureBeyondViewport"] = True

            shot = self.send("Page.captureScreenshot", shot_params, session_id=session_id)
            png_bytes = base64.b64decode(shot["data"])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_suffix(".png.tmp")
            tmp_path.write_bytes(png_bytes)
            tmp_path.replace(out_path)
            px_w, px_h = read_png_dimensions(out_path)
            return {
                "width": px_w,
                "height": px_h,
                "imageCount": info.get("imageCount", 0),
                "warnings": info.get("warnings", []),
            }
        finally:
            try:
                self.send("Target.closeTarget", {"targetId": target_id})
            except Exception:
                pass

    def close(self) -> None:
        if self._ws is not None:
            try:
                self.send("Browser.close")
            except Exception:
                pass
            self._ws.close()
            self._ws = None
        if self._proc is not None:
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None
        if self._profile_dir is not None:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None


# ---------------------------------------------------------------------------
# Fonts & CSS helpers
# ---------------------------------------------------------------------------


def build_inlined_fonts_css() -> str:
    """Embed bundled OFL woff2 fonts as data URIs for offline CORS-free HTML."""
    blocks: list[str] = ["/* Bundled OFL fonts (inlined for offline rendering) */"]
    for family, weight, filename in FONT_FACE_SPECS:
        font_path = FONTS_DIR / filename
        if not font_path.is_file():
            raise FileNotFoundError(f"缺少内置字体文件：{font_path}")
        b64 = base64.b64encode(font_path.read_bytes()).decode("ascii")
        blocks.append(
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            f"  font-display: block;\n"
            f"  src: url('data:font/woff2;base64,{b64}') format('woff2');\n"
            f"}}"
        )
    return "\n\n".join(blocks) + "\n"


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def esc_multiline(text: str) -> str:
    return "<br>".join(esc(line) for line in str(text).split("\n"))


def resolve_asset_path(raw_path: str, base_dir: Path, field_label: str, errors: list[str]) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append(f"{field_label} 必须是非空图片路径。")
        return ""
    cleaned = raw_path.strip()
    if cleaned.startswith(("http://", "https://", "data:")):
        return cleaned
    candidate = Path(cleaned)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.is_file():
        errors.append(f"{field_label} 指向的文件不存在：{raw_path}（解析为 {candidate}）")
        return ""
    return candidate.as_uri()


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------


def normalize_image_options(
    raw: dict[str, Any],
    field_label: str,
    errors: list[str],
    *,
    default_fit: str = "cover",
    default_position: str = "top center",
    default_bg: str = "",
) -> tuple[str, str, str]:
    fit = str(raw.get("fit") or default_fit).strip().lower()
    if fit not in VALID_FITS:
        errors.append(
            f"{field_label}.fit 无效（{fit!r}）；可选值：{', '.join(VALID_FITS)}。"
        )
        fit = default_fit
    position = str(raw.get("position") or default_position).strip()
    if not position:
        position = default_position
    bg = str(raw.get("bg") if raw.get("bg") is not None else default_bg).strip()
    if bg and not BG_COLOR_RE.fullmatch(bg):
        errors.append(
            f"{field_label}.bg 无效（{bg!r}）；请使用 #RGB/#RRGGBB/#RRGGBBAA、rgb()/rgba() 或 transparent。"
        )
        bg = default_bg
    return fit, position, bg


def validate_spec(
    spec_path: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not spec_path.is_file():
        return [], [f"规格文件不存在：{spec_path}"]
    try:
        raw_text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [f"无法读取规格文件：{exc}"]
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return [], [f"规格文件不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"]

    if not isinstance(data, dict):
        return [], ["规格文件顶层必须是 JSON 对象（包含 cards 数组）。"]

    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        return [], ["规格文件缺少非空的 cards 数组。"]

    base_dir = spec_path.resolve().parent
    default_brand = data.get("brand")
    if default_brand is not None and not isinstance(default_brand, dict):
        errors.append("顶层 brand 必须是对象或省略。")
        default_brand = None

    default_theme = str(data.get("theme") or "warm-paper").strip()
    if default_theme not in SUPPORTED_THEMES:
        errors.append(
            f"顶层 theme 无效（{default_theme!r}）；可选值：{', '.join(SUPPORTED_THEMES)}。"
        )
        default_theme = "warm-paper"

    default_display_raw = data.get("displayStyle")
    default_display: str | None = None
    if default_display_raw is not None:
        default_display = str(default_display_raw).strip()
        if default_display not in SUPPORTED_DISPLAY_STYLES:
            errors.append(
                f"顶层 displayStyle 无效（{default_display!r}）；可选值：{', '.join(SUPPORTED_DISPLAY_STYLES)}。"
            )
            default_display = None

    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_cards):
        prefix = f"cards[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{prefix} 必须是对象。")
            continue

        card_id = raw.get("id")
        if not isinstance(card_id, str) or not CARD_ID_RE.fullmatch(card_id):
            errors.append(
                f"{prefix}.id 无效（{card_id!r}）；请使用字母、数字、连字符或下划线。"
            )
            continue
        if card_id in seen_ids:
            errors.append(f"{prefix}.id 重复：'{card_id}'。")
            continue
        seen_ids.add(card_id)
        label = f"card '{card_id}'"

        layout = raw.get("layout")
        if layout not in SUPPORTED_LAYOUTS:
            errors.append(
                f"{label}.layout 无效（{layout!r}）；可选值：{', '.join(SUPPORTED_LAYOUTS)}。"
            )
            continue

        ratio_str = str(raw.get("ratio", "16:9")).strip()
        try:
            width, height = parse_ratio(ratio_str)
        except ValueError as exc:
            errors.append(f"{label}.ratio：{exc}")
            continue

        theme = str(raw.get("theme") or default_theme).strip()
        if theme not in SUPPORTED_THEMES:
            errors.append(
                f"{label}.theme 无效（{theme!r}）；可选值：{', '.join(SUPPORTED_THEMES)}。"
            )
            continue

        if raw.get("displayStyle") is not None:
            display_style = str(raw.get("displayStyle")).strip()
            if display_style not in SUPPORTED_DISPLAY_STYLES:
                errors.append(
                    f"{label}.displayStyle 无效（{display_style!r}）；可选值：{', '.join(SUPPORTED_DISPLAY_STYLES)}。"
                )
                continue
        elif default_display is not None:
            display_style = default_display
        else:
            display_style = THEME_DEFAULT_DISPLAY[theme]

        accent = str(raw.get("accent") or THEME_DEFAULT_ACCENT[theme]).strip()
        if not HEX_COLOR_RE.fullmatch(accent):
            errors.append(
                f"{label}.accent 无效（{accent!r}）；请使用 #RGB 或 #RRGGBB 十六进制颜色。"
            )
            continue

        if "brand" in raw:
            brand_raw = raw.get("brand")
        else:
            brand_raw = default_brand
        brand_obj: dict[str, Any] | None = None
        if brand_raw is not None:
            if not isinstance(brand_raw, dict):
                errors.append(f"{label}.brand 必须是对象或 null。")
            else:
                icon_uri = ""
                if brand_raw.get("icon"):
                    icon_uri = resolve_asset_path(
                        brand_raw["icon"], base_dir, f"{label}.brand.icon", errors
                    )
                brand_obj = {
                    "name": str(brand_raw.get("name", "")).strip(),
                    "tag": str(brand_raw.get("tag", "")).strip(),
                    "icon": icon_uri,
                }
                if not brand_obj["name"] and not brand_obj["tag"] and not brand_obj["icon"]:
                    brand_obj = None

        title = str(raw.get("title", "")).strip()
        subtitle = str(raw.get("subtitle", "")).strip()
        eyebrow = str(raw.get("eyebrow", "")).strip()
        footer = str(raw.get("footer", "")).strip()
        variant = str(raw.get("variant", "tilted-pages")).strip()

        if not title and not (layout == "gallery-wall" and variant == "collage"):
            errors.append(f"{label}.title 不能为空（仅 gallery-wall collage 变体可省略标题）。")

        card: dict[str, Any] = {
            "id": card_id,
            "layout": layout,
            "theme": theme,
            "displayStyle": display_style,
            "ratio": ratio_str,
            "width": width,
            "height": height,
            "orientation": (
                "portrait"
                if height > width
                else ("square" if width == height else "landscape")
            ),
            "isTall": (height / width) > 1.6,
            "accent": accent,
            "accentInk": contrast_ink_for_hex(accent),
            "brand": brand_obj,
            "title": title,
            "subtitle": subtitle,
            "eyebrow": eyebrow,
            "footer": footer,
        }

        if layout in ("layered-hero", "layered-bento"):
            raw_layers = raw.get("layers")
            if not isinstance(raw_layers, list) or not (2 <= len(raw_layers) <= 3):
                errors.append(f"{label}.layers 必须包含 2 到 3 个分层项。")
            else:
                layers: list[dict[str, Any]] = []
                for li, layer in enumerate(raw_layers):
                    if not isinstance(layer, dict):
                        errors.append(f"{label}.layers[{li}] 必须是对象。")
                        continue
                    uri = resolve_asset_path(
                        layer.get("image", ""),
                        base_dir,
                        f"{label}.layers[{li}].image",
                        errors,
                    )
                    is_transparent = bool(layer.get("transparent", False))
                    fit, position, bg = normalize_image_options(
                        layer,
                        f"{label}.layers[{li}]",
                        errors,
                        default_fit="cover",
                        default_position="center bottom" if is_transparent else "top center",
                    )
                    layers.append(
                        {
                            "index": f"{li + 1:02d}",
                            "label": str(layer.get("label", f"Layer {li + 1}")).strip(),
                            "image": uri,
                            "transparent": is_transparent,
                            "fit": fit,
                            "position": position,
                            "bg": bg,
                        }
                    )
                card["layers"] = layers
            card["showIndexBadges"] = bool(
                raw.get("showIndexBadges", layout == "layered-bento")
            )

        if layout == "layered-hero":
            raw_hero = raw.get("hero")
            if not isinstance(raw_hero, dict):
                errors.append(f"{label}.hero 必须是包含 image 的对象。")
            else:
                uri = resolve_asset_path(
                    raw_hero.get("image", ""), base_dir, f"{label}.hero.image", errors
                )
                fit, position, bg = normalize_image_options(
                    raw_hero,
                    f"{label}.hero",
                    errors,
                    default_fit="cover",
                    default_position="top center",
                )
                card["hero"] = {
                    "image": uri,
                    "tilt": bool(raw_hero.get("tilt", True)),
                    "fit": fit,
                    "position": position,
                    "bg": bg,
                }

        if layout in ("layered-bento", "module-bento", "gallery-wall"):
            raw_items = raw.get("items")
            if raw_items is None and layout == "gallery-wall":
                raw_items = raw.get("pages")
            min_items = 2
            max_items = 9 if layout == "gallery-wall" else 6
            if not isinstance(raw_items, list) or not (min_items <= len(raw_items) <= max_items):
                errors.append(
                    f"{label}.items 必须包含 {min_items} 到 {max_items} 个画面项。"
                )
            else:
                items: list[dict[str, Any]] = []
                for ii, item in enumerate(raw_items):
                    if not isinstance(item, dict):
                        errors.append(f"{label}.items[{ii}] 必须是对象。")
                        continue
                    uri = resolve_asset_path(
                        item.get("image", ""),
                        base_dir,
                        f"{label}.items[{ii}].image",
                        errors,
                    )
                    fit, position, bg = normalize_image_options(
                        item,
                        f"{label}.items[{ii}]",
                        errors,
                        default_fit="cover",
                        default_position="top center",
                    )
                    item_label = str(item.get("label") or item.get("caption") or "").strip()
                    item_index = str(item.get("index", "")).strip()
                    items.append(
                        {
                            "image": uri,
                            "index": item_index,
                            "label": item_label,
                            "fit": fit,
                            "position": position,
                            "bg": bg,
                        }
                    )
                card["items"] = items

        if layout == "module-bento":
            columns = raw.get("columns")
            if columns is not None and columns not in (2, 3):
                errors.append(f"{label}.columns 只支持 2 或 3。")
            card["columns"] = columns

        if layout == "gallery-wall":
            if variant not in ("tilted-pages", "collage"):
                errors.append(
                    f"{label}.variant 无效（{variant!r}）；可选值：tilted-pages、collage。"
                )
            card["variant"] = variant
            fg = raw.get("foreground")
            card["foreground"] = (
                resolve_asset_path(fg, base_dir, f"{label}.foreground", errors)
                if fg
                else ""
            )

        if layout == "section-assembly":
            raw_sections = raw.get("sections")
            if not isinstance(raw_sections, list) or not (2 <= len(raw_sections) <= 5):
                errors.append(f"{label}.sections 必须包含 2 到 5 个拆解段。")
            else:
                sections: list[dict[str, Any]] = []
                count = len(raw_sections)
                for si, sec in enumerate(raw_sections):
                    if not isinstance(sec, dict):
                        errors.append(f"{label}.sections[{si}] 必须是对象。")
                        continue
                    default_y = round(14 + (72 * si / max(1, count - 1)), 1)
                    target_y = sec.get("targetY", default_y)
                    if not isinstance(target_y, (int, float)) or not (0 <= target_y <= 100):
                        errors.append(f"{label}.sections[{si}].targetY 必须在 0 到 100 之间。")
                        target_y = default_y
                    sec_fit, sec_pos, sec_bg = normalize_image_options(
                        sec,
                        f"{label}.sections[{si}]",
                        errors,
                        default_fit="cover",
                        default_position="center",
                    )
                    plates: list[dict[str, str]] = []
                    if sec.get("images") is not None:
                        raw_imgs = sec["images"]
                        if not isinstance(raw_imgs, list) or not (1 <= len(raw_imgs) <= 2):
                            errors.append(f"{label}.sections[{si}].images 必须包含 1 到 2 张图片。")
                        else:
                            for sub_i, sub_img in enumerate(raw_imgs):
                                if isinstance(sub_img, dict):
                                    sub_uri = resolve_asset_path(
                                        sub_img.get("image", ""),
                                        base_dir,
                                        f"{label}.sections[{si}].images[{sub_i}].image",
                                        errors,
                                    )
                                    p_fit, p_pos, p_bg = normalize_image_options(
                                        sub_img,
                                        f"{label}.sections[{si}].images[{sub_i}]",
                                        errors,
                                        default_fit=sec_fit,
                                        default_position=sec_pos,
                                        default_bg=sec_bg,
                                    )
                                    plates.append(
                                        {"image": sub_uri, "fit": p_fit, "position": p_pos, "bg": p_bg}
                                    )
                                else:
                                    sub_uri = resolve_asset_path(
                                        sub_img,
                                        base_dir,
                                        f"{label}.sections[{si}].images[{sub_i}]",
                                        errors,
                                    )
                                    plates.append(
                                        {"image": sub_uri, "fit": sec_fit, "position": sec_pos, "bg": sec_bg}
                                    )
                    elif sec.get("image"):
                        sub_uri = resolve_asset_path(
                            sec["image"],
                            base_dir,
                            f"{label}.sections[{si}].image",
                            errors,
                        )
                        plates.append(
                            {"image": sub_uri, "fit": sec_fit, "position": sec_pos, "bg": sec_bg}
                        )
                    else:
                        errors.append(f"{label}.sections[{si}] 必须提供 image 或 images。")

                    raw_ratios = sec.get("ratios")
                    if isinstance(raw_ratios, list) and len(raw_ratios) == len(plates) and all(
                        isinstance(r, (int, float)) and r > 0 for r in raw_ratios
                    ):
                        ratios = [float(r) for r in raw_ratios]
                    else:
                        ratios = [1.0] * max(1, len(plates))

                    sections.append(
                        {
                            "index": f"{si + 1:02d}",
                            "caption": str(sec.get("caption", "")).strip(),
                            "summary": str(sec.get("summary", sec.get("caption", ""))).strip(),
                            "plates": plates,
                            "ratios": ratios,
                            "targetY": float(target_y),
                        }
                    )
                card["sections"] = sections

            raw_showcase = raw.get("showcase")
            if not isinstance(raw_showcase, dict):
                errors.append(f"{label}.showcase 必须是包含 image 的对象。")
            else:
                uri = resolve_asset_path(
                    raw_showcase.get("image", ""),
                    base_dir,
                    f"{label}.showcase.image",
                    errors,
                )
                fit, position, bg = normalize_image_options(
                    raw_showcase,
                    f"{label}.showcase",
                    errors,
                    default_fit="cover",
                    default_position="top center",
                )
                card["showcase"] = {
                    "image": uri,
                    "tilt": bool(raw_showcase.get("tilt", True)),
                    "fit": fit,
                    "position": position,
                    "bg": bg,
                }

        if layout == "window-callout":
            arrangement = str(raw.get("arrangement") or "split").strip()
            if arrangement not in SUPPORTED_WC_ARRANGEMENTS:
                errors.append(
                    f"{label}.arrangement 无效（{arrangement!r}）；可选值：{', '.join(SUPPORTED_WC_ARRANGEMENTS)}。"
                )
                arrangement = "split"
            card["arrangement"] = arrangement

            raw_window = raw.get("window")
            if not isinstance(raw_window, dict):
                errors.append(f"{label}.window 必须是包含 image 的对象。")
            else:
                uri = resolve_asset_path(
                    raw_window.get("image", ""),
                    base_dir,
                    f"{label}.window.image",
                    errors,
                )
                fit, position, bg = normalize_image_options(
                    raw_window,
                    f"{label}.window",
                    errors,
                    default_fit="contain",
                    default_position="center",
                )
                card["window"] = {
                    "image": uri,
                    "label": str(raw_window.get("label", "")).strip(),
                    "fit": fit,
                    "position": position,
                    "bg": bg,
                }

            raw_callouts = raw.get("callouts")
            if not isinstance(raw_callouts, list) or not (2 <= len(raw_callouts) <= 4):
                errors.append(f"{label}.callouts 必须包含 2 到 4 个局部标注卡。")
            else:
                callouts: list[dict[str, Any]] = []
                count = len(raw_callouts)
                for ci, co in enumerate(raw_callouts):
                    if not isinstance(co, dict):
                        errors.append(f"{label}.callouts[{ci}] 必须是对象。")
                        continue
                    uri = resolve_asset_path(
                        co.get("image", ""),
                        base_dir,
                        f"{label}.callouts[{ci}].image",
                        errors,
                    )
                    fit, position, bg = normalize_image_options(
                        co,
                        f"{label}.callouts[{ci}]",
                        errors,
                        default_fit="cover",
                        default_position="center",
                    )
                    default_x = round(18 + (64 * ci / max(1, count - 1)), 1)
                    target_x = co.get("targetX", default_x)
                    target_y = co.get("targetY", 62.0)
                    if not isinstance(target_x, (int, float)) or not (0 <= target_x <= 100):
                        errors.append(f"{label}.callouts[{ci}].targetX 必须在 0 到 100 之间。")
                        target_x = default_x
                    if not isinstance(target_y, (int, float)) or not (0 <= target_y <= 100):
                        errors.append(f"{label}.callouts[{ci}].targetY 必须在 0 到 100 之间。")
                        target_y = 62.0
                    target_w = co.get("targetW")
                    target_h = co.get("targetH")
                    if target_w is not None and (not isinstance(target_w, (int, float)) or not (1 <= target_w <= 100)):
                        errors.append(f"{label}.callouts[{ci}].targetW 必须在 1 到 100 之间。")
                        target_w = None
                    if target_h is not None and (not isinstance(target_h, (int, float)) or not (1 <= target_h <= 100)):
                        errors.append(f"{label}.callouts[{ci}].targetH 必须在 1 到 100 之间。")
                        target_h = None
                    co_title = str(co.get("title") or co.get("label") or "").strip()
                    if not co_title:
                        errors.append(f"{label}.callouts[{ci}].title 不能为空。")
                    callouts.append(
                        {
                            "index": str(co.get("index") or f"{ci + 1:02d}").strip(),
                            "tag": str(co.get("tag", "")).strip(),
                            "title": co_title,
                            "caption": str(co.get("caption", "")).strip(),
                            "image": uri,
                            "targetX": float(target_x),
                            "targetY": float(target_y),
                            "targetW": float(target_w) if target_w is not None else None,
                            "targetH": float(target_h) if target_h is not None else None,
                            "fit": fit,
                            "position": position,
                            "bg": bg,
                        }
                    )
                card["callouts"] = callouts

        if layout == "before-after":
            for side_key, default_lbl in (("before", "BEFORE"), ("after", "AFTER")):
                raw_side = raw.get(side_key)
                if not isinstance(raw_side, dict):
                    errors.append(f"{label}.{side_key} 必须是包含 image 的对象。")
                else:
                    uri = resolve_asset_path(
                        raw_side.get("image", ""),
                        base_dir,
                        f"{label}.{side_key}.image",
                        errors,
                    )
                    fit, position, bg = normalize_image_options(
                        raw_side,
                        f"{label}.{side_key}",
                        errors,
                        default_fit="cover",
                        default_position="center",
                    )
                    card[side_key] = {
                        "label": str(raw_side.get("label") or default_lbl).strip(),
                        "image": uri,
                        "caption": str(raw_side.get("caption", "")).strip(),
                        "fit": fit,
                        "position": position,
                        "bg": bg,
                    }
            card["compareNote"] = str(raw.get("compareNote", "")).strip()

        if layout == "workflow-steps":
            raw_steps = raw.get("steps")
            if not isinstance(raw_steps, list) or not (3 <= len(raw_steps) <= 4):
                errors.append(f"{label}.steps 必须包含 3 到 4 个步骤项。")
            else:
                steps: list[dict[str, Any]] = []
                for si, st in enumerate(raw_steps):
                    if not isinstance(st, dict):
                        errors.append(f"{label}.steps[{si}] 必须是对象。")
                        continue
                    uri = resolve_asset_path(
                        st.get("image", ""),
                        base_dir,
                        f"{label}.steps[{si}].image",
                        errors,
                    )
                    fit, position, bg = normalize_image_options(
                        st,
                        f"{label}.steps[{si}]",
                        errors,
                        default_fit="cover",
                        default_position="top center",
                    )
                    st_title = str(st.get("title") or st.get("label") or "").strip()
                    if not st_title:
                        errors.append(f"{label}.steps[{si}].title 不能为空。")
                    steps.append(
                        {
                            "index": str(st.get("index") or f"{si + 1:02d}").strip(),
                            "title": st_title,
                            "caption": str(st.get("caption", "")).strip(),
                            "image": uri,
                            "fit": fit,
                            "position": position,
                            "bg": bg,
                        }
                    )
                card["steps"] = steps

        normalized.append(card)

    return normalized, errors


# ---------------------------------------------------------------------------
# Editorial HTML/CSS compilation for all 8 archetypes & 3 themes
# ---------------------------------------------------------------------------


COMMON_CSS = """
:root {
  --paper-base: #f6f3ec;
  --paper-bg: radial-gradient(ellipse 85% 75% at 22% 18%, #fbf9f4 0%, #f6f3ec 55%, #efeae0 100%);
  --ink: #1b1c19;
  --muted: #4a4d46;
  --meta: #5e6159;
  --sep: #8c8f86;
  --eyebrow-ink: #43463e;
  --body-ink: #272924;
  --caption-ink: #474a42;
  --frame-bg: #ffffff;
  --surface-bg: #fbfaf7;
  --pill-bg: rgba(255, 255, 255, 0.92);
  --pill-border: rgba(27, 28, 25, 0.10);
  --hairline: rgba(27, 28, 25, 0.09);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.28) 0%, rgba(255, 255, 255, 0.06) 100%);
  --glass-border: rgba(255, 255, 255, 0.68);
  --glass-inner-ring: rgba(27, 28, 25, 0.08);
  --shadow-card: 0 22px 48px -18px rgba(24, 22, 18, 0.18), 0 4px 12px -4px rgba(24, 22, 18, 0.08);
  --shadow-plate: 0 18px 38px -14px rgba(24, 22, 18, 0.22), 0 3px 10px -3px rgba(24, 22, 18, 0.10);
  --connector-opacity: 0.50;
  --halo-ring: rgba(255, 255, 255, 0.90);
  --bridge-bg: #fbfaf7;
  --bridge-border: rgba(27, 28, 25, 0.12);
  --bridge-ink: #2f312b;
  --font-serif: 'Playfair Display', 'Songti SC', 'Noto Serif CJK SC', 'STSong', Georgia, serif;
  --font-sans: 'Inter', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}

.theme-dark-studio {
  --paper-base: #101114;
  --paper-bg: radial-gradient(ellipse 85% 75% at 24% 16%, #1a1c22 0%, #101114 58%, #0d0e11 100%);
  --ink: #f3f4f6;
  --muted: #b0b6c2;
  --meta: #8e95a3;
  --sep: #5b6270;
  --eyebrow-ink: #c7cdd8;
  --body-ink: #dfe3eb;
  --caption-ink: #bcc2ce;
  --frame-bg: #17191e;
  --surface-bg: #16181d;
  --pill-bg: rgba(24, 27, 34, 0.92);
  --pill-border: rgba(255, 255, 255, 0.12);
  --hairline: rgba(255, 255, 255, 0.10);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.10) 0%, rgba(255, 255, 255, 0.03) 100%);
  --glass-border: rgba(255, 255, 255, 0.22);
  --glass-inner-ring: rgba(255, 255, 255, 0.08);
  --shadow-card: 0 28px 60px -20px rgba(0, 0, 0, 0.68), 0 6px 18px -4px rgba(0, 0, 0, 0.45);
  --shadow-plate: 0 22px 46px -16px rgba(0, 0, 0, 0.72), 0 5px 14px -4px rgba(0, 0, 0, 0.48);
  --connector-opacity: 0.72;
  --halo-ring: rgba(16, 17, 20, 0.88);
  --bridge-bg: #1a1d24;
  --bridge-border: rgba(255, 255, 255, 0.14);
  --bridge-ink: #e5e7eb;
}

.theme-cool-gallery {
  --paper-base: #f2f4f7;
  --paper-bg: radial-gradient(ellipse 85% 75% at 22% 18%, #fafbfd 0%, #f2f4f7 56%, #e9edf2 100%);
  --ink: #14171d;
  --muted: #4b525e;
  --meta: #616976;
  --sep: #8f98a6;
  --eyebrow-ink: #3e4552;
  --body-ink: #20252e;
  --caption-ink: #464d59;
  --frame-bg: #ffffff;
  --surface-bg: #f8f9fb;
  --pill-bg: rgba(255, 255, 255, 0.92);
  --pill-border: rgba(20, 23, 29, 0.10);
  --hairline: rgba(20, 23, 29, 0.09);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.32) 0%, rgba(255, 255, 255, 0.08) 100%);
  --glass-border: rgba(255, 255, 255, 0.74);
  --glass-inner-ring: rgba(20, 23, 29, 0.08);
  --shadow-card: 0 22px 48px -18px rgba(15, 23, 42, 0.14), 0 4px 12px -4px rgba(15, 23, 42, 0.07);
  --shadow-plate: 0 18px 38px -14px rgba(15, 23, 42, 0.18), 0 3px 10px -3px rgba(15, 23, 42, 0.09);
  --connector-opacity: 0.54;
  --halo-ring: rgba(255, 255, 255, 0.90);
  --bridge-bg: #ffffff;
  --bridge-border: rgba(20, 23, 29, 0.11);
  --bridge-ink: #1f242d;
}

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body {
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: var(--paper-base);
  color: var(--ink);
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

.sc-canvas {
  position: relative;
  width: var(--canvas-w);
  height: var(--canvas-h);
  overflow: hidden;
  background: var(--paper-bg);
  color: var(--ink);
}

.sc-brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  line-height: 1;
  color: var(--ink);
}

.sc-brand-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  object-fit: contain;
  display: block;
}

.sc-brand-name {
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--ink);
}

.sc-brand-sep {
  color: var(--sep);
  font-weight: 400;
}

.sc-brand-tag {
  font-family: var(--font-mono);
  font-size: 15px;
  font-weight: 400;
  color: var(--caption-ink);
  letter-spacing: 0.01em;
}

.sc-title {
  font-family: var(--font-serif);
  font-weight: 400;
  color: var(--ink);
  letter-spacing: -0.025em;
  line-height: 1.05;
}

.sc-title.is-cjk {
  font-weight: 500;
  letter-spacing: -0.01em;
  line-height: 1.22;
}

.display-sans .sc-title {
  font-family: var(--font-sans);
  font-weight: 600;
  letter-spacing: -0.032em;
  line-height: 1.07;
}

.display-sans .sc-title.is-cjk {
  font-weight: 600;
  letter-spacing: -0.018em;
  line-height: 1.18;
}

.sc-subtitle {
  font-family: var(--font-sans);
  font-size: 20px;
  font-weight: 400;
  line-height: 1.48;
  color: var(--muted);
  text-wrap: balance;
}

.sc-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 14.5px;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--eyebrow-ink);
}

.sc-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--accent);
  flex: none;
}

.sc-footer {
  font-family: var(--font-mono);
  font-size: 14.5px;
  line-height: 1.5;
  color: var(--meta);
  letter-spacing: 0.01em;
}

.sc-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 24px;
  font-size: 16px;
  line-height: 1.3;
  color: var(--body-ink);
}

.sc-legend-item {
  display: inline-flex;
  align-items: baseline;
  gap: 7px;
}

.sc-idx {
  font-family: var(--font-mono);
  font-weight: 500;
  color: var(--accent);
  font-size: 15px;
}

.sc-frame {
  position: relative;
  overflow: hidden;
  border-radius: 16px;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
}

.sc-frame img {
  width: 100%;
  height: 100%;
  display: block;
}

.sc-layer-card {
  position: absolute;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-plate);
}

.sc-layer-card.is-transparent {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  box-shadow:
    inset 0 0 0 1px var(--glass-inner-ring),
    var(--shadow-plate);
}

.sc-layer-card img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: top center;
}

.sc-layer-card.is-transparent img {
  object-fit: cover;
  object-position: center bottom;
}

.sc-layer-badge {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 4;
  padding: 4px 9px;
  border-radius: 6px;
  background: var(--pill-bg);
  border: 1px solid var(--pill-border);
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  color: var(--accent);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.14);
}

/* ---------- 1. layered-hero ---------- */
.layout-layered-hero.orient-landscape {
  display: grid;
  grid-template-columns: 44% 56%;
  grid-template-rows: minmax(0, 1fr);
}

.layout-layered-hero.orient-landscape .lh-left {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 36px 10px 34px 44px;
  min-height: 0;
  z-index: 2;
}

.layout-layered-hero.orient-landscape .lh-header {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: none;
}

.layout-layered-hero.orient-landscape .sc-title {
  font-size: 62px;
  margin-top: 6px;
}

.layout-layered-hero.orient-landscape .sc-title.is-cjk {
  font-size: 50px;
}

.layout-layered-hero.orient-landscape .sc-subtitle {
  max-width: 480px;
  font-size: 20px;
}

.layout-layered-hero.orient-landscape .lh-stack {
  position: relative;
  flex: 1 1 0;
  min-height: 280px;
  margin: 22px 0 18px;
}

.layout-layered-hero.orient-landscape .lh-layer-0 {
  top: 2%;
  left: -4px;
  width: 62%;
  height: 70%;
  transform: rotate(-1.2deg);
  z-index: 1;
}

.layout-layered-hero.orient-landscape .lh-layer-1 {
  top: 13%;
  left: 18%;
  width: 62%;
  height: 70%;
  transform: rotate(-2.4deg);
  z-index: 2;
}

.layout-layered-hero.orient-landscape .lh-layer-2 {
  top: 25%;
  left: 36%;
  width: 62%;
  height: 70%;
  transform: rotate(-3.4deg);
  z-index: 3;
}

.layout-layered-hero.orient-landscape .lh-right {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  padding: 42px 0 34px 22px;
  min-height: 0;
}

.layout-layered-hero.orient-landscape .lh-hero-wrap {
  position: relative;
  width: 106%;
  display: flex;
  flex-direction: column;
  gap: 14px;
  transform-origin: center center;
}

.layout-layered-hero.orient-landscape .lh-hero-wrap.is-tilted {
  transform: rotate(-2.1deg) translateX(8px);
}

.layout-layered-hero.orient-landscape .lh-hero-frame {
  width: 100%;
  aspect-ratio: 16 / 10;
  border-radius: 18px;
}

/* layered-hero vertical / square */
.layout-layered-hero.orient-portrait,
.layout-layered-hero.orient-square {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 46px 52px 42px;
  gap: 24px;
}

.layout-layered-hero.orient-portrait .lh-header,
.layout-layered-hero.orient-square .lh-header {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.layout-layered-hero.orient-portrait .sc-title {
  font-size: 56px;
}

.layout-layered-hero.orient-portrait .sc-title.is-cjk {
  font-size: 48px;
}

.layout-layered-hero.orient-square .sc-title {
  font-size: 48px;
}

.layout-layered-hero.orient-square .sc-title.is-cjk {
  font-size: 42px;
}

.layout-layered-hero.orient-portrait .lh-hero-wrap,
.layout-layered-hero.orient-square .lh-hero-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.layout-layered-hero.orient-portrait .lh-hero-frame {
  width: 100%;
  aspect-ratio: 16 / 10;
  border-radius: 18px;
}

.layout-layered-hero.orient-square .lh-hero-frame {
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: 16px;
}

.layout-layered-hero.orient-portrait .lh-stack-row,
.layout-layered-hero.orient-square .lh-stack-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 18px;
}

.layout-layered-hero.orient-portrait .lh-stack-tile,
.layout-layered-hero.orient-square .lh-stack-tile {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: 12px;
}

/* ---------- 2. layered-bento ---------- */
.layout-layered-bento.orient-landscape {
  display: grid;
  grid-template-columns: 38.5% 61.5%;
  grid-template-rows: minmax(0, 1fr);
}

.layout-layered-bento.orient-landscape .lb-left {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 44px 16px 40px 52px;
  min-height: 0;
  z-index: 2;
}

.layout-layered-bento.orient-landscape .lb-header {
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex: none;
}

.layout-layered-bento.orient-landscape .sc-title {
  font-size: 52px;
  margin-top: 6px;
}

.layout-layered-bento.orient-landscape .sc-title.is-cjk {
  font-size: 46px;
}

.layout-layered-bento.orient-landscape .sc-subtitle {
  font-size: 19.5px;
  max-width: 430px;
}

.layout-layered-bento.orient-landscape .lb-stack {
  position: relative;
  flex: 1 1 0;
  min-height: 260px;
  margin: 20px 0 16px;
}

.layout-layered-bento.orient-landscape .lb-layer-0 {
  top: 2%;
  left: -4px;
  width: 64%;
  height: 58%;
  transform: rotate(-1.1deg);
  z-index: 1;
}

.layout-layered-bento.orient-landscape .lb-layer-1 {
  top: 17%;
  left: 14%;
  width: 67%;
  height: 60%;
  transform: rotate(-0.8deg);
  z-index: 2;
}

.layout-layered-bento.orient-landscape .lb-layer-2 {
  top: 37%;
  left: 31%;
  width: 68%;
  height: 58%;
  transform: rotate(0.5deg);
  z-index: 3;
}

.layout-layered-bento.orient-landscape .lb-right {
  padding: 34px 40px 34px 14px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-auto-rows: minmax(0, 1fr);
  gap: 16px;
  min-height: 0;
}

.lb-tile {
  position: relative;
  overflow: hidden;
  border-radius: 16px;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
  min-height: 0;
}

.lb-tile.span-2 {
  grid-column: span 2;
}

.lb-tile img {
  width: 100%;
  height: 100%;
  display: block;
}

/* layered-bento portrait / square */
.layout-layered-bento.orient-portrait,
.layout-layered-bento.orient-square {
  display: flex;
  flex-direction: column;
  padding: 42px 46px 38px;
  gap: 22px;
}

.layout-layered-bento.orient-portrait .lb-header,
.layout-layered-bento.orient-square .lb-header {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.layout-layered-bento.orient-portrait .sc-title {
  font-size: 48px;
}

.layout-layered-bento.orient-square .sc-title {
  font-size: 42px;
}

.layout-layered-bento.orient-portrait .lb-stack-row,
.layout-layered-bento.orient-square .lb-stack-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}

.layout-layered-bento.orient-portrait .lb-stack-tile,
.layout-layered-bento.orient-square .lb-stack-tile {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: 12px;
}

.layout-layered-bento.orient-portrait .lb-grid,
.layout-layered-bento.orient-square .lb-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

/* ---------- 3. section-assembly ---------- */
.layout-section-assembly.orient-landscape {
  display: grid;
  grid-template-columns: 28% 28.5% 43.5%;
  grid-template-rows: minmax(0, 1fr);
}

.layout-section-assembly.orient-landscape .sa-left {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 46px 18px 40px 54px;
  min-height: 0;
  z-index: 2;
}

.layout-section-assembly.orient-landscape .sa-header {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.layout-section-assembly.orient-landscape .sc-title {
  font-size: 54px;
  margin-top: 6px;
}

.layout-section-assembly.orient-landscape .sc-title.is-cjk {
  font-size: 46px;
}

.layout-section-assembly.orient-landscape .sc-subtitle {
  font-size: 19px;
  line-height: 1.48;
}

.sa-steps {
  display: flex;
  flex-direction: column;
  gap: 13px;
  margin-top: 20px;
}

.sa-step {
  display: grid;
  grid-template-columns: 32px 1fr;
  align-items: baseline;
  gap: 6px;
}

.sc-step-text {
  font-size: 17px;
  line-height: 1.42;
  color: var(--body-ink);
}

.layout-section-assembly.orient-landscape .sa-middle {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 42px 18px 34px 12px;
  gap: 14px;
  min-height: 0;
  z-index: 2;
}

.sa-plate-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-height: 0;
}

.sc-section-caption {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-family: var(--font-mono);
  font-size: 14px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--caption-ink);
}

.sa-plate-row {
  display: flex;
  gap: 10px;
  flex: 1;
  min-height: 0;
  align-items: stretch;
}

.sa-plate {
  position: relative;
  overflow: hidden;
  border-radius: 12px;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-plate);
  transform: rotate(-1.2deg);
  min-width: 0;
}

.sa-plate img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center;
}

.layout-section-assembly.orient-landscape .sa-right {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 36px 46px 0 128px;
  min-height: 0;
  z-index: 2;
}

.sa-showcase {
  flex: 1;
  min-height: 0;
  border-radius: 16px 16px 0 0;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
  overflow: hidden;
  transform-origin: top center;
}

.sa-showcase.is-tilted {
  transform: rotate(-1.4deg) translateY(4px);
}

.sa-showcase img {
  width: 100%;
  height: 100%;
  display: block;
}

.sc-connectors {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 3;
}

/* section-assembly portrait / square */
.layout-section-assembly.orient-portrait,
.layout-section-assembly.orient-square {
  display: flex;
  flex-direction: column;
  padding: 40px 44px 32px;
  gap: 20px;
}

.layout-section-assembly.orient-portrait .sa-top,
.layout-section-assembly.orient-square .sa-top {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.layout-section-assembly.orient-portrait .sc-title {
  font-size: 46px;
}

.layout-section-assembly.orient-square .sc-title {
  font-size: 40px;
}

.layout-section-assembly.orient-portrait .sa-steps,
.layout-section-assembly.orient-square .sa-steps {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 18px;
  margin-top: 6px;
}

.layout-section-assembly.orient-portrait .sa-stage,
.layout-section-assembly.orient-square .sa-stage {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 44% 56%;
  gap: 36px;
}

.layout-section-assembly.orient-portrait .sa-middle,
.layout-section-assembly.orient-square .sa-middle {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 12px;
  min-height: 0;
}

.layout-section-assembly.orient-portrait .sa-right,
.layout-section-assembly.orient-square .sa-right {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

/* ---------- 4. module-bento ---------- */
.layout-module-bento {
  display: flex;
  flex-direction: column;
  padding: 28px 38px 30px;
  gap: 18px;
}

.mb-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.mb-title-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.layout-module-bento .sc-title {
  font-size: 46px;
  line-height: 1.12;
}

.layout-module-bento .sc-title.is-cjk {
  font-size: 42px;
}

.layout-module-bento .sc-subtitle {
  font-size: 19px;
  max-width: 780px;
}

.mb-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-auto-rows: minmax(0, 1fr);
  gap: 20px;
}

.mb-grid.cols-3 {
  grid-template-columns: repeat(3, 1fr);
}

.mb-grid.cols-2 {
  grid-template-columns: repeat(2, 1fr);
}

.mb-grid.layout-5 {
  grid-template-columns: repeat(6, 1fr);
}

.mb-grid.layout-5 .mb-card:nth-child(1),
.mb-grid.layout-5 .mb-card:nth-child(2) {
  grid-column: span 3;
}

.mb-grid.layout-5 .mb-card:nth-child(3),
.mb-grid.layout-5 .mb-card:nth-child(4),
.mb-grid.layout-5 .mb-card:nth-child(5) {
  grid-column: span 2;
}

.mb-card {
  position: relative;
  overflow: hidden;
  border-radius: 16px;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.mb-card-media {
  flex: 1;
  min-height: 0;
  position: relative;
  background: var(--frame-bg);
}

.mb-card-media img {
  width: 100%;
  height: 100%;
  display: block;
}

.mb-card-label {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 15px;
  font-family: var(--font-mono);
  font-size: 15px;
  color: var(--body-ink);
  background: var(--surface-bg);
  border-top: 1px solid var(--hairline);
}

.mb-card-label .sc-idx {
  font-size: 15px;
  flex: none;
}

.mb-header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.lb-right-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 34px 40px 34px 14px;
  min-height: 0;
}

.lb-right-col .lb-right {
  flex: 1;
  padding: 0;
}

.lh-bottom-meta,
.lb-bottom-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.layout-module-bento.orient-portrait .mb-title-row,
.layout-module-bento.orient-square .mb-title-row {
  flex-direction: column;
  gap: 8px;
}

/* ---------- 5. gallery-wall (tilted-pages) ---------- */
.layout-gallery-wall.variant-tilted-pages.orient-landscape {
  display: grid;
  grid-template-columns: 29.5% 70.5%;
  grid-template-rows: minmax(0, 1fr);
}

.gw-left {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 50px 24px 44px 56px;
  min-height: 0;
  z-index: 2;
}

.gw-header {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.layout-gallery-wall.variant-tilted-pages.orient-landscape .sc-title {
  font-size: 60px;
  margin-top: 10px;
}

.layout-gallery-wall.variant-tilted-pages.orient-landscape .sc-title.is-cjk {
  font-size: 50px;
}

.gw-pages-stage {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 28px;
  padding: 46px 0 36px 20px;
  overflow: hidden;
}

.gw-page-col {
  flex: 0 0 280px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transform: rotate(-4deg);
  transform-origin: top left;
}

.gw-page-col:nth-child(2) {
  margin-top: 14px;
}

.gw-page-col:nth-child(3) {
  margin-top: 28px;
}

.gw-page-col:nth-child(4) {
  margin-top: 42px;
}

.gw-page-label {
  font-family: var(--font-mono);
  font-size: 15.5px;
  color: var(--caption-ink);
  padding-left: 4px;
}

.gw-page-frame {
  width: 100%;
  height: 700px;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
}

.gw-page-frame img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: top center;
}

.layout-gallery-wall.variant-tilted-pages.orient-portrait,
.layout-gallery-wall.variant-tilted-pages.orient-square {
  display: flex;
  flex-direction: column;
  padding: 42px 44px 36px;
  gap: 26px;
}

.layout-gallery-wall.variant-tilted-pages.orient-portrait .sc-title {
  font-size: 50px;
}

.layout-gallery-wall.variant-tilted-pages.orient-square .sc-title {
  font-size: 44px;
}

.layout-gallery-wall.variant-tilted-pages.orient-portrait .gw-pages-stage,
.layout-gallery-wall.variant-tilted-pages.orient-square .gw-pages-stage {
  flex: 1;
  min-height: 0;
  padding: 16px 6px 10px;
  gap: 20px;
}

.layout-gallery-wall.variant-tilted-pages.orient-portrait .gw-page-col,
.layout-gallery-wall.variant-tilted-pages.orient-square .gw-page-col {
  flex: 1 1 0;
  min-width: 0;
  transform: rotate(-2.5deg);
}

.layout-gallery-wall.variant-tilted-pages.orient-portrait .gw-page-frame {
  height: 920px;
}

.layout-gallery-wall.variant-tilted-pages.orient-square .gw-page-frame {
  height: 700px;
}

/* ---------- 5b. gallery-wall (collage) ---------- */
.gw-collage-title-block {
  position: absolute;
  top: 44px;
  left: 54px;
  width: 34%;
  z-index: 12;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.gw-collage-title-block .sc-title {
  font-size: 52px;
  margin-top: 6px;
}

.gw-collage-title-block .sc-title.is-cjk {
  font-size: 44px;
}

.gw-collage-title-block .sc-subtitle {
  font-size: 19px;
  max-width: 450px;
}

.gw-collage-slot {
  position: absolute;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
  transform: rotate(-4.2deg);
}

.gw-collage-slot img {
  width: 100%;
  height: 100%;
  display: block;
}

.gw-foreground {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  width: 100%;
  max-height: 34%;
  object-fit: cover;
  object-position: bottom center;
  pointer-events: none;
  z-index: 20;
}

/* ---------- 6. window-callout ---------- */
.layout-window-callout {
  display: flex;
  flex-direction: column;
  padding: 26px 38px 24px;
  gap: 14px;
}

.wc-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  flex: none;
  z-index: 2;
}

.wc-title-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.layout-window-callout .sc-title {
  font-size: 38px;
  line-height: 1.08;
}

.layout-window-callout .sc-title.is-cjk {
  font-size: 34px;
}

.layout-window-callout .sc-subtitle {
  font-size: 19px;
  max-width: 780px;
  line-height: 1.44;
}

/* Default split arrangement on landscape (16:9 / 4:3): huge left window + magnified right stack */
.wc-split-body {
  flex: 1 1 0;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.56fr) 52px minmax(0, 1fr);
  align-items: stretch;
  z-index: 2;
}

.wc-split-body .wc-main-stage {
  flex: none;
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.wc-split-body .wc-window-wrap {
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  margin: 0 auto;
}

.wc-gutter {
  pointer-events: none;
}

.wc-callouts-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  height: 100%;
}

.wc-callouts-stack .wc-callout-card {
  flex: 1 1 0;
  min-height: 0;
}

.wc-callouts-stack .wc-callout-meta {
  padding: 9px 14px 10px;
  gap: 0;
}

.wc-callouts-stack .wc-callout-head {
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.wc-callouts-stack .wc-callout-title {
  font-size: 17px;
  white-space: nowrap;
  flex: none;
}

.wc-callout-inline-sep {
  color: var(--sep);
  font-size: 14px;
  flex: none;
}

.wc-callout-inline-cap {
  font-size: 15px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.wc-main-stage {
  flex: 1.65 1 0;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  z-index: 2;
}

.wc-window-wrap {
  position: relative;
  height: 100%;
  width: 82%;
  max-width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.wc-window-label {
  font-family: var(--font-mono);
  font-size: 14px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--caption-ink);
  flex: none;
}

.wc-window-frame {
  position: relative;
  flex: 1;
  min-height: 0;
  border-radius: 16px;
  overflow: hidden;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
}

.wc-window-frame img {
  width: 100%;
  height: 100%;
  display: block;
}

.wc-runway {
  height: 28px;
  flex: none;
}

.wc-callouts-grid {
  flex: 0.90 1 0;
  min-height: 0;
  display: grid;
  gap: 18px;
  z-index: 2;
}

.wc-callouts-grid.cols-2 { grid-template-columns: repeat(2, 1fr); }
.wc-callouts-grid.cols-3 { grid-template-columns: repeat(3, 1fr); }
.wc-callouts-grid.cols-4 { grid-template-columns: repeat(4, 1fr); }

.wc-callout-card {
  position: relative;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid var(--hairline);
  background: var(--surface-bg);
  box-shadow: var(--shadow-plate);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.wc-callout-media {
  flex: 1;
  min-height: 0;
  position: relative;
  background: var(--frame-bg);
  overflow: hidden;
}

.wc-callout-media img {
  width: 100%;
  height: 100%;
  display: block;
}

.wc-callout-meta {
  padding: 10px 14px 11px;
  border-top: 1px solid var(--hairline);
  background: var(--surface-bg);
  display: flex;
  flex-direction: column;
  gap: 3px;
  flex: none;
}

.wc-callout-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.wc-callout-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.28;
}

.wc-callout-tag {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--meta);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  flex: none;
}

.wc-callout-caption {
  font-size: 15px;
  line-height: 1.42;
  color: var(--muted);
}

.wc-pin-marker {
  position: absolute;
  transform: translate(-50%, -50%);
  width: 32px;
  height: 32px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--accent-ink);
  border: 2.5px solid var(--halo-ring);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 6;
}

.layout-window-callout.orient-portrait,
.layout-window-callout.orient-square {
  padding: 36px 40px 34px;
  gap: 18px;
}

.layout-window-callout.orient-portrait .wc-title-row,
.layout-window-callout.orient-square .wc-title-row {
  flex-direction: column;
  gap: 8px;
}

.layout-window-callout.orient-portrait .sc-title { font-size: 46px; }
.layout-window-callout.orient-portrait .sc-title.is-cjk { font-size: 42px; }
.layout-window-callout.orient-square .sc-title { font-size: 40px; }

.layout-window-callout.orient-portrait .wc-main-stage,
.layout-window-callout.orient-square .wc-main-stage {
  flex: none;
}

.layout-window-callout.orient-portrait .wc-window-wrap,
.layout-window-callout.orient-square .wc-window-wrap {
  width: 100%;
  height: auto;
}

.layout-window-callout.orient-portrait .wc-window-frame {
  aspect-ratio: 16 / 10;
}

.layout-window-callout.orient-square .wc-window-frame {
  aspect-ratio: 16 / 9;
}

.layout-window-callout.orient-portrait .wc-runway,
.layout-window-callout.orient-square .wc-runway {
  display: none;
}

.layout-window-callout.orient-portrait .wc-callouts-grid,
.layout-window-callout.orient-square .wc-callouts-grid {
  flex: 1 1 0;
  grid-template-columns: repeat(2, 1fr);
  grid-auto-rows: minmax(0, 1fr);
  gap: 16px;
}

/* Portrait 2-3 callouts (and 9:16 2-4 callouts): horizontal-split rows keep wide UI crops crystal clear */
.layout-window-callout.orient-portrait .wc-callouts-grid.cols-3,
.layout-window-callout.orient-portrait .wc-callouts-grid.cols-2,
.layout-window-callout.is-tall .wc-callouts-grid.cols-4 {
  grid-template-columns: 1fr;
  gap: 14px;
}

.layout-window-callout.orient-portrait .wc-callouts-grid.cols-3 .wc-callout-card,
.layout-window-callout.orient-portrait .wc-callouts-grid.cols-2 .wc-callout-card,
.layout-window-callout.is-tall .wc-callouts-grid.cols-4 .wc-callout-card {
  flex-direction: row;
}

.layout-window-callout.orient-portrait .wc-callouts-grid.cols-3 .wc-callout-media,
.layout-window-callout.orient-portrait .wc-callouts-grid.cols-2 .wc-callout-media,
.layout-window-callout.is-tall .wc-callouts-grid.cols-4 .wc-callout-media {
  flex: 0 0 58%;
}

.layout-window-callout.orient-portrait .wc-callouts-grid.cols-3 .wc-callout-meta,
.layout-window-callout.orient-portrait .wc-callouts-grid.cols-2 .wc-callout-meta,
.layout-window-callout.is-tall .wc-callouts-grid.cols-4 .wc-callout-meta {
  flex: 1;
  border-top: none;
  border-left: 1px solid var(--hairline);
  justify-content: center;
  padding: 16px 20px;
  gap: 6px;
}

.layout-window-callout.orient-portrait .wc-callouts-grid.cols-3 .wc-callout-title,
.layout-window-callout.orient-portrait .wc-callouts-grid.cols-2 .wc-callout-title,
.layout-window-callout.is-tall .wc-callouts-grid.cols-4 .wc-callout-title {
  font-size: 19px;
}

.layout-window-callout.orient-portrait .wc-callouts-grid.cols-3 .wc-callout-caption,
.layout-window-callout.orient-portrait .wc-callouts-grid.cols-2 .wc-callout-caption,
.layout-window-callout.is-tall .wc-callouts-grid.cols-4 .wc-callout-caption {
  font-size: 15.5px;
  line-height: 1.48;
}

/* ---------- 7. before-after ---------- */
.layout-before-after {
  display: flex;
  flex-direction: column;
  padding: 28px 38px 28px;
  gap: 18px;
}

.ba-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  flex: none;
}

.ba-title-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.layout-before-after .sc-title {
  font-size: 46px;
  line-height: 1.1;
}

.layout-before-after .sc-title.is-cjk {
  font-size: 42px;
}

.layout-before-after .sc-subtitle {
  font-size: 19.5px;
  max-width: 780px;
}

.ba-stage {
  position: relative;
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 34px;
}

.ba-pane {
  display: flex;
  flex-direction: column;
  gap: 11px;
  min-height: 0;
}

.ba-label-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.ba-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 14px;
  border-radius: 999px;
  background: var(--pill-bg);
  border: 1px solid var(--pill-border);
  font-family: var(--font-mono);
  font-size: 14.5px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--caption-ink);
}

.ba-pane.is-after .ba-pill {
  color: var(--ink);
  border-color: color-mix(in srgb, var(--accent) 44%, var(--pill-border));
}

.ba-frame {
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
  border-radius: 16px;
  border: 1px solid var(--hairline);
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
}

.ba-pane.is-after .ba-frame {
  border: 1.5px solid color-mix(in srgb, var(--accent) 52%, transparent);
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--accent) 18%, transparent),
    var(--shadow-card);
}

.ba-frame img {
  width: 100%;
  height: 100%;
  display: block;
}

.ba-caption {
  font-size: 16px;
  line-height: 1.45;
  color: var(--muted);
  flex: none;
}

.ba-bridge {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 46px;
  height: 46px;
  border-radius: 999px;
  background: var(--bridge-bg);
  border: 1px solid var(--bridge-border);
  box-shadow: var(--shadow-plate);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
  z-index: 6;
}

.ba-bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex: none;
}

.ba-note {
  font-family: var(--font-mono);
  font-size: 14.5px;
  color: var(--caption-ink);
}

.layout-before-after.orient-portrait {
  padding: 40px 44px 36px;
  gap: 20px;
}

.layout-before-after.orient-portrait .ba-title-row,
.layout-before-after.orient-square .ba-title-row {
  flex-direction: column;
  gap: 8px;
}

.layout-before-after.orient-portrait .ba-stage {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 28px;
}

/* ---------- 8. workflow-steps ---------- */
.layout-workflow-steps {
  display: flex;
  flex-direction: column;
  padding: 28px 38px 28px;
  gap: 18px;
}

.ws-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  flex: none;
}

.ws-title-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.layout-workflow-steps .sc-title {
  font-size: 46px;
  line-height: 1.1;
}

.layout-workflow-steps .sc-title.is-cjk {
  font-size: 42px;
}

.layout-workflow-steps .sc-subtitle {
  font-size: 19.5px;
  max-width: 780px;
}

.ws-flow {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: stretch;
  gap: 0;
}

.ws-step-card {
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-radius: 16px;
  border: 1px solid var(--hairline);
  background: var(--surface-bg);
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
}

.ws-step-media {
  flex: 1;
  min-height: 0;
  position: relative;
  background: var(--frame-bg);
  overflow: hidden;
}

.ws-step-media img {
  width: 100%;
  height: 100%;
  display: block;
}

.ws-step-body {
  padding: 15px 18px 17px;
  border-top: 1px solid var(--hairline);
  background: var(--surface-bg);
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex: none;
}

.ws-step-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.ws-step-title {
  font-size: 18.5px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.28;
}

.ws-step-caption {
  font-size: 15.5px;
  line-height: 1.46;
  color: var(--muted);
}

.ws-arrow {
  width: 30px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
  opacity: 0.72;
}

.layout-workflow-steps.orient-portrait,
.layout-workflow-steps.orient-square {
  padding: 40px 44px 36px;
  gap: 20px;
}

.layout-workflow-steps.orient-portrait .ws-title-row,
.layout-workflow-steps.orient-square .ws-title-row {
  flex-direction: column;
  gap: 8px;
}

.layout-workflow-steps.orient-portrait .ws-flow.is-vertical {
  flex-direction: column;
  gap: 0;
}

.layout-workflow-steps.orient-portrait .ws-flow.is-vertical .ws-step-card {
  flex-direction: row;
}

.layout-workflow-steps.orient-portrait .ws-flow.is-vertical .ws-step-media {
  flex: 0 0 56%;
}

.layout-workflow-steps.orient-portrait .ws-flow.is-vertical .ws-step-body {
  flex: 1;
  border-top: none;
  border-left: 1px solid var(--hairline);
  justify-content: center;
  padding: 24px 28px;
  gap: 10px;
}

.layout-workflow-steps.orient-portrait .ws-flow.is-vertical .ws-step-title {
  font-size: 22px;
}

.layout-workflow-steps.orient-portrait .ws-flow.is-vertical .ws-step-caption {
  font-size: 16.5px;
  line-height: 1.52;
}

.layout-workflow-steps.orient-portrait .ws-flow.is-vertical .ws-arrow {
  width: 100%;
  height: 24px;
}

.layout-workflow-steps .ws-flow.is-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-auto-rows: minmax(0, 1fr);
  gap: 18px;
}

/* ---------- Tall portrait (9:16, height/width > 1.6) refinements ---------- */
.sc-canvas.is-tall {
  padding: 54px 48px 46px;
}

.layout-layered-hero.is-tall,
.layout-layered-bento.is-tall,
.layout-section-assembly.is-tall,
.layout-module-bento.is-tall,
.layout-gallery-wall.variant-tilted-pages.is-tall,
.layout-window-callout.is-tall,
.layout-before-after.is-tall,
.layout-workflow-steps.is-tall {
  padding: 54px 48px 46px;
  gap: 26px;
}

.layout-layered-hero.is-tall .sc-title,
.layout-layered-bento.is-tall .sc-title,
.layout-section-assembly.is-tall .sc-title,
.layout-module-bento.is-tall .sc-title,
.layout-gallery-wall.variant-tilted-pages.is-tall .sc-title,
.layout-window-callout.is-tall .sc-title,
.layout-before-after.is-tall .sc-title,
.layout-workflow-steps.is-tall .sc-title {
  font-size: 52px;
}

.layout-layered-hero.is-tall .sc-title.is-cjk,
.layout-layered-bento.is-tall .sc-title.is-cjk,
.layout-section-assembly.is-tall .sc-title.is-cjk,
.layout-module-bento.is-tall .sc-title.is-cjk,
.layout-gallery-wall.variant-tilted-pages.is-tall .sc-title.is-cjk,
.layout-window-callout.is-tall .sc-title.is-cjk,
.layout-before-after.is-tall .sc-title.is-cjk,
.layout-workflow-steps.is-tall .sc-title.is-cjk {
  font-size: 48px;
}

.layout-layered-hero.is-tall .sc-subtitle,
.layout-layered-bento.is-tall .sc-subtitle,
.layout-window-callout.is-tall .sc-subtitle,
.layout-before-after.is-tall .sc-subtitle,
.layout-workflow-steps.is-tall .sc-subtitle {
  font-size: 21px;
  max-width: 100%;
}

.layout-layered-hero.is-tall .lh-hero-frame {
  aspect-ratio: 4 / 3;
}

.layout-layered-hero.is-tall .lh-stack-tile {
  aspect-ratio: 4 / 3;
}

.layout-gallery-wall.variant-tilted-pages.is-tall .gw-page-frame {
  height: 1220px;
}

.layout-before-after.is-tall .ba-stage {
  gap: 36px;
}

.layout-before-after.is-tall .ba-caption {
  font-size: 17px;
}

.layout-window-callout.is-tall .wc-callouts-grid.cols-3,
.layout-window-callout.is-tall .wc-callouts-grid.cols-2 {
  grid-template-columns: 1fr;
  gap: 18px;
}

.layout-window-callout.is-tall .wc-callouts-grid.cols-3 .wc-callout-card,
.layout-window-callout.is-tall .wc-callouts-grid.cols-2 .wc-callout-card {
  flex-direction: row;
}

.layout-window-callout.is-tall .wc-callouts-grid.cols-3 .wc-callout-media,
.layout-window-callout.is-tall .wc-callouts-grid.cols-2 .wc-callout-media {
  flex: 0 0 54%;
}

.layout-window-callout.is-tall .wc-callouts-grid.cols-3 .wc-callout-meta,
.layout-window-callout.is-tall .wc-callouts-grid.cols-2 .wc-callout-meta {
  flex: 1;
  border-top: none;
  border-left: 1px solid var(--hairline);
  justify-content: center;
  padding: 20px 24px;
  gap: 8px;
}

.layout-window-callout.is-tall .wc-callouts-grid.cols-3 .wc-callout-title,
.layout-window-callout.is-tall .wc-callouts-grid.cols-2 .wc-callout-title {
  font-size: 20px;
}

.layout-window-callout.is-tall .wc-callouts-grid.cols-3 .wc-callout-caption,
.layout-window-callout.is-tall .wc-callouts-grid.cols-2 .wc-callout-caption {
  font-size: 16px;
  line-height: 1.5;
}

.layout-workflow-steps.is-tall .ws-flow.is-vertical .ws-arrow {
  height: 28px;
}
"""


CONNECTOR_SCRIPT = r"""
<script>
window.__positionShowcaseConnectors = function () {
  const canvas = document.querySelector('.sc-canvas');
  if (!canvas) return;
  const svg = document.querySelector('.sc-connectors');
  const cRect = canvas.getBoundingClientRect();
  const cs = getComputedStyle(canvas);
  const accent = cs.getPropertyValue('--accent').trim() || '#1b1c19';
  const connOpacity = cs.getPropertyValue('--connector-opacity').trim() || '0.50';
  const haloRing = cs.getPropertyValue('--halo-ring').trim() || 'rgba(255,255,255,0.9)';
  const ns = 'http://www.w3.org/2000/svg';
  if (svg) {
    svg.setAttribute('viewBox', `0 0 ${cRect.width} ${cRect.height}`);
    svg.innerHTML = '';
  }

  function appendDot(cx, cy, r, fill, opacity) {
    if (!svg) return;
    const circle = document.createElementNS(ns, 'circle');
    circle.setAttribute('cx', cx.toFixed(1));
    circle.setAttribute('cy', cy.toFixed(1));
    circle.setAttribute('r', String(r));
    circle.setAttribute('fill', fill);
    if (opacity !== undefined) circle.setAttribute('fill-opacity', String(opacity));
    svg.appendChild(circle);
  }

  // 1. section-assembly connectors (middle plates -> right showcase)
  const showcase = document.querySelector('.sa-showcase');
  if (showcase && svg) {
    const sRect = showcase.getBoundingClientRect();
    const plates = document.querySelectorAll('.sa-plate-row[data-target-y]');
    plates.forEach((row) => {
      const rRect = row.getBoundingClientRect();
      const targetPct = parseFloat(row.getAttribute('data-target-y') || '50');
      const x1 = rRect.right - cRect.left + 14;
      const y1 = (rRect.top + rRect.bottom) / 2 - cRect.top;
      const x2 = sRect.left - cRect.left - 16;
      const y2 = (sRect.top - cRect.top) + sRect.height * (targetPct / 100);
      if (x2 <= x1 + 12) return;

      const path = document.createElementNS(ns, 'path');
      if (Math.abs(y2 - y1) < 36) {
        path.setAttribute('d', `M ${x1.toFixed(1)} ${y1.toFixed(1)} L ${x2.toFixed(1)} ${y1.toFixed(1)}`);
        appendDot(x1, y1, 4, accent);
        appendDot(x2, y1, 4, accent);
      } else {
        const midX = (x1 + x2) / 2;
        path.setAttribute(
          'd',
          `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${midX.toFixed(1)} ${y1.toFixed(1)}, ${midX.toFixed(1)} ${y2.toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`
        );
        appendDot(x1, y1, 4, accent);
        appendDot(x2, y2, 4, accent);
      }
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', accent);
      path.setAttribute('stroke-opacity', connOpacity);
      path.setAttribute('stroke-width', '1.5');
      path.setAttribute('stroke-dasharray', '4 4');
      svg.appendChild(path);
    });
  }

  // 2. window-callout sizing, image-relative coordinates, connectors & portrait pins
  const mainWindow = document.querySelector('.wc-window-frame');
  const mainWrap = document.querySelector('.wc-window-wrap');
  const mainStage = document.querySelector('.wc-main-stage');
  if (mainWindow) {
    const winImg = mainWindow.querySelector('img');
    const winFit = (winImg && getComputedStyle(winImg).objectFit) || 'contain';
    const isSplit = canvas.classList.contains('arrange-split') && canvas.classList.contains('orient-landscape');

    // Auto-hug window wrap to actual image aspect ratio when using contain
    if (winImg && winImg.naturalWidth > 0 && winImg.naturalHeight > 0 && winFit === 'contain') {
      const natRatio = winImg.naturalWidth / winImg.naturalHeight;
      if (isSplit && mainWrap && mainStage) {
        const stageRect = mainStage.getBoundingClientRect();
        const labelEl = mainWrap.querySelector('.wc-window-label');
        const labelH = labelEl ? (labelEl.getBoundingClientRect().height + 6) : 0;
        const availW = Math.floor(stageRect.width);
        const availH = Math.max(180, Math.floor(stageRect.height - labelH));
        if (availW > 120 && availH > 120) {
          const fitScale = Math.min(availW / winImg.naturalWidth, availH / winImg.naturalHeight);
          const boxW = Math.max(260, Math.round(winImg.naturalWidth * fitScale));
          const boxH = Math.max(180, Math.round(winImg.naturalHeight * fitScale));
          mainWrap.style.width = `${boxW}px`;
          mainWrap.style.height = `${boxH + labelH}px`;
        }
      } else if (canvas.classList.contains('orient-landscape') && mainWrap && mainStage) {
        const stageRect = mainStage.getBoundingClientRect();
        const frameRect = mainWindow.getBoundingClientRect();
        if (frameRect.height > 20 && stageRect.width > 100) {
          const idealW = Math.round(frameRect.height * natRatio);
          const clampedW = Math.min(Math.floor(stageRect.width), Math.max(260, idealW));
          mainWrap.style.width = `${clampedW}px`;
        }
      } else if (!canvas.classList.contains('orient-landscape')) {
        const clampedRatio = Math.max(1.0, Math.min(2.3, natRatio));
        mainWindow.style.aspectRatio = clampedRatio.toFixed(4);
      }
    }

    function getRenderedImageRect(imgEl) {
      const r = imgEl.getBoundingClientRect();
      if (!imgEl.naturalWidth || !imgEl.naturalHeight) return r;
      const style = getComputedStyle(imgEl);
      if (style.objectFit !== 'contain') return r;
      const scale = Math.min(r.width / imgEl.naturalWidth, r.height / imgEl.naturalHeight);
      const rw = imgEl.naturalWidth * scale;
      const rh = imgEl.naturalHeight * scale;
      const tokens = (style.objectPosition || '50% 50%').trim().split(/\s+/);
      function parsePos(tok, fallback) {
        if (!tok) return fallback;
        if (tok === 'left' || tok === 'top') return 0;
        if (tok === 'right' || tok === 'bottom') return 1;
        if (tok === 'center') return 0.5;
        if (tok.endsWith('%')) return parseFloat(tok) / 100;
        return fallback;
      }
      const px = parsePos(tokens[0], 0.5);
      const py = parsePos(tokens[1] || tokens[0], 0.5);
      return {
        left: r.left + (r.width - rw) * px,
        top: r.top + (r.height - rh) * py,
        width: rw,
        height: rh,
      };
    }

    const imgRect = winImg ? getRenderedImageRect(winImg) : mainWindow.getBoundingClientRect();

    if (canvas.classList.contains('orient-landscape') && svg) {
      const callouts = document.querySelectorAll('.wc-callout-card[data-target-x][data-target-y]');
      callouts.forEach((card) => {
        const kRect = card.getBoundingClientRect();
        const txPct = parseFloat(card.getAttribute('data-target-x') || '50');
        const tyPct = parseFloat(card.getAttribute('data-target-y') || '50');
        const twAttr = card.getAttribute('data-target-w');
        const thAttr = card.getAttribute('data-target-h');
        const x2 = (imgRect.left - cRect.left) + imgRect.width * (txPct / 100);
        const y2 = (imgRect.top - cRect.top) + imgRect.height * (tyPct / 100);

        // Optional subtle focus reticle box around target region
        let endX = x2;
        let endY = y2;
        const hasReticle = Boolean(twAttr && thAttr);
        if (hasReticle) {
          const rw = imgRect.width * (parseFloat(twAttr) / 100);
          const rh = imgRect.height * (parseFloat(thAttr) / 100);
          const rectEl = document.createElementNS(ns, 'rect');
          rectEl.setAttribute('x', (x2 - rw / 2).toFixed(1));
          rectEl.setAttribute('y', (y2 - rh / 2).toFixed(1));
          rectEl.setAttribute('width', rw.toFixed(1));
          rectEl.setAttribute('height', rh.toFixed(1));
          rectEl.setAttribute('rx', '6');
          rectEl.setAttribute('fill', accent);
          rectEl.setAttribute('fill-opacity', '0.09');
          rectEl.setAttribute('stroke', accent);
          rectEl.setAttribute('stroke-opacity', '0.72');
          rectEl.setAttribute('stroke-width', '1.6');
          svg.appendChild(rectEl);
          if (isSplit) {
            endX = x2 + rw / 2;
          } else {
            endY = y2 + rh / 2;
          }
        }

        const path = document.createElementNS(ns, 'path');
        let x1, y1;
        if (isSplit) {
          x1 = kRect.left - cRect.left - 8;
          y1 = (kRect.top + kRect.bottom) / 2 - cRect.top;
          const spanX = Math.max(48, x1 - endX);
          const pull1 = Math.max(46, Math.min(185, spanX * 0.44));
          const pull2 = Math.max(46, Math.min(185, spanX * 0.40));
          path.setAttribute(
            'd',
            `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${(x1 - pull1).toFixed(1)} ${y1.toFixed(1)}, ${(endX + pull2).toFixed(1)} ${endY.toFixed(1)}, ${endX.toFixed(1)} ${endY.toFixed(1)}`
          );
        } else {
          x1 = (kRect.left + kRect.right) / 2 - cRect.left;
          y1 = kRect.top - cRect.top - 6;
          const spanY = Math.max(40, y1 - endY);
          const lift1 = Math.max(58, Math.min(135, spanY * 0.48));
          const lift2 = Math.max(46, Math.min(150, spanY * 0.44));
          path.setAttribute(
            'd',
            `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${x1.toFixed(1)} ${(y1 - lift1).toFixed(1)}, ${endX.toFixed(1)} ${(endY + lift2).toFixed(1)}, ${endX.toFixed(1)} ${endY.toFixed(1)}`
          );
        }
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', accent);
        path.setAttribute('stroke-opacity', connOpacity);
        path.setAttribute('stroke-width', '1.6');
        path.setAttribute('stroke-dasharray', '4 4');
        svg.appendChild(path);

        // Start anchor dot at callout edge
        appendDot(x1, y1, 3.6, accent);
        // Target beacon: compact edge dot on focus reticle, or double-ring beacon when pinpoint
        if (hasReticle) {
          appendDot(endX, endY, 5.5, haloRing);
          appendDot(endX, endY, 3.6, accent);
        } else {
          appendDot(endX, endY, 11, accent, 0.24);
          appendDot(endX, endY, 6.2, haloRing);
          appendDot(endX, endY, 3.8, accent);
        }
      });
    } else {
      // Portrait / square: position numbered pin markers relative to rendered image content rect
      const frameRect = mainWindow.getBoundingClientRect();
      const pins = mainWindow.querySelectorAll('.wc-pin-marker[data-target-x][data-target-y]');
      pins.forEach((pin) => {
        const txPct = parseFloat(pin.getAttribute('data-target-x') || '50');
        const tyPct = parseFloat(pin.getAttribute('data-target-y') || '50');
        const localX = (imgRect.left - frameRect.left) + imgRect.width * (txPct / 100);
        const localY = (imgRect.top - frameRect.top) + imgRect.height * (tyPct / 100);
        pin.style.left = `${localX.toFixed(1)}px`;
        pin.style.top = `${localY.toFixed(1)}px`;
      });
    }
  }
};
window.addEventListener('DOMContentLoaded', () => window.__positionShowcaseConnectors());
</script>
"""


def render_brand_html(brand: dict[str, Any] | None) -> str:
    if not brand:
        return ""
    parts: list[str] = []
    if brand.get("icon"):
        parts.append(f'<img class="sc-brand-icon" src="{esc(brand["icon"])}" alt="">')
    if brand.get("name"):
        parts.append(f'<span class="sc-brand-name">{esc(brand["name"])}</span>')
    if brand.get("name") and brand.get("tag"):
        parts.append('<span class="sc-brand-sep">·</span>')
    if brand.get("tag"):
        parts.append(f'<span class="sc-brand-tag">{esc(brand["tag"])}</span>')
    return f'<div class="sc-brand">{"".join(parts)}</div>'


def render_title_html(title: str) -> str:
    if not title:
        return ""
    cls = "sc-title is-cjk" if has_cjk(title) else "sc-title"
    return f'<h1 class="{cls}">{esc_multiline(title)}</h1>'


def render_subtitle_html(subtitle: str) -> str:
    if not subtitle:
        return ""
    return f'<p class="sc-subtitle">{esc_multiline(subtitle)}</p>'


def render_eyebrow_html(eyebrow: str) -> str:
    if not eyebrow:
        return ""
    return f'<div class="sc-eyebrow"><span class="sc-dot"></span><span>{esc(eyebrow)}</span></div>'


def render_footer_html(footer: str) -> str:
    if not footer:
        return ""
    return f'<div class="sc-footer">{esc_multiline(footer)}</div>'


def render_legend_html(layers: list[dict[str, Any]]) -> str:
    items = [
        f'<span class="sc-legend-item"><span class="sc-idx">{esc(layer["index"])}</span>'
        f'<span>{esc(layer["label"])}</span></span>'
        for layer in layers
    ]
    return f'<div class="sc-legend">{"".join(items)}</div>'


def build_img_style(fit: str, position: str) -> str:
    return f"object-fit:{esc(fit)};object-position:{esc(position)};"


def build_bg_style(bg: str, extra: str = "") -> str:
    parts: list[str] = []
    if extra:
        parts.append(extra.rstrip(";") + ";")
    if bg:
        parts.append(f"background:{esc(bg)};")
    return "".join(parts)


def render_layer_card(
    layer: dict[str, Any],
    extra_cls: str,
    show_badge: bool,
    *,
    inline_relative: bool = False,
) -> str:
    trans_cls = " is-transparent" if layer.get("transparent") else ""
    badge = (
        f'<span class="sc-layer-badge">{esc(layer["index"])}</span>'
        if show_badge
        else ""
    )
    box_style = build_bg_style(
        layer.get("bg", ""),
        extra="position:relative;" if inline_relative else "",
    )
    box_attr = f' style="{box_style}"' if box_style else ""
    img_style = build_img_style(layer["fit"], layer["position"])
    return (
        f'<div class="sc-layer-card {extra_cls}{trans_cls}"{box_attr}>'
        f'{badge}<img src="{esc(layer["image"])}" alt="{esc(layer["label"])}" style="{img_style}">'
        f"</div>"
    )


def build_layered_hero_body(card: dict[str, Any]) -> str:
    layers = card["layers"]
    hero = card["hero"]
    show_badges = card["showIndexBadges"]
    fit_style = build_img_style(hero["fit"], hero["position"])
    frame_bg = build_bg_style(hero.get("bg", ""))
    frame_style_attr = f' style="{frame_bg}"' if frame_bg else ""
    tilt_cls = " is-tilted" if hero["tilt"] else ""
    footer_html = render_footer_html(card["footer"])

    if card["orientation"] == "landscape":
        layer_cards = "".join(
            render_layer_card(layer, f"lh-layer-{idx}", show_badges)
            for idx, layer in enumerate(layers)
        )
        return f"""
        <div class="lh-left">
          <div class="lh-header">
            {render_brand_html(card["brand"])}
            {render_title_html(card["title"])}
            {render_subtitle_html(card["subtitle"])}
          </div>
          <div class="lh-stack">{layer_cards}</div>
          <div class="lh-bottom-meta">
            {render_legend_html(layers)}
            {footer_html}
          </div>
        </div>
        <div class="lh-right">
          <div class="lh-hero-wrap{tilt_cls}">
            {render_eyebrow_html(card["eyebrow"])}
            <div class="sc-frame lh-hero-frame"{frame_style_attr}>
              <img src="{esc(hero["image"])}" alt="" style="{fit_style}">
            </div>
          </div>
        </div>
        """

    # Portrait or square adaptation
    layer_tiles = "".join(
        render_layer_card(layer, "lh-stack-tile", show_badges, inline_relative=True)
        for layer in layers
    )
    return f"""
    <div class="lh-header">
      {render_brand_html(card["brand"])}
      {render_title_html(card["title"])}
      {render_subtitle_html(card["subtitle"])}
    </div>
    <div class="lh-hero-wrap">
      {render_eyebrow_html(card["eyebrow"])}
      <div class="sc-frame lh-hero-frame"{frame_style_attr}>
        <img src="{esc(hero["image"])}" alt="" style="{fit_style}">
      </div>
    </div>
    <div class="lh-stack-row">{layer_tiles}</div>
    <div class="lh-bottom-meta">
      {render_legend_html(layers)}
      {footer_html}
    </div>
    """


def build_layered_bento_body(card: dict[str, Any]) -> str:
    layers = card["layers"]
    items = card["items"]
    show_badges = card["showIndexBadges"]
    odd_span = len(items) in (3, 5)
    eyebrow_html = render_eyebrow_html(card["eyebrow"])
    footer_html = render_footer_html(card["footer"])

    tiles: list[str] = []
    for idx, item in enumerate(items):
        span_cls = " span-2" if odd_span and idx == 0 else ""
        fit_style = build_img_style(item["fit"], item["position"])
        tile_bg = build_bg_style(item.get("bg", ""))
        tile_style_attr = f' style="{tile_bg}"' if tile_bg else ""
        tiles.append(
            f'<div class="lb-tile{span_cls}"{tile_style_attr}>'
            f'<img src="{esc(item["image"])}" alt="{esc(item["label"])}" style="{fit_style}">'
            f"</div>"
        )

    if card["orientation"] == "landscape":
        layer_cards = "".join(
            render_layer_card(layer, f"lb-layer-{idx}", show_badges)
            for idx, layer in enumerate(layers)
        )
        if eyebrow_html:
            right_html = (
                f'<div class="lb-right-col">{eyebrow_html}'
                f'<div class="lb-right">{"".join(tiles)}</div></div>'
            )
        else:
            right_html = f'<div class="lb-right">{"".join(tiles)}</div>'
        return f"""
        <div class="lb-left">
          <div class="lb-header">
            {render_brand_html(card["brand"])}
            {render_title_html(card["title"])}
            {render_subtitle_html(card["subtitle"])}
          </div>
          <div class="lb-stack">{layer_cards}</div>
          <div class="lb-bottom-meta">
            {render_legend_html(layers)}
            {footer_html}
          </div>
        </div>
        {right_html}
        """

    layer_tiles = "".join(
        render_layer_card(layer, "lb-stack-tile", show_badges, inline_relative=True)
        for layer in layers
    )
    return f"""
    <div class="lb-header">
      {render_brand_html(card["brand"])}
      {eyebrow_html}
      {render_title_html(card["title"])}
      {render_subtitle_html(card["subtitle"])}
    </div>
    <div class="lb-stack-row">{layer_tiles}</div>
    {render_legend_html(layers)}
    <div class="lb-grid">{"".join(tiles)}</div>
    {footer_html}
    """


def build_section_assembly_body(card: dict[str, Any]) -> str:
    sections = card["sections"]
    showcase = card["showcase"]
    fit_style = build_img_style(showcase["fit"], showcase["position"])
    showcase_bg = build_bg_style(showcase.get("bg", ""))
    showcase_style_attr = f' style="{showcase_bg}"' if showcase_bg else ""
    tilt_cls = " is-tilted" if showcase["tilt"] else ""

    steps_html = "".join(
        f'<div class="sa-step"><span class="sc-idx">{esc(sec["index"])}</span>'
        f'<span class="sc-step-text">{esc(sec["summary"])}</span></div>'
        for sec in sections
    )

    plates_html: list[str] = []
    for sec in sections:
        plate_imgs = "".join(
            f'<div class="sa-plate" style="{build_bg_style(plate.get("bg", ""), extra=f"flex:{ratio} 1 0%;")}">'
            f'<img src="{esc(plate["image"])}" alt="{esc(sec["caption"])}" '
            f'style="{build_img_style(plate["fit"], plate["position"])}"></div>'
            for plate, ratio in zip(sec["plates"], sec["ratios"])
        )
        caption_html = (
            f'<div class="sc-section-caption"><span class="sc-idx">{esc(sec["index"])}</span>'
            f'<span>{esc(sec["caption"])}</span></div>'
            if sec["caption"]
            else ""
        )
        plates_html.append(
            f'<div class="sa-plate-group">{caption_html}'
            f'<div class="sa-plate-row" data-target-y="{sec["targetY"]}">{plate_imgs}</div>'
            f"</div>"
        )

    if card["orientation"] == "landscape":
        return f"""
        <div class="sa-left">
          <div class="sa-header">
            {render_brand_html(card["brand"])}
            {render_title_html(card["title"])}
            {render_subtitle_html(card["subtitle"])}
            <div class="sa-steps">{steps_html}</div>
          </div>
          {render_footer_html(card["footer"])}
        </div>
        <div class="sa-middle">{"".join(plates_html)}</div>
        <div class="sa-right">
          {render_eyebrow_html(card["eyebrow"])}
          <div class="sa-showcase{tilt_cls}"{showcase_style_attr}>
            <img src="{esc(showcase["image"])}" alt="" style="{fit_style}">
          </div>
        </div>
        <svg class="sc-connectors" aria-hidden="true"></svg>
        """

    return f"""
    <div class="sa-top">
      {render_brand_html(card["brand"])}
      {render_title_html(card["title"])}
      {render_subtitle_html(card["subtitle"])}
      <div class="sa-steps">{steps_html}</div>
    </div>
    <div class="sa-stage">
      <div class="sa-middle">{"".join(plates_html)}</div>
      <div class="sa-right">
        {render_eyebrow_html(card["eyebrow"])}
        <div class="sa-showcase{tilt_cls}"{showcase_style_attr}>
          <img src="{esc(showcase["image"])}" alt="" style="{fit_style}">
        </div>
      </div>
    </div>
    {render_footer_html(card["footer"])}
    <svg class="sc-connectors" aria-hidden="true"></svg>
    """


def build_module_bento_body(card: dict[str, Any]) -> str:
    items = card["items"]
    count = len(items)
    if card["orientation"] in ("portrait", "square"):
        grid_cls = "cols-2"
    elif card.get("columns") == 2:
        grid_cls = "cols-2"
    elif card.get("columns") == 3:
        grid_cls = "cols-3"
    elif count == 5:
        grid_cls = "layout-5"
    elif count in (2, 4):
        grid_cls = "cols-2"
    else:
        grid_cls = "cols-3"

    cards_html: list[str] = []
    for item in items:
        fit_style = build_img_style(item["fit"], item["position"])
        media_bg = build_bg_style(item.get("bg", ""))
        media_style_attr = f' style="{media_bg}"' if media_bg else ""
        label_parts: list[str] = []
        if item.get("index"):
            label_parts.append(f'<span class="sc-idx">{esc(item["index"])}</span>')
        if item.get("label"):
            label_parts.append(f'<span>{esc(item["label"])}</span>')
        label_html = (
            f'<div class="mb-card-label">{"".join(label_parts)}</div>'
            if label_parts
            else ""
        )
        cards_html.append(
            f'<div class="mb-card">'
            f'<div class="mb-card-media"{media_style_attr}>'
            f'<img src="{esc(item["image"])}" alt="{esc(item["label"])}" style="{fit_style}"></div>'
            f"{label_html}</div>"
        )

    return f"""
    <div class="mb-header">
      {render_brand_html(card["brand"])}
      <div class="mb-title-row">
        {render_title_html(card["title"])}
        {render_subtitle_html(card["subtitle"])}
      </div>
    </div>
    <div class="mb-grid {grid_cls}">{"".join(cards_html)}</div>
    {render_footer_html(card["footer"])}
    """


def build_gallery_wall_body(card: dict[str, Any]) -> str:
    items = card["items"]
    variant = card["variant"]

    if variant == "tilted-pages":
        cols_parts: list[str] = []
        for item in items:
            lbl = item["label"]
            lbl_html = f'<div class="gw-page-label">{esc(lbl)}</div>' if lbl else ""
            frame_bg = build_bg_style(item.get("bg", ""))
            frame_style_attr = f' style="{frame_bg}"' if frame_bg else ""
            img_style = build_img_style(item["fit"], item["position"])
            cols_parts.append(
                f'<div class="gw-page-col">{lbl_html}'
                f'<div class="gw-page-frame"{frame_style_attr}>'
                f'<img src="{esc(item["image"])}" alt="{esc(lbl)}" style="{img_style}"></div>'
                f"</div>"
            )
        cols_html = "".join(cols_parts)
        if card["orientation"] == "landscape":
            return f"""
            <div class="gw-left">
              <div class="gw-header">
                {render_brand_html(card["brand"])}
                {render_eyebrow_html(card["eyebrow"])}
                {render_title_html(card["title"])}
                {render_subtitle_html(card["subtitle"])}
              </div>
              {render_footer_html(card["footer"])}
            </div>
            <div class="gw-pages-stage">{cols_html}</div>
            """
        return f"""
        <div class="gw-header">
          {render_brand_html(card["brand"])}
          {render_eyebrow_html(card["eyebrow"])}
          {render_title_html(card["title"])}
          {render_subtitle_html(card["subtitle"])}
        </div>
        <div class="gw-pages-stage">{cols_html}</div>
        {render_footer_html(card["footer"])}
        """

    # Collage variant
    has_title = bool(card["title"])
    if card["orientation"] in ("portrait", "square"):
        if has_title:
            slots = [
                (4, 34, 46, 26),
                (52, 31, 46, 26),
                (2, 62, 46, 26),
                (50, 59, 46, 26),
                (10, 88, 44, 24),
                (56, 85, 44, 24),
            ]
        else:
            slots = [
                (-2, 2, 50, 28),
                (50, 0, 50, 28),
                (-4, 32, 50, 28),
                (48, 30, 50, 28),
                (-2, 62, 50, 28),
                (50, 60, 50, 28),
            ]
    elif has_title:
        # Matches 04-desktop-mobile-worlds.png: clean top-left editorial space, angled masonry around it
        slots = [
            (43.5, -2.5, 31.0, 36.5),
            (76.5, -5.5, 29.0, 35.5),
            (11.0, 41.0, 31.0, 36.5),
            (43.8, 35.5, 31.0, 36.5),
            (76.2, 30.5, 29.5, 36.5),
            (27.0, 77.5, 30.5, 34.0),
            (59.2, 72.0, 30.5, 34.0),
            (91.2, 67.0, 24.0, 34.0),
        ]
    else:
        # Full-bleed angled showcase wall matching 01-worlds-wall.png
        slots = [
            (-6.0, -5.0, 33.5, 36.5),
            (29.0, -7.5, 33.5, 36.5),
            (64.0, -10.0, 33.5, 36.5),
            (-2.0, 31.5, 33.5, 37.5),
            (33.0, 29.0, 33.5, 37.5),
            (68.0, 26.5, 33.5, 37.5),
            (4.0, 68.5, 33.5, 37.5),
            (39.0, 66.0, 33.5, 37.5),
            (74.0, 63.5, 33.5, 37.5),
        ]

    title_html = ""
    if has_title:
        title_width_style = (
            ' style="width:86%;top:36px;left:44px;"'
            if card["orientation"] in ("portrait", "square")
            else ""
        )
        title_html = f"""
        <div class="gw-collage-title-block"{title_width_style}>
          {render_brand_html(card["brand"])}
          {render_eyebrow_html(card["eyebrow"])}
          {render_title_html(card["title"])}
          {render_subtitle_html(card["subtitle"])}
          {render_footer_html(card["footer"])}
        </div>
        """

    slot_html: list[str] = []
    for idx, item in enumerate(items[: len(slots)]):
        left, top, width_pct, height_pct = slots[idx]
        fit_style = build_img_style(item["fit"], item["position"])
        box_style = build_bg_style(
            item.get("bg", ""),
            extra=f"left:{left}%;top:{top}%;width:{width_pct}%;height:{height_pct}%;",
        )
        slot_html.append(
            f'<div class="gw-collage-slot" style="{box_style}">'
            f'<img src="{esc(item["image"])}" alt="{esc(item["label"])}" style="{fit_style}">'
            f"</div>"
        )

    fg_html = (
        f'<img class="gw-foreground" src="{esc(card["foreground"])}" alt="">'
        if card.get("foreground")
        else ""
    )
    return title_html + "".join(slot_html) + fg_html


def build_window_callout_body(card: dict[str, Any]) -> str:
    win = card["window"]
    callouts = card["callouts"]
    is_landscape = card["orientation"] == "landscape"
    is_split = is_landscape and card.get("arrangement", "split") == "split"
    brand_html = render_brand_html(card["brand"])

    win_bg = build_bg_style(win.get("bg", ""))
    win_style_attr = f' style="{win_bg}"' if win_bg else ""
    win_img_style = build_img_style(win["fit"], win["position"])
    win_label_html = (
        f'<div class="wc-window-label">{esc(win["label"])}</div>'
        if win.get("label")
        else ""
    )

    # On portrait/square, place crisp numbered pins directly on the window to avoid tangled lines.
    pins_html = ""
    if not is_landscape:
        pins_html = "".join(
            f'<span class="wc-pin-marker" data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}" '
            f'style="left:{co["targetX"]}%;top:{co["targetY"]}%;">'
            f'{esc(co["index"])}</span>'
            for co in callouts
        )

    callout_cards: list[str] = []
    for co in callouts:
        media_bg = build_bg_style(co.get("bg", ""))
        media_style_attr = f' style="{media_bg}"' if media_bg else ""
        img_style = build_img_style(co["fit"], co["position"])
        tag_html = (
            f'<span class="wc-callout-tag">{esc(co["tag"])}</span>'
            if co.get("tag")
            else ""
        )
        size_attrs = ""
        if co.get("targetW") is not None and co.get("targetH") is not None:
            size_attrs = f' data-target-w="{co["targetW"]}" data-target-h="{co["targetH"]}"'

        if is_split:
            inline_cap = (
                f'<span class="wc-callout-inline-sep">·</span>'
                f'<span class="wc-callout-inline-cap">{esc(co["caption"])}</span>'
                if co.get("caption")
                else ""
            )
            meta_html = (
                f'<div class="wc-callout-meta">'
                f'<div class="wc-callout-head"><span class="sc-idx">{esc(co["index"])}</span>'
                f'<span class="wc-callout-title">{esc(co["title"])}</span>'
                f'{inline_cap}{tag_html}</div></div>'
            )
        else:
            caption_html = (
                f'<div class="wc-callout-caption">{esc(co["caption"])}</div>'
                if co.get("caption")
                else ""
            )
            meta_html = (
                f'<div class="wc-callout-meta">'
                f'<div class="wc-callout-head"><span class="sc-idx">{esc(co["index"])}</span>'
                f'<span class="wc-callout-title">{esc(co["title"])}</span>{tag_html}</div>'
                f"{caption_html}</div>"
            )

        callout_cards.append(
            f'<div class="wc-callout-card" data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}"{size_attrs}>'
            f'<div class="wc-callout-media"{media_style_attr}>'
            f'<img src="{esc(co["image"])}" alt="{esc(co["title"])}" style="{img_style}"></div>'
            f"{meta_html}</div>"
        )

    svg_html = '<svg class="sc-connectors" aria-hidden="true"></svg>' if is_landscape else ""

    if is_split:
        return f"""
        <div class="wc-header">
          {brand_html}
          <div class="wc-title-row">
            {render_title_html(card["title"])}
            {render_subtitle_html(card["subtitle"])}
          </div>
        </div>
        <div class="wc-split-body">
          <div class="wc-main-stage">
            <div class="wc-window-wrap">
              {win_label_html}
              <div class="wc-window-frame"{win_style_attr}>
                <img src="{esc(win["image"])}" alt="{esc(win["label"])}" style="{win_img_style}">
              </div>
            </div>
          </div>
          <div class="wc-gutter" aria-hidden="true"></div>
          <div class="wc-callouts-stack">{"".join(callout_cards)}</div>
        </div>
        {render_footer_html(card["footer"])}
        {svg_html}
        """

    cols_cls = f"cols-{len(callouts)}"
    return f"""
    <div class="wc-header">
      {brand_html}
      <div class="wc-title-row">
        {render_title_html(card["title"])}
        {render_subtitle_html(card["subtitle"])}
      </div>
    </div>
    <div class="wc-main-stage">
      <div class="wc-window-wrap">
        {win_label_html}
        <div class="wc-window-frame"{win_style_attr}>
          <img src="{esc(win["image"])}" alt="{esc(win["label"])}" style="{win_img_style}">
          {pins_html}
        </div>
      </div>
    </div>
    <div class="wc-runway"></div>
    <div class="wc-callouts-grid {cols_cls}">{"".join(callout_cards)}</div>
    {render_footer_html(card["footer"])}
    {svg_html}
    """


def build_before_after_body(card: dict[str, Any]) -> str:
    before = card["before"]
    after = card["after"]
    is_portrait = card["orientation"] == "portrait"
    brand_html = render_brand_html(card["brand"])

    def render_pane(side: dict[str, Any], is_after: bool) -> str:
        frame_bg = build_bg_style(side.get("bg", ""))
        frame_attr = f' style="{frame_bg}"' if frame_bg else ""
        img_style = build_img_style(side["fit"], side["position"])
        dot_html = '<span class="sc-dot"></span>' if is_after else ""
        caption_html = (
            f'<div class="ba-caption">{esc(side["caption"])}</div>'
            if side.get("caption")
            else ""
        )
        cls = "ba-pane is-after" if is_after else "ba-pane is-before"
        return (
            f'<div class="{cls}">'
            f'<div class="ba-label-row"><span class="ba-pill">{dot_html}<span>{esc(side["label"])}</span></span></div>'
            f'<div class="ba-frame"{frame_attr}>'
            f'<img src="{esc(side["image"])}" alt="{esc(side["label"])}" style="{img_style}"></div>'
            f"{caption_html}</div>"
        )

    if is_portrait:
        bridge_svg = (
            '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">'
            '<path d="M10 4V15.5M10 15.5L5.5 11M10 15.5L14.5 11" '
            'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
    else:
        bridge_svg = (
            '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">'
            '<path d="M4 10H15.5M15.5 10L11 5.5M15.5 10L11 14.5" '
            'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )

    note_html = (
        f'<div class="ba-note">{esc(card["compareNote"])}</div>'
        if card.get("compareNote")
        else ""
    )
    footer_html = render_footer_html(card["footer"])
    bottom_bar = (
        f'<div class="ba-bottom-bar">{footer_html}{note_html}</div>'
        if (footer_html or note_html)
        else ""
    )

    return f"""
    <div class="ba-header">
      {brand_html}
      <div class="ba-title-row">
        {render_title_html(card["title"])}
        {render_subtitle_html(card["subtitle"])}
      </div>
    </div>
    <div class="ba-stage">
      {render_pane(before, False)}
      {render_pane(after, True)}
      <div class="ba-bridge" aria-hidden="true">{bridge_svg}</div>
    </div>
    {bottom_bar}
    """


def build_workflow_steps_body(card: dict[str, Any]) -> str:
    steps = card["steps"]
    orient = card["orientation"]
    brand_html = render_brand_html(card["brand"])

    use_vertical = orient == "portrait" and (len(steps) == 3 or card.get("isTall", False))
    use_grid = (orient == "square") or (orient == "portrait" and not use_vertical)

    h_arrow = (
        '<div class="ws-arrow" aria-hidden="true">'
        '<svg width="18" height="18" viewBox="0 0 18 18" fill="none">'
        '<path d="M3 9H14.5M14.5 9L10.5 5M14.5 9L10.5 13" '
        'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
    )
    v_arrow = (
        '<div class="ws-arrow" aria-hidden="true">'
        '<svg width="18" height="18" viewBox="0 0 18 18" fill="none">'
        '<path d="M9 3V14.5M9 14.5L5 10.5M9 14.5L13 10.5" '
        'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
    )

    parts: list[str] = []
    for idx, step in enumerate(steps):
        if idx > 0 and not use_grid:
            parts.append(v_arrow if use_vertical else h_arrow)
        media_bg = build_bg_style(step.get("bg", ""))
        media_attr = f' style="{media_bg}"' if media_bg else ""
        img_style = build_img_style(step["fit"], step["position"])
        caption_html = (
            f'<div class="ws-step-caption">{esc(step["caption"])}</div>'
            if step.get("caption")
            else ""
        )
        parts.append(
            f'<div class="ws-step-card">'
            f'<div class="ws-step-media"{media_attr}>'
            f'<img src="{esc(step["image"])}" alt="{esc(step["title"])}" style="{img_style}"></div>'
            f'<div class="ws-step-body">'
            f'<div class="ws-step-head"><span class="sc-idx">{esc(step["index"])}</span>'
            f'<span class="ws-step-title">{esc(step["title"])}</span></div>'
            f"{caption_html}</div></div>"
        )

    flow_cls = "ws-flow is-grid" if use_grid else ("ws-flow is-vertical" if use_vertical else "ws-flow")
    return f"""
    <div class="ws-header">
      {brand_html}
      <div class="ws-title-row">
        {render_title_html(card["title"])}
        {render_subtitle_html(card["subtitle"])}
      </div>
    </div>
    <div class="{flow_cls}">{"".join(parts)}</div>
    {render_footer_html(card["footer"])}
    """


def build_card_html(card: dict[str, Any], fonts_css_href: str) -> str:
    layout = card["layout"]
    if layout == "layered-hero":
        body_inner = build_layered_hero_body(card)
    elif layout == "layered-bento":
        body_inner = build_layered_bento_body(card)
    elif layout == "section-assembly":
        body_inner = build_section_assembly_body(card)
    elif layout == "module-bento":
        body_inner = build_module_bento_body(card)
    elif layout == "gallery-wall":
        body_inner = build_gallery_wall_body(card)
    elif layout == "window-callout":
        body_inner = build_window_callout_body(card)
    elif layout == "before-after":
        body_inner = build_before_after_body(card)
    elif layout == "workflow-steps":
        body_inner = build_workflow_steps_body(card)
    else:
        raise ValueError(f"不支持的版式：{layout}")

    variant_cls = (
        f" variant-{card['variant']}" if layout == "gallery-wall" else ""
    )
    arrange_cls = (
        f" arrange-{card['arrangement']}" if layout == "window-callout" else ""
    )
    theme_cls = f"theme-{card['theme']}"
    display_cls = f"display-{card['displayStyle']}"
    tall_cls = " is-tall" if card.get("isTall") else ""
    extra_script = (
        CONNECTOR_SCRIPT if layout in ("section-assembly", "window-callout") else ""
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(card["id"])}</title>
  <link rel="stylesheet" href="{esc(fonts_css_href)}">
  <style>{COMMON_CSS}</style>
</head>
<body class="{esc(theme_cls)}">
  <main
    class="sc-canvas {esc(theme_cls)} {esc(display_cls)} layout-{esc(layout)} orient-{esc(card["orientation"])}{tall_cls}{variant_cls}{arrange_cls}"
    style="--canvas-w:{card["width"]}px;--canvas-h:{card["height"]}px;--accent:{esc(card["accent"])};--accent-ink:{esc(card["accentInk"])};"
  >
    {body_inner}
  </main>
  {extra_script}
</body>
</html>
"""


def build_contact_sheet_html(results: list[dict[str, Any]], spec_name: str) -> str:
    themes = [item.get("theme", "warm-paper") for item in results]
    sheet_theme = "warm-paper"
    if themes:
        counts = {t: themes.count(t) for t in SUPPORTED_THEMES}
        best = max(counts.items(), key=lambda kv: kv[1])
        if best[1] > 0:
            sheet_theme = best[0]

    palette_map = {
        "warm-paper": {
            "bg": "#f6f3ec",
            "ink": "#1b1c19",
            "sub": "#6b6e65",
            "card": "#fbfaf7",
            "thumb": "#efeae0",
            "border": "rgba(27,28,25,0.09)",
            "badge_bg": "rgba(27,28,25,0.06)",
            "badge_ink": "#4f524a",
            "dim": "#7c7f76",
            "link_line": "rgba(27,28,25,0.28)",
            "heading_font": "'Playfair Display', 'Songti SC', Georgia, serif",
        },
        "dark-studio": {
            "bg": "#101114",
            "ink": "#f3f4f6",
            "sub": "#989da8",
            "card": "#16181d",
            "thumb": "#0d0e11",
            "border": "rgba(255,255,255,0.10)",
            "badge_bg": "rgba(255,255,255,0.08)",
            "badge_ink": "#cbd1dc",
            "dim": "#878d99",
            "link_line": "rgba(255,255,255,0.28)",
            "heading_font": "'Inter', 'PingFang SC', sans-serif",
        },
        "cool-gallery": {
            "bg": "#f2f4f7",
            "ink": "#14171d",
            "sub": "#5d6470",
            "card": "#ffffff",
            "thumb": "#e9edf2",
            "border": "rgba(20,23,29,0.09)",
            "badge_bg": "rgba(20,23,29,0.06)",
            "badge_ink": "#4b525e",
            "dim": "#7a828f",
            "link_line": "rgba(20,23,29,0.28)",
            "heading_font": "'Inter', 'PingFang SC', sans-serif",
        },
    }
    p = palette_map[sheet_theme]

    cards_markup: list[str] = []
    for item in results:
        warn_html = ""
        if item["warnings"]:
            warn_items = "".join(f"<li>{esc(w)}</li>" for w in item["warnings"])
            warn_html = f'<ul class="cs-warn">{warn_items}</ul>'
        theme_badge = (
            f'<span class="cs-badge">{esc(item["theme"])}</span>'
            if item.get("theme")
            else ""
        )
        cards_markup.append(
            f"""
            <article class="cs-item">
              <a class="cs-thumb" href="png/{esc(item['id'])}.png" target="_blank" rel="noopener">
                <img src="png/{esc(item['id'])}.png" alt="{esc(item['id'])}">
              </a>
              <div class="cs-meta">
                <div>
                  <strong>{esc(item['id'])}</strong>
                  <span class="cs-badge">{esc(item['layout'])}</span>
                  {theme_badge}
                  <span class="cs-dim">{esc(item['ratio'])} · {item['pixelWidth']}×{item['pixelHeight']}</span>
                </div>
                <div class="cs-links">
                  <a href="png/{esc(item['id'])}.png" target="_blank">PNG</a>
                  <a href="html/{esc(item['id'])}.html" target="_blank">HTML</a>
                </div>
              </div>
              {warn_html}
            </article>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Showcase Cards · {esc(spec_name)}</title>
  <link rel="stylesheet" href="html/fonts.css">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: {p["bg"]};
      color: {p["ink"]};
      font-family: 'Inter', 'PingFang SC', sans-serif;
      padding: 40px 44px 64px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 28px;
      border-bottom: 1px solid {p["border"]};
      padding-bottom: 16px;
    }}
    h1 {{
      font-family: {p["heading_font"]};
      font-weight: 600;
      font-size: 30px;
      letter-spacing: -0.02em;
    }}
    .cs-sub {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      color: {p["sub"]};
    }}
    .cs-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(520px, 1fr));
      gap: 28px;
    }}
    .cs-item {{
      background: {p["card"]};
      border: 1px solid {p["border"]};
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 14px 32px -18px rgba(0,0,0,0.22);
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .cs-thumb {{
      display: block;
      border-radius: 10px;
      overflow: hidden;
      border: 1px solid {p["border"]};
      background: {p["thumb"]};
    }}
    .cs-thumb img {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .cs-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      font-size: 15px;
    }}
    .cs-badge {{
      margin-left: 6px;
      padding: 2px 9px;
      border-radius: 999px;
      background: {p["badge_bg"]};
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      color: {p["badge_ink"]};
    }}
    .cs-dim {{
      margin-left: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      color: {p["dim"]};
    }}
    .cs-links {{
      display: flex;
      gap: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
    }}
    .cs-links a {{
      color: {p["ink"]};
      text-decoration: none;
      border-bottom: 1px solid {p["link_line"]};
    }}
    .cs-warn {{
      margin-left: 18px;
      font-size: 14px;
      color: #d9485f;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Showcase Contact Sheet</h1>
    <div class="cs-sub">{esc(spec_name)} · {len(results)} cards</div>
  </header>
  <section class="cs-grid">
    {"".join(cards_markup)}
  </section>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    chrome_path = find_chrome()
    chrome_version = get_binary_version(chrome_path) if chrome_path else None
    node_path = shutil.which("node")
    node_version = get_binary_version(node_path) if node_path else None
    missing_fonts = [
        name for name in REQUIRED_FONT_FILES if not (FONTS_DIR / name).is_file()
    ]

    ok = chrome_path is not None and not missing_fonts
    payload = {
        "ok": ok,
        "platform": platform.system(),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "supported": True,
        },
        "chrome": {
            "found": chrome_path is not None,
            "path": chrome_path,
            "version": chrome_version,
        },
        "node": {
            "found": node_path is not None,
            "path": node_path,
            "version": node_version,
            "required": False,
        },
        "fonts": {
            "directory": str(FONTS_DIR),
            "ready": not missing_fonts,
            "missing": missing_fonts,
        },
    }
    if not ok:
        hints: list[str] = []
        if chrome_path is None:
            hints.append(
                "未找到 Chrome、Chromium 或 Microsoft Edge；请安装其一，或通过环境变量 CHROME_PATH 指定可执行文件路径。"
            )
        if missing_fonts:
            hints.append("缺少内置字体文件：" + ", ".join(missing_fonts))
        payload["remediation"] = hints

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_render(args: argparse.Namespace) -> int:
    if args.scale <= 0 or args.scale > 4:
        print(
            json.dumps(
                {"ok": False, "errors": [f"--scale 必须在 (0, 4] 范围内，收到 {args.scale}。"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    spec_path = Path(args.spec).expanduser().resolve()
    all_cards, errors = validate_spec(spec_path)
    if errors:
        print(
            json.dumps(
                {"ok": False, "spec": str(spec_path), "errors": errors},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    if args.only:
        cards = [c for c in all_cards if c["id"] == args.only]
        if not cards:
            available = ", ".join(c["id"] for c in all_cards) or "(无)"
            print(
                json.dumps(
                    {
                        "ok": False,
                        "spec": str(spec_path),
                        "errors": [
                            f"--only 指定的卡片 '{args.only}' 不存在；可选卡片：{available}。"
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    else:
        cards = all_cards

    chrome_path = find_chrome()
    if not chrome_path:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [
                        "未找到 Chrome、Chromium 或 Microsoft Edge；请先安装或通过 CHROME_PATH 指定路径。"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    out_dir = Path(args.out_dir).expanduser().resolve()
    html_dir = out_dir / "html"
    png_dir = out_dir / "png"
    html_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)

    fonts_css_path = html_dir / "fonts.css"
    fonts_css_path.write_text(build_inlined_fonts_css(), encoding="utf-8")

    results: list[dict[str, Any]] = []
    try:
        with ChromeSession(chrome_path) as browser:
            for card in cards:
                card_html_path = html_dir / f"{card['id']}.html"
                card_png_path = png_dir / f"{card['id']}.png"
                card_html_path.write_text(
                    build_card_html(card, "fonts.css"), encoding="utf-8"
                )
                capture_info = browser.capture_page(
                    url=card_html_path.as_uri(),
                    out_path=card_png_path,
                    width=card["width"],
                    height=card["height"],
                    scale=args.scale,
                    wait_ms=100,
                    check_card_dom=True,
                )
                results.append(
                    {
                        "id": card["id"],
                        "layout": card["layout"],
                        "theme": card["theme"],
                        "displayStyle": card["displayStyle"],
                        "ratio": card["ratio"],
                        "cssWidth": card["width"],
                        "cssHeight": card["height"],
                        "pixelWidth": capture_info["width"],
                        "pixelHeight": capture_info["height"],
                        "html": str(card_html_path),
                        "png": str(card_png_path),
                        "imageCount": capture_info["imageCount"],
                        "warnings": capture_info["warnings"],
                    }
                )
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "errors": [str(exc)], "partial": results},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    # Preserve every card in spec.json whose PNG already exists on disk (in spec order).
    rendered_by_id = {item["id"]: item for item in results}
    sheet_entries: list[dict[str, Any]] = []
    for card in all_cards:
        cid = card["id"]
        if cid in rendered_by_id:
            sheet_entries.append(rendered_by_id[cid])
            continue
        existing_png = png_dir / f"{cid}.png"
        if existing_png.is_file():
            try:
                px_w, px_h = read_png_dimensions(existing_png)
            except ValueError:
                continue
            sheet_entries.append(
                {
                    "id": cid,
                    "layout": card["layout"],
                    "theme": card["theme"],
                    "displayStyle": card["displayStyle"],
                    "ratio": card["ratio"],
                    "cssWidth": card["width"],
                    "cssHeight": card["height"],
                    "pixelWidth": px_w,
                    "pixelHeight": px_h,
                    "html": str(html_dir / f"{cid}.html"),
                    "png": str(existing_png),
                    "imageCount": 0,
                    "warnings": [],
                }
            )

    contact_sheet = out_dir / "index.html"
    contact_sheet.write_text(
        build_contact_sheet_html(sheet_entries, spec_path.name), encoding="utf-8"
    )
    summary = {
        "ok": True,
        "spec": str(spec_path),
        "outDir": str(out_dir),
        "contactSheet": str(contact_sheet),
        "contactSheetCards": len(sheet_entries),
        "scale": args.scale,
        "rendered": len(results),
        "cards": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_shoot(args: argparse.Namespace) -> int:
    html_path = Path(args.html_file).expanduser().resolve()
    if not html_path.is_file():
        print(
            json.dumps(
                {"ok": False, "errors": [f"HTML 文件不存在：{html_path}"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    try:
        width, height = parse_ratio(args.ratio)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1

    chrome_path = find_chrome()
    if not chrome_path:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [
                        "未找到 Chrome、Chromium 或 Microsoft Edge；请先安装或通过 CHROME_PATH 指定路径。"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    out_path = Path(args.out).expanduser().resolve()
    try:
        with ChromeSession(chrome_path) as browser:
            info = browser.capture_page(
                url=html_path.as_uri(),
                out_path=out_path,
                width=width,
                height=height,
                scale=args.scale,
                wait_ms=120,
                check_card_dom=True,
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "html": str(html_path),
                "out": str(out_path),
                "ratio": args.ratio,
                "cssWidth": width,
                "cssHeight": height,
                "pixelWidth": info["width"],
                "pixelHeight": info["height"],
                "scale": args.scale,
                "imageCount": info["imageCount"],
                "warnings": info["warnings"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    target = args.target.strip()
    if target.startswith(("http://", "https://", "file://")):
        url = target
    else:
        local_path = Path(target).expanduser().resolve()
        if not local_path.is_file():
            print(
                json.dumps(
                    {"ok": False, "errors": [f"待抓图的本地文件不存在：{local_path}"]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        url = local_path.as_uri()

    try:
        width, height = parse_viewport(args.viewport)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1

    chrome_path = find_chrome()
    if not chrome_path:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [
                        "未找到 Chrome、Chromium 或 Microsoft Edge；请先安装或通过 CHROME_PATH 指定路径。"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    out_path = Path(args.out).expanduser().resolve()
    try:
        with ChromeSession(chrome_path) as browser:
            info = browser.capture_page(
                url=url,
                out_path=out_path,
                width=width,
                height=height,
                scale=args.scale,
                selector=args.selector,
                full_page=args.full_page,
                wait_ms=args.wait_ms,
                check_card_dom=False,
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "target": target,
                "out": str(out_path),
                "viewport": f"{width}x{height}",
                "selector": args.selector,
                "fullPage": args.full_page,
                "scale": args.scale,
                "pixelWidth": info["width"],
                "pixelHeight": info["height"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render warm-paper editorial showcase cards to @2x PNGs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="检查 Python、本机浏览器与内置字体状态")
    p_doctor.set_defaults(func=cmd_doctor)

    p_render = sub.add_parser("render", help="按 JSON 规格渲染展示卡片与离线总览页")
    p_render.add_argument("spec", help="spec.json 文件路径")
    p_render.add_argument("--out-dir", required=True, help="输出目录")
    p_render.add_argument("--scale", type=float, default=2.0, help="设备像素比，默认 2")
    p_render.add_argument("--only", default=None, help="仅渲染指定 card id")
    p_render.set_defaults(func=cmd_render)

    p_shoot = sub.add_parser("shoot", help="将自定义 HTML 卡片导出为高清 PNG")
    p_shoot.add_argument("html_file", help="本地 HTML 文件路径")
    p_shoot.add_argument("--out", required=True, help="输出 PNG 路径")
    p_shoot.add_argument("--ratio", default="16:9", help="画幅比例或 WxH，默认 16:9")
    p_shoot.add_argument("--scale", type=float, default=2.0, help="设备像素比，默认 2")
    p_shoot.set_defaults(func=cmd_shoot)

    p_capture = sub.add_parser("capture", help="无边框截取本地 HTML 或网页高清画面")
    p_capture.add_argument("target", help="URL（http/https）或本地 HTML 文件路径")
    p_capture.add_argument("--out", required=True, help="输出 PNG 路径")
    p_capture.add_argument("--viewport", default="1440x900", help="视口尺寸 WxH，默认 1440x900")
    p_capture.add_argument("--selector", default=None, help="可选 CSS 选择器，仅截取该元素")
    p_capture.add_argument("--full-page", action="store_true", help="截取整页长图")
    p_capture.add_argument("--scale", type=float, default=2.0, help="设备像素比，默认 2")
    p_capture.add_argument("--wait-ms", type=int, default=200, help="渲染稳定等待毫秒数，默认 200")
    p_capture.set_defaults(func=cmd_capture)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
