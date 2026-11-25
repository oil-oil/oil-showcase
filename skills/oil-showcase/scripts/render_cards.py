#!/usr/bin/env python3
"""Render editorial product showcase cards (warm-paper, dark-studio, cool-gallery, wallpaper-stage, blueprint-grid) to crisp @2x PNGs.

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
import math
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
    "split-compare",
    "device-pair",
    "changelog-card",
)

SUPPORTED_THEMES = (
    "warm-paper",
    "dark-studio",
    "cool-gallery",
    "wallpaper-stage",
    "blueprint-grid",
)

SUPPORTED_STAGE_PALETTES = ("twilight", "ocean", "sunset", "emerald")
SUPPORTED_WC_MODES = ("auto", "stage", "loupe", "split", "stacked", "blueprint")
SUPPORTED_DISPLAY_STYLES = ("serif", "sans")
SUPPORTED_WC_ARRANGEMENTS = ("split", "stacked")
SUPPORTED_BA_ORIENTATIONS = ("side-by-side", "stacked")
SUPPORTED_WS_STYLES = ("row", "staged")
SUPPORTED_LANGS = ("zh", "en")

THEME_GRAIN_FREQUENCY: dict[str, str] = {
    "warm-paper": "0.88",
    "dark-studio": "0.82",
    "cool-gallery": "0.86",
    "wallpaper-stage": "0.80",
    "blueprint-grid": "0.88",
}

THEME_DEFAULT_DISPLAY: dict[str, str] = {
    "warm-paper": "serif",
    "dark-studio": "sans",
    "cool-gallery": "sans",
    "wallpaper-stage": "sans",
    "blueprint-grid": "sans",
}

THEME_DEFAULT_ACCENT: dict[str, str] = {
    "warm-paper": "#1b1c19",
    "dark-studio": "#f3f4f6",
    "cool-gallery": "#14171d",
    "wallpaper-stage": "#eb8666",
    "blueprint-grid": "#1d4ed8",
}

CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
BG_COLOR_RE = re.compile(
    r"^(?:transparent|#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|"
    r"rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\))$",
    re.IGNORECASE,
)
_POS_TOKEN = r"(?:top|bottom|left|right|center|-?(?:\d+(?:\.\d+)?|\.\d+)(?:%|px)|0)"
POSITION_RE = re.compile(rf"^{_POS_TOKEN}(?:\s+{_POS_TOKEN}){{0,3}}$", re.IGNORECASE)
CUSTOM_SIZE_RE = re.compile(r"^(\d{3,4})[xX×](\d{3,4})$")
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
VALID_FITS = ("cover", "contain")


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def is_cjk_card(card: dict[str, Any]) -> bool:
    """Detect whether a card uses CJK copy, honoring explicit lang when present."""
    explicit = str(card.get("lang") or "").strip().lower()
    if explicit == "zh":
        return True
    if explicit == "en":
        return False

    samples: list[str] = []
    for key in (
        "title",
        "subtitle",
        "eyebrow",
        "footer",
        "compareNote",
        "deltaBadge",
        "version",
        "tag",
        "stageLabel",
        "viewportLabel",
        "metaLeft",
        "metaRight",
        "gridLabel",
    ):
        val = card.get(key)
        if isinstance(val, str) and val:
            samples.append(val)

    brand = card.get("brand")
    if isinstance(brand, dict):
        for key in ("name", "tag"):
            val = brand.get(key)
            if isinstance(val, str) and val:
                samples.append(val)

    for list_key in ("layers", "items", "pages", "sections", "callouts", "steps", "highlights", "specs"):
        seq = card.get(list_key)
        if isinstance(seq, list):
            for entry in seq:
                if isinstance(entry, dict):
                    for sub_key in ("label", "title", "caption", "desc", "summary", "tag", "eyebrow", "value"):
                        val = entry.get(sub_key)
                        if isinstance(val, str) and val:
                            samples.append(val)

    for side_key in ("before", "after", "window", "desktop", "mobile", "shot"):
        side = card.get(side_key)
        if isinstance(side, dict):
            for sub_key in ("label", "caption", "meta", "tag", "metaLeft", "metaRight"):
                val = side.get(sub_key)
                if isinstance(val, str) and val:
                    samples.append(val)

    return any(has_cjk(s) for s in samples)


def validate_scale(scale: float) -> str | None:
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0 or scale > 4:
        return f"--scale 必须在 (0, 4] 范围内，收到 {scale}。"
    return None


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=f".{os.getpid()}.tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            os.chmod(tmp_path, 0o644)
        except OSError:
            pass
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


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
                  '.wc-loupe-title',
                  '.wc-loupe-desc',
                  '.wc-bp-heading',
                  '.wc-bp-text',
                  '.ba-caption',
                  '.sp-caption',
                  '.cl-release-title',
                  '.cl-hl-title',
                  '.cl-hl-desc',
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
            atomic_write_bytes(out_path, png_bytes)
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


def resolve_asset_path(
    raw_path: str,
    base_dir: Path,
    field_label: str,
    errors: list[str],
    local_paths: set[Path] | None = None,
) -> str:
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
    if local_paths is not None:
        local_paths.add(candidate)
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
    elif not POSITION_RE.fullmatch(position):
        errors.append(
            f"{field_label}.position 无效（{position!r}）；仅允许 CSS object-position 关键字（top/bottom/left/right/center）、百分比或 px 数值。"
        )
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

    default_stage_palette = str(data.get("stagePalette") or "twilight").strip().lower()
    if default_stage_palette not in SUPPORTED_STAGE_PALETTES:
        errors.append(
            f"顶层 stagePalette 无效（{default_stage_palette!r}）；可选值：{', '.join(SUPPORTED_STAGE_PALETTES)}。"
        )
        default_stage_palette = "twilight"

    default_display_raw = data.get("displayStyle")
    default_display: str | None = None
    if default_display_raw is not None:
        default_display = str(default_display_raw).strip()
        if default_display not in SUPPORTED_DISPLAY_STYLES:
            errors.append(
                f"顶层 displayStyle 无效（{default_display!r}）；可选值：{', '.join(SUPPORTED_DISPLAY_STYLES)}。"
            )
            default_display = None

    default_lang_raw = data.get("lang")
    default_lang: str | None = None
    if default_lang_raw is not None:
        default_lang = str(default_lang_raw).strip().lower()
        if default_lang not in SUPPORTED_LANGS:
            errors.append(
                f"顶层 lang 无效（{default_lang!r}）；可选值：{', '.join(SUPPORTED_LANGS)}。"
            )
            default_lang = None

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
        card_local_paths: set[Path] = set()

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

        stage_palette = str(raw.get("stagePalette") or default_stage_palette).strip().lower()
        if stage_palette not in SUPPORTED_STAGE_PALETTES:
            errors.append(
                f"{label}.stagePalette 无效（{stage_palette!r}）；可选值：{', '.join(SUPPORTED_STAGE_PALETTES)}。"
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

        explicit_lang: str | None = default_lang
        if raw.get("lang") is not None:
            card_lang_raw = str(raw.get("lang")).strip().lower()
            if card_lang_raw not in SUPPORTED_LANGS:
                errors.append(
                    f"{label}.lang 无效（{card_lang_raw!r}）；可选值：{', '.join(SUPPORTED_LANGS)}。"
                )
                continue
            explicit_lang = card_lang_raw

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
                        brand_raw["icon"],
                        base_dir,
                        f"{label}.brand.icon",
                        errors,
                        local_paths=card_local_paths,
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

        lang_probe = dict(raw)
        lang_probe["brand"] = brand_obj
        if explicit_lang is not None:
            lang_probe["lang"] = explicit_lang
        else:
            lang_probe.pop("lang", None)
        card_is_cjk = is_cjk_card(lang_probe)

        card_tilt_raw = raw.get("tilt")
        explicit_tilt: bool | None = None
        if card_tilt_raw is not None:
            if not isinstance(card_tilt_raw, bool):
                errors.append(f"{label}.tilt 必须是布尔值 true 或 false。")
            else:
                explicit_tilt = card_tilt_raw

        card: dict[str, Any] = {
            "id": card_id,
            "layout": layout,
            "theme": theme,
            "stagePalette": stage_palette,
            "gridLabel": str(raw.get("gridLabel", "")).strip(),
            "displayStyle": display_style,
            "lang": "zh" if card_is_cjk else "en",
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
            "tilt": explicit_tilt if explicit_tilt is not None else False,
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
                        local_paths=card_local_paths,
                    )
                    is_transparent = bool(layer.get("transparent", False))
                    fit, position, bg = normalize_image_options(
                        layer,
                        f"{label}.layers[{li}]",
                        errors,
                        default_fit="cover",
                        default_position="center bottom" if is_transparent else "top center",
                    )
                    default_layer_label = f"图层 {li + 1}" if card_is_cjk else f"Layer {li + 1}"
                    layers.append(
                        {
                            "index": f"{li + 1:02d}",
                            "label": str(layer.get("label") or default_layer_label).strip(),
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
                    raw_hero.get("image", ""),
                    base_dir,
                    f"{label}.hero.image",
                    errors,
                    local_paths=card_local_paths,
                )
                fit, position, bg = normalize_image_options(
                    raw_hero,
                    f"{label}.hero",
                    errors,
                    default_fit="cover",
                    default_position="top center",
                )
                hero_tilt = (
                    bool(raw_hero.get("tilt"))
                    if "tilt" in raw_hero
                    else (explicit_tilt if explicit_tilt is not None else True)
                )
                card["hero"] = {
                    "image": uri,
                    "tilt": hero_tilt,
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
                        local_paths=card_local_paths,
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
                resolve_asset_path(
                    fg,
                    base_dir,
                    f"{label}.foreground",
                    errors,
                    local_paths=card_local_paths,
                )
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
                                        local_paths=card_local_paths,
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
                                        local_paths=card_local_paths,
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
                            local_paths=card_local_paths,
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
                    local_paths=card_local_paths,
                )
                fit, position, bg = normalize_image_options(
                    raw_showcase,
                    f"{label}.showcase",
                    errors,
                    default_fit="cover",
                    default_position="top center",
                )
                showcase_tilt = (
                    bool(raw_showcase.get("tilt"))
                    if "tilt" in raw_showcase
                    else (explicit_tilt if explicit_tilt is not None else False)
                )
                card["showcase"] = {
                    "image": uri,
                    "tilt": showcase_tilt,
                    "fit": fit,
                    "position": position,
                    "bg": bg,
                }

        if layout == "window-callout":
            raw_mode = str(raw.get("mode") or "auto").strip().lower()
            if raw_mode not in SUPPORTED_WC_MODES:
                errors.append(
                    f"{label}.mode 无效（{raw_mode!r}）；可选值：{', '.join(SUPPORTED_WC_MODES)}。"
                )
                raw_mode = "auto"

            arrangement = str(raw.get("arrangement") or "split").strip()
            if arrangement not in SUPPORTED_WC_ARRANGEMENTS:
                errors.append(
                    f"{label}.arrangement 无效（{arrangement!r}）；可选值：{', '.join(SUPPORTED_WC_ARRANGEMENTS)}。"
                )
                arrangement = "split"
            if raw_mode in ("split", "stacked"):
                arrangement = raw_mode
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
                    local_paths=card_local_paths,
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
            if raw_callouts is None:
                raw_callouts = []
            if not isinstance(raw_callouts, list) or len(raw_callouts) > 4:
                errors.append(f"{label}.callouts 必须是包含 0 到 4 个项的数组。")
                raw_callouts = []

            if raw_mode == "stage" or (raw_mode == "auto" and len(raw_callouts) == 0):
                wc_mode = "stage"
            elif raw_mode == "loupe" or (raw_mode == "auto" and len(raw_callouts) == 1):
                wc_mode = "loupe"
                if len(raw_callouts) != 1:
                    errors.append(f"{label}.callouts 在 loupe 模式下必须恰好包含 1 个聚焦放大项。")
            elif raw_mode == "blueprint" or (
                raw_mode == "auto" and theme == "blueprint-grid" and len(raw_callouts) >= 2
            ):
                wc_mode = "blueprint"
                if len(raw_callouts) < 1:
                    errors.append(f"{label}.callouts 在 blueprint 模式下至少需要 1 个标注项。")
            else:
                wc_mode = "standard"
                if len(raw_callouts) < 2:
                    errors.append(f"{label}.callouts 在 {arrangement} 模式下必须包含 2 到 4 个局部标注卡。")

            card["wcMode"] = wc_mode
            card["stageLabel"] = str(raw.get("stageLabel", "")).strip()
            card["viewportLabel"] = str(raw.get("viewportLabel", "")).strip()

            card_specs: list[dict[str, str]] = []
            raw_card_specs = raw.get("specs")
            if raw_card_specs is not None:
                if not isinstance(raw_card_specs, list) or len(raw_card_specs) > 5:
                    errors.append(f"{label}.specs 必须是包含最多 5 项的数组。")
                else:
                    for spi, sp in enumerate(raw_card_specs):
                        if not isinstance(sp, dict):
                            errors.append(f"{label}.specs[{spi}] 必须是包含 label 和 value 的对象。")
                            continue
                        sp_lbl = str(sp.get("label", "")).strip()
                        sp_val = str(sp.get("value", "")).strip()
                        if sp_lbl or sp_val:
                            card_specs.append({"label": sp_lbl, "value": sp_val})
            card["specs"] = card_specs

            callouts: list[dict[str, Any]] = []
            count = len(raw_callouts)
            for ci, co in enumerate(raw_callouts):
                if not isinstance(co, dict):
                    errors.append(f"{label}.callouts[{ci}] 必须是对象。")
                    continue
                img_raw = co.get("image", "")
                if img_raw:
                    uri = resolve_asset_path(
                        img_raw,
                        base_dir,
                        f"{label}.callouts[{ci}].image",
                        errors,
                        local_paths=card_local_paths,
                    )
                elif wc_mode == "blueprint":
                    uri = ""
                else:
                    uri = resolve_asset_path(
                        img_raw,
                        base_dir,
                        f"{label}.callouts[{ci}].image",
                        errors,
                        local_paths=card_local_paths,
                    )
                fit, position, bg = normalize_image_options(
                    co,
                    f"{label}.callouts[{ci}]",
                    errors,
                    default_fit="cover",
                    default_position="center",
                )
                default_x = round(18 + (64 * ci / max(1, count - 1)), 1) if count > 1 else 36.0
                target_x = co.get("targetX", default_x)
                target_y = co.get("targetY", 62.0)
                if not isinstance(target_x, (int, float)) or isinstance(target_x, bool) or not (0 <= target_x <= 100):
                    errors.append(f"{label}.callouts[{ci}].targetX 必须在 0 到 100 之间。")
                    target_x = default_x
                if not isinstance(target_y, (int, float)) or isinstance(target_y, bool) or not (0 <= target_y <= 100):
                    errors.append(f"{label}.callouts[{ci}].targetY 必须在 0 到 100 之间。")
                    target_y = 62.0
                target_w = co.get("targetW")
                target_h = co.get("targetH")
                if target_w is not None and (
                    isinstance(target_w, bool) or not isinstance(target_w, (int, float)) or not (1 <= target_w <= 100)
                ):
                    errors.append(f"{label}.callouts[{ci}].targetW 必须在 1 到 100 之间。")
                    target_w = None
                if target_h is not None and (
                    isinstance(target_h, bool) or not isinstance(target_h, (int, float)) or not (1 <= target_h <= 100)
                ):
                    errors.append(f"{label}.callouts[{ci}].targetH 必须在 1 到 100 之间。")
                    target_h = None
                co_title = str(co.get("title") or co.get("label") or "").strip()
                if not co_title:
                    errors.append(f"{label}.callouts[{ci}].title 不能为空。")
                co_specs: list[dict[str, str]] = []
                raw_co_specs = co.get("specs")
                if raw_co_specs is not None:
                    if not isinstance(raw_co_specs, list) or len(raw_co_specs) > 4:
                        errors.append(f"{label}.callouts[{ci}].specs 必须是包含最多 4 项的数组。")
                    else:
                        for spi, sp in enumerate(raw_co_specs):
                            if not isinstance(sp, dict):
                                errors.append(f"{label}.callouts[{ci}].specs[{spi}] 必须是对象。")
                                continue
                            sp_lbl = str(sp.get("label", "")).strip()
                            sp_val = str(sp.get("value", "")).strip()
                            if sp_lbl or sp_val:
                                co_specs.append({"label": sp_lbl, "value": sp_val})
                callouts.append(
                    {
                        "index": str(co.get("index") or f"{ci + 1:02d}").strip(),
                        "eyebrow": str(co.get("eyebrow", "")).strip(),
                        "tag": str(co.get("tag", "")).strip(),
                        "title": co_title,
                        "caption": str(co.get("desc") or co.get("caption") or "").strip(),
                        "image": uri,
                        "targetX": float(target_x),
                        "targetY": float(target_y),
                        "targetW": float(target_w) if target_w is not None else None,
                        "targetH": float(target_h) if target_h is not None else None,
                        "specs": co_specs,
                        "fit": fit,
                        "position": position,
                        "bg": bg,
                    }
                )
            card["callouts"] = callouts

        if layout == "before-after":
            raw_ba_orient = raw.get("orientation")
            if raw_ba_orient is not None:
                ba_orient = str(raw_ba_orient).strip().lower()
                if ba_orient not in SUPPORTED_BA_ORIENTATIONS:
                    errors.append(
                        f"{label}.orientation 无效（{ba_orient!r}）；可选值：{', '.join(SUPPORTED_BA_ORIENTATIONS)}。"
                    )
                    ba_orient = "stacked" if card["orientation"] == "portrait" else "side-by-side"
            else:
                ba_orient = "stacked" if card["orientation"] == "portrait" else "side-by-side"
            card["compareOrientation"] = ba_orient
            card["deltaBadge"] = str(raw.get("deltaBadge", "")).strip()

            ba_defaults = (
                (("before", "对比前"), ("after", "对比后"))
                if card_is_cjk
                else (("before", "BEFORE"), ("after", "AFTER"))
            )
            for side_key, default_lbl in ba_defaults:
                raw_side = raw.get(side_key)
                if not isinstance(raw_side, dict):
                    errors.append(f"{label}.{side_key} 必须是包含 image 的对象。")
                else:
                    uri = resolve_asset_path(
                        raw_side.get("image", ""),
                        base_dir,
                        f"{label}.{side_key}.image",
                        errors,
                        local_paths=card_local_paths,
                    )
                    fit, position, bg = normalize_image_options(
                        raw_side,
                        f"{label}.{side_key}",
                        errors,
                        default_fit="cover",
                        default_position="center",
                    )
                    width_pct: float | None = None
                    if raw_side.get("widthPercent") is not None:
                        raw_wp = raw_side.get("widthPercent")
                        if (
                            isinstance(raw_wp, bool)
                            or not isinstance(raw_wp, (int, float))
                            or not math.isfinite(raw_wp)
                            or not (35 <= float(raw_wp) <= 100)
                        ):
                            errors.append(
                                f"{label}.{side_key}.widthPercent 必须在 35 到 100 之间，收到 {raw_wp!r}。"
                            )
                        else:
                            width_pct = float(raw_wp)
                    card[side_key] = {
                        "label": str(raw_side.get("label") or default_lbl).strip(),
                        "meta": str(raw_side.get("meta") or raw_side.get("tag") or "").strip(),
                        "image": uri,
                        "caption": str(raw_side.get("caption", "")).strip(),
                        "fit": fit,
                        "position": position,
                        "bg": bg,
                        "widthPercent": width_pct,
                    }
            card["compareNote"] = str(raw.get("compareNote", "")).strip()

        if layout == "workflow-steps":
            ws_style = str(raw.get("style") or "row").strip().lower()
            if ws_style not in SUPPORTED_WS_STYLES:
                errors.append(
                    f"{label}.style 无效（{ws_style!r}）；可选值：{', '.join(SUPPORTED_WS_STYLES)}。"
                )
                ws_style = "row"
            card["wsStyle"] = ws_style
            card["tilt"] = (
                explicit_tilt if explicit_tilt is not None else (ws_style == "staged")
            )
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
                        local_paths=card_local_paths,
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

        if layout == "split-compare":
            raw_split_pct = raw.get("splitPercent", 50)
            if (
                isinstance(raw_split_pct, bool)
                or not isinstance(raw_split_pct, (int, float))
                or not math.isfinite(raw_split_pct)
                or not (15 <= float(raw_split_pct) <= 85)
            ):
                errors.append(
                    f"{label}.splitPercent 必须在 15 到 85 之间，收到 {raw_split_pct!r}。"
                )
                split_pct = 50.0
            else:
                split_pct = float(raw_split_pct)
            card["splitPercent"] = split_pct

            sp_defaults = (
                (("before", "对比前"), ("after", "对比后"))
                if card_is_cjk
                else (("before", "BEFORE"), ("after", "AFTER"))
            )
            for side_key, default_lbl in sp_defaults:
                raw_side = raw.get(side_key)
                if not isinstance(raw_side, dict):
                    errors.append(f"{label}.{side_key} 必须是包含 image 的对象。")
                else:
                    uri = resolve_asset_path(
                        raw_side.get("image", ""),
                        base_dir,
                        f"{label}.{side_key}.image",
                        errors,
                        local_paths=card_local_paths,
                    )
                    fit, position, bg = normalize_image_options(
                        raw_side,
                        f"{label}.{side_key}",
                        errors,
                        default_fit="cover",
                        default_position="top center",
                    )
                    card[side_key] = {
                        "label": str(raw_side.get("label") or default_lbl).strip(),
                        "caption": str(raw_side.get("caption", "")).strip(),
                        "image": uri,
                        "fit": fit,
                        "position": position,
                        "bg": bg,
                    }

        if layout == "device-pair":
            dp_defaults = (
                ("desktop", "WEB"),
                ("mobile", "iOS"),
            )
            for dev_key, default_lbl in dp_defaults:
                raw_dev = raw.get(dev_key)
                if not isinstance(raw_dev, dict):
                    errors.append(f"{label}.{dev_key} 必须是包含 image 的对象。")
                else:
                    uri = resolve_asset_path(
                        raw_dev.get("image", ""),
                        base_dir,
                        f"{label}.{dev_key}.image",
                        errors,
                        local_paths=card_local_paths,
                    )
                    fit, position, bg = normalize_image_options(
                        raw_dev,
                        f"{label}.{dev_key}",
                        errors,
                        default_fit="cover",
                        default_position="top center",
                    )
                    safe_top_bg = ""
                    if dev_key == "mobile" and raw_dev.get("safeTopBg"):
                        raw_stb = str(raw_dev.get("safeTopBg")).strip()
                        if not BG_COLOR_RE.fullmatch(raw_stb):
                            errors.append(
                                f"{label}.mobile.safeTopBg 无效（{raw_stb!r}）；请使用合法十六进制或 rgb/rgba 颜色。"
                            )
                        else:
                            safe_top_bg = raw_stb
                    card[dev_key] = {
                        "label": str(raw_dev.get("label") or default_lbl).strip(),
                        "image": uri,
                        "fit": fit,
                        "position": position,
                        "bg": bg,
                        "safeTopBg": safe_top_bg,
                    }

        if layout == "changelog-card":
            version = str(raw.get("version", "")).strip()
            if not version:
                errors.append(f"{label}.version 不能为空（例如 \"v0.4\"）。")
            card["version"] = version

            raw_highlights = raw.get("highlights")
            if not isinstance(raw_highlights, list) or not (2 <= len(raw_highlights) <= 4):
                errors.append(f"{label}.highlights 必须包含 2 到 4 个版本亮点项。")
            else:
                highlights: list[dict[str, str]] = []
                for hi, hl in enumerate(raw_highlights):
                    if not isinstance(hl, dict):
                        errors.append(f"{label}.highlights[{hi}] 必须是对象。")
                        continue
                    hl_title = str(hl.get("title") or hl.get("label") or "").strip()
                    if not hl_title:
                        errors.append(f"{label}.highlights[{hi}].title 不能为空。")
                    highlights.append(
                        {
                            "index": str(hl.get("index") or f"{hi + 1:02d}").strip(),
                            "title": hl_title,
                            "desc": str(hl.get("desc") or hl.get("caption") or "").strip(),
                        }
                    )
                card["highlights"] = highlights

            raw_shot = raw.get("shot")
            raw_img = raw.get("image")
            if isinstance(raw_shot, dict):
                shot_src = raw_shot.get("image", "")
                shot_opts_obj = raw_shot
            elif isinstance(raw_img, dict):
                shot_src = raw_img.get("image", "")
                shot_opts_obj = raw_img
            else:
                shot_src = raw_img or ""
                shot_opts_obj = raw
            shot_uri = resolve_asset_path(
                shot_src,
                base_dir,
                f"{label}.image",
                errors,
                local_paths=card_local_paths,
            )
            shot_fit, shot_pos, shot_bg = normalize_image_options(
                shot_opts_obj,
                f"{label}.image",
                errors,
                default_fit="cover",
                default_position="center",
            )
            default_cl_tag = "版本更新" if card_is_cjk else "RELEASE"
            cl_tag = str(
                raw.get("tag")
                or (raw_shot.get("tag") if isinstance(raw_shot, dict) else "")
                or default_cl_tag
            ).strip()
            meta_left = str(
                raw.get("metaLeft")
                or (raw_shot.get("metaLeft") if isinstance(raw_shot, dict) else "")
                or ""
            ).strip()
            meta_right = str(
                raw.get("metaRight")
                or (raw_shot.get("metaRight") if isinstance(raw_shot, dict) else "")
                or ""
            ).strip()
            card["shot"] = {
                "image": shot_uri,
                "fit": shot_fit,
                "position": shot_pos,
                "bg": shot_bg,
                "tag": cl_tag,
                "metaLeft": meta_left,
                "metaRight": meta_right,
            }

        card["_inputPaths"] = sorted(str(p) for p in card_local_paths)
        normalized.append(card)

    return normalized, errors


# ---------------------------------------------------------------------------
# Editorial HTML/CSS compilation for all 8 archetypes & 3 themes
# ---------------------------------------------------------------------------


COMMON_CSS = """
:root {
  --paper-base: #f5f1e8;
  --paper-bg:
    radial-gradient(ellipse 58% 46% at 10% -4%, rgba(255, 253, 248, 0.94) 0%, rgba(255, 252, 245, 0.34) 44%, rgba(245, 241, 232, 0) 74%),
    linear-gradient(142deg, #faf7f0 0%, #f4efe4 48%, #eae3d5 100%);
  --key-light-bg: linear-gradient(
    126deg,
    rgba(255, 253, 248, 0.72) 0%,
    rgba(255, 250, 240, 0.22) 24%,
    rgba(255, 255, 255, 0) 48%
  );
  --glow-strength: 10%;
  --glow-secondary: 6%;
  --grain-opacity: 0.036;
  --grain-blend: multiply;
  --ink: #1b1c19;
  --muted: #4a4d46;
  --meta: #5e6159;
  --sep: #8c8f86;
  --eyebrow-ink: #43463e;
  --body-ink: #272924;
  --caption-ink: #474a42;
  --frame-bg: #ffffff;
  --surface-bg: #fbfaf7;
  --hairline: rgba(27, 28, 25, 0.10);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.28) 0%, rgba(255, 255, 255, 0.06) 100%);
  --glass-border: rgba(255, 255, 255, 0.68);
  --glass-inner-ring: rgba(27, 28, 25, 0.08);
  --shadow-card:
    14px 28px 62px -12px rgba(24, 22, 18, 0.20),
    5px 10px 22px -6px rgba(24, 22, 18, 0.11),
    0 0 0 1px rgba(27, 28, 25, 0.09);
  --shadow-plate:
    10px 20px 44px -10px rgba(24, 22, 18, 0.22),
    4px 8px 16px -4px rgba(24, 22, 18, 0.12),
    0 0 0 1px rgba(27, 28, 25, 0.09);
  --shadow-hero:
    22px 42px 86px -14px rgba(24, 22, 18, 0.26),
    8px 16px 32px -6px rgba(24, 22, 18, 0.14),
    0 0 0 1px rgba(27, 28, 25, 0.11);
  --rim-top: rgba(255, 255, 255, 0.72);
  --rim-left: rgba(255, 255, 255, 0.36);
  --rim-bottom: rgba(27, 28, 25, 0.14);
  --sheen-grad: linear-gradient(
    126deg,
    rgba(255, 255, 255, 0.13) 0%,
    rgba(255, 255, 255, 0.03) 24%,
    rgba(255, 255, 255, 0) 48%
  );
  --occluded-ink: rgba(27, 28, 25, 0.22);
  --occluded-accent-mix: 36%;
  --connector-opacity: 0.78;
  --halo-ring: rgba(255, 255, 255, 0.92);
  --font-serif: 'Playfair Display', 'Songti SC', 'Noto Serif SC', 'Noto Serif CJK SC', 'STSong', Georgia, serif;
  --font-sans: 'Inter', 'PingFang SC', 'Noto Sans SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}

.theme-dark-studio {
  --paper-base: #0c0e11;
  --paper-bg:
    radial-gradient(ellipse 52% 46% at 10% -6%, rgba(212, 228, 255, 0.105) 0%, rgba(140, 166, 206, 0.03) 42%, rgba(12, 14, 17, 0) 70%),
    linear-gradient(142deg, #11151b 0%, #0c0e11 48%, #090c10 100%);
  --key-light-bg: linear-gradient(
    126deg,
    rgba(255, 255, 255, 0.072) 0%,
    rgba(222, 236, 255, 0.022) 22%,
    rgba(255, 255, 255, 0) 46%
  );
  --glow-strength: 18%;
  --glow-secondary: 13%;
  --grain-opacity: 0.038;
  --grain-blend: overlay;
  --ink: #f5f7fa;
  --muted: #98a2b2;
  --meta: #7e8a99;
  --sep: #4b5563;
  --eyebrow-ink: #c7cdd8;
  --body-ink: #dfe3eb;
  --caption-ink: #adb6c4;
  --frame-bg: #17191e;
  --surface-bg: #15181f;
  --hairline: rgba(255, 255, 255, 0.12);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.10) 0%, rgba(255, 255, 255, 0.03) 100%);
  --glass-border: rgba(255, 255, 255, 0.22);
  --glass-inner-ring: rgba(255, 255, 255, 0.08);
  --shadow-card:
    22px 42px 88px rgba(0, 0, 0, 0.80),
    8px 16px 32px rgba(0, 0, 0, 0.58),
    2px 4px 10px rgba(0, 0, 0, 0.44),
    0 0 0 1px rgba(255, 255, 255, 0.14);
  --shadow-plate:
    18px 32px 68px rgba(0, 0, 0, 0.78),
    7px 13px 26px rgba(0, 0, 0, 0.54),
    0 0 0 1px rgba(255, 255, 255, 0.14);
  --shadow-hero:
    26px 48px 102px rgba(0, 0, 0, 0.86),
    10px 20px 38px rgba(0, 0, 0, 0.64),
    0 0 0 1px rgba(255, 255, 255, 0.17);
  --rim-top: rgba(255, 255, 255, 0.28);
  --rim-left: rgba(255, 255, 255, 0.13);
  --rim-bottom: rgba(0, 0, 0, 0.54);
  --sheen-grad: linear-gradient(
    125deg,
    rgba(255, 255, 255, 0.085) 0%,
    rgba(255, 255, 255, 0.02) 25%,
    rgba(255, 255, 255, 0) 48%
  );
  --occluded-ink: rgba(255, 255, 255, 0.24);
  --occluded-accent-mix: 40%;
  --connector-opacity: 0.90;
  --halo-ring: rgba(12, 14, 17, 0.90);
}

.theme-cool-gallery {
  --paper-base: #eef1f5;
  --paper-bg:
    radial-gradient(ellipse 58% 46% at 10% -4%, rgba(255, 255, 255, 0.94) 0%, rgba(255, 255, 255, 0.36) 44%, rgba(238, 241, 245, 0) 74%),
    linear-gradient(142deg, #f5f7fa 0%, #eef1f5 46%, #e4e9f1 100%);
  --key-light-bg: linear-gradient(
    126deg,
    rgba(255, 255, 255, 0.74) 0%,
    rgba(255, 255, 255, 0.24) 24%,
    rgba(255, 255, 255, 0) 48%
  );
  --glow-strength: 14%;
  --glow-secondary: 9%;
  --grain-opacity: 0.032;
  --grain-blend: multiply;
  --ink: #0b1220;
  --muted: #526075;
  --meta: #64748b;
  --sep: #94a3b8;
  --eyebrow-ink: #3e4552;
  --body-ink: #1e293b;
  --caption-ink: #475569;
  --frame-bg: #ffffff;
  --surface-bg: #f8fafc;
  --hairline: rgba(15, 23, 42, 0.11);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.32) 0%, rgba(255, 255, 255, 0.08) 100%);
  --glass-border: rgba(255, 255, 255, 0.74);
  --glass-inner-ring: rgba(15, 23, 42, 0.08);
  --shadow-card:
    18px 34px 72px rgba(15, 23, 42, 0.22),
    6px 14px 28px rgba(15, 23, 42, 0.13),
    0 0 0 1px rgba(15, 23, 42, 0.14);
  --shadow-plate:
    14px 24px 52px rgba(15, 23, 42, 0.20),
    5px 10px 20px rgba(15, 23, 42, 0.11),
    0 0 0 1px rgba(15, 23, 42, 0.13);
  --shadow-hero:
    26px 46px 96px rgba(15, 23, 42, 0.30),
    10px 18px 36px rgba(15, 23, 42, 0.18),
    0 0 0 1px color-mix(in srgb, var(--accent) 28%, rgba(15, 23, 42, 0.18));
  --rim-top: rgba(255, 255, 255, 0.46);
  --rim-left: rgba(255, 255, 255, 0.22);
  --rim-bottom: rgba(15, 23, 42, 0.20);
  --sheen-grad: linear-gradient(
    126deg,
    rgba(255, 255, 255, 0.11) 0%,
    rgba(255, 255, 255, 0.025) 24%,
    rgba(255, 255, 255, 0) 48%
  );
  --occluded-ink: rgba(15, 23, 42, 0.22);
  --occluded-accent-mix: 36%;
  --connector-opacity: 0.80;
  --halo-ring: rgba(255, 255, 255, 0.92);
}

.theme-wallpaper-stage {
  --paper-base: #131126;
  --paper-bg:
    radial-gradient(ellipse 52% 46% at 10% -6%, rgba(255, 228, 240, 0.22) 0%, rgba(167, 139, 250, 0.08) 40%, transparent 70%),
    radial-gradient(ellipse 58% 64% at 82% 72%, rgba(235, 134, 102, 0.52) 0%, rgba(196, 84, 114, 0.28) 38%, transparent 72%),
    radial-gradient(ellipse 68% 66% at 24% 38%, rgba(96, 42, 128, 0.64) 0%, rgba(62, 28, 96, 0.28) 46%, transparent 76%),
    radial-gradient(ellipse 64% 68% at 76% 14%, rgba(44, 58, 136, 0.68) 0%, rgba(28, 36, 88, 0.26) 46%, transparent 74%),
    radial-gradient(ellipse 55% 58% at 16% 88%, rgba(36, 46, 112, 0.72) 0%, transparent 72%),
    linear-gradient(138deg, #16112a 0%, #111329 48%, #1e1326 100%);
  --key-light-bg:
    radial-gradient(ellipse 40% 34% at 52% 86%, rgba(244, 152, 114, 0.24) 0%, transparent 72%),
    linear-gradient(124deg, rgba(255, 255, 255, 0.09) 0%, rgba(255, 255, 255, 0.02) 24%, transparent 48%);
  --glow-strength: 18%;
  --glow-secondary: 12%;
  --grain-opacity: 0.052;
  --grain-blend: overlay;
  --ink: #ffffff;
  --muted: rgba(244, 240, 252, 0.76);
  --meta: rgba(244, 240, 252, 0.68);
  --sep: rgba(255, 255, 255, 0.36);
  --eyebrow-ink: rgba(255, 255, 255, 0.80);
  --body-ink: rgba(250, 248, 255, 0.90);
  --caption-ink: rgba(244, 240, 252, 0.76);
  --frame-bg: #0e0f11;
  --surface-bg: rgba(20, 18, 36, 0.84);
  --hairline: rgba(255, 255, 255, 0.16);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.22) 0%, rgba(255, 255, 255, 0.06) 52%, rgba(255, 210, 196, 0.12) 100%);
  --glass-border: rgba(255, 255, 255, 0.28);
  --glass-inner-ring: rgba(0, 0, 0, 0.42);
  --shadow-card:
    0 32px 84px -14px rgba(6, 4, 18, 0.72),
    0 14px 32px -8px rgba(6, 4, 18, 0.52),
    0 0 0 1px rgba(255, 255, 255, 0.20);
  --shadow-plate:
    0 24px 62px -12px rgba(6, 4, 18, 0.68),
    0 10px 26px -6px rgba(6, 4, 18, 0.48),
    0 0 0 1px rgba(255, 255, 255, 0.20);
  --shadow-hero:
    0 42px 104px -14px rgba(6, 4, 18, 0.76),
    0 18px 40px -8px rgba(6, 4, 18, 0.56),
    0 0 92px -12px rgba(235, 134, 102, 0.30),
    0 0 0 1px rgba(255, 255, 255, 0.24);
  --rim-top: rgba(255, 255, 255, 0.44);
  --rim-left: rgba(255, 255, 255, 0.20);
  --rim-bottom: rgba(0, 0, 0, 0.48);
  --sheen-grad: linear-gradient(
    126deg,
    rgba(255, 255, 255, 0.07) 0%,
    rgba(255, 255, 255, 0.015) 24%,
    transparent 46%
  );
  --occluded-ink: rgba(255, 244, 240, 0.14);
  --occluded-accent-mix: 34%;
  --connector-opacity: 0.90;
  --halo-ring: rgba(16, 14, 34, 0.90);
}

.theme-wallpaper-stage.stage-palette-ocean {
  --paper-base: #0a1829;
  --paper-bg:
    radial-gradient(ellipse 52% 46% at 10% -6%, rgba(216, 244, 255, 0.20) 0%, rgba(56, 189, 248, 0.08) 40%, transparent 70%),
    radial-gradient(ellipse 58% 64% at 82% 72%, rgba(56, 189, 248, 0.44) 0%, rgba(14, 116, 144, 0.26) 38%, transparent 72%),
    radial-gradient(ellipse 68% 66% at 24% 38%, rgba(20, 74, 128, 0.68) 0%, rgba(12, 48, 92, 0.28) 46%, transparent 76%),
    radial-gradient(ellipse 64% 68% at 76% 14%, rgba(30, 64, 138, 0.64) 0%, rgba(15, 36, 86, 0.26) 46%, transparent 74%),
    radial-gradient(ellipse 55% 58% at 16% 88%, rgba(15, 58, 108, 0.72) 0%, transparent 72%),
    linear-gradient(138deg, #0b1b32 0%, #091628 48%, #0c2236 100%);
  --key-light-bg:
    radial-gradient(ellipse 40% 34% at 52% 86%, rgba(56, 189, 248, 0.20) 0%, transparent 72%),
    linear-gradient(124deg, rgba(255, 255, 255, 0.09) 0%, rgba(255, 255, 255, 0.02) 24%, transparent 48%);
}

.theme-wallpaper-stage.stage-palette-sunset {
  --paper-base: #22111c;
  --paper-bg:
    radial-gradient(ellipse 52% 46% at 10% -6%, rgba(255, 236, 214, 0.22) 0%, rgba(251, 146, 60, 0.08) 40%, transparent 70%),
    radial-gradient(ellipse 58% 64% at 82% 72%, rgba(249, 115, 22, 0.48) 0%, rgba(217, 70, 96, 0.30) 38%, transparent 72%),
    radial-gradient(ellipse 68% 66% at 24% 38%, rgba(136, 38, 86, 0.64) 0%, rgba(84, 24, 62, 0.28) 46%, transparent 76%),
    radial-gradient(ellipse 64% 68% at 76% 14%, rgba(92, 34, 88, 0.62) 0%, rgba(52, 20, 56, 0.26) 46%, transparent 74%),
    radial-gradient(ellipse 55% 58% at 16% 88%, rgba(68, 22, 58, 0.72) 0%, transparent 72%),
    linear-gradient(138deg, #241020 0%, #1c101f 48%, #2e161d 100%);
  --key-light-bg:
    radial-gradient(ellipse 40% 34% at 52% 86%, rgba(251, 146, 60, 0.24) 0%, transparent 72%),
    linear-gradient(124deg, rgba(255, 255, 255, 0.09) 0%, rgba(255, 255, 255, 0.02) 24%, transparent 48%);
}

.theme-wallpaper-stage.stage-palette-emerald {
  --paper-base: #0b1b19;
  --paper-bg:
    radial-gradient(ellipse 52% 46% at 10% -6%, rgba(220, 252, 238, 0.18) 0%, rgba(52, 211, 153, 0.08) 40%, transparent 70%),
    radial-gradient(ellipse 58% 64% at 82% 72%, rgba(16, 185, 129, 0.44) 0%, rgba(13, 148, 136, 0.24) 38%, transparent 72%),
    radial-gradient(ellipse 68% 66% at 24% 38%, rgba(18, 78, 70, 0.66) 0%, rgba(12, 52, 48, 0.28) 46%, transparent 76%),
    radial-gradient(ellipse 64% 68% at 76% 14%, rgba(22, 68, 86, 0.62) 0%, rgba(14, 42, 56, 0.26) 46%, transparent 74%),
    radial-gradient(ellipse 55% 58% at 16% 88%, rgba(14, 56, 52, 0.72) 0%, transparent 72%),
    linear-gradient(138deg, #0b1d1b 0%, #091719 48%, #102420 100%);
  --key-light-bg:
    radial-gradient(ellipse 40% 34% at 52% 86%, rgba(52, 211, 153, 0.20) 0%, transparent 72%),
    linear-gradient(124deg, rgba(255, 255, 255, 0.09) 0%, rgba(255, 255, 255, 0.02) 24%, transparent 48%);
}

.theme-wallpaper-stage .sc-title {
  text-shadow: 0 2px 24px rgba(10, 8, 24, 0.35);
}

.theme-wallpaper-stage .sc-ambient-bounce {
  opacity: 0.45;
}

.theme-blueprint-grid {
  --paper-base: #f3f5f8;
  --paper-bg:
    radial-gradient(ellipse 56% 48% at 10% -4%, rgba(255, 255, 255, 0.85) 0%, rgba(243, 245, 248, 0) 68%),
    #f3f5f8;
  --key-light-bg: linear-gradient(
    126deg,
    rgba(255, 255, 255, 0.48) 0%,
    rgba(255, 255, 255, 0.14) 24%,
    rgba(255, 255, 255, 0) 48%
  );
  --glow-strength: 8%;
  --glow-secondary: 4%;
  --grain-opacity: 0.030;
  --grain-blend: multiply;
  --ink: #0b1220;
  --muted: #475569;
  --meta: #64748b;
  --sep: #94a3b8;
  --eyebrow-ink: #475569;
  --body-ink: #1e293b;
  --caption-ink: #475569;
  --frame-bg: #ffffff;
  --surface-bg: #ffffff;
  --hairline: rgba(15, 23, 42, 0.18);
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.34) 0%, rgba(255, 255, 255, 0.08) 100%);
  --glass-border: rgba(255, 255, 255, 0.82);
  --glass-inner-ring: rgba(15, 23, 42, 0.12);
  --shadow-card:
    14px 26px 56px -10px rgba(15, 23, 42, 0.15),
    4px 10px 22px -4px rgba(15, 23, 42, 0.09),
    0 0 0 1px rgba(15, 23, 42, 0.18);
  --shadow-plate:
    10px 20px 42px -10px rgba(15, 23, 42, 0.14),
    3px 8px 16px -4px rgba(15, 23, 42, 0.08),
    0 0 0 1px rgba(15, 23, 42, 0.18);
  --shadow-hero:
    16px 30px 64px -10px rgba(15, 23, 42, 0.16),
    4px 10px 22px -4px rgba(15, 23, 42, 0.09),
    0 0 0 1px rgba(15, 23, 42, 0.18);
  --rim-top: rgba(255, 255, 255, 0.90);
  --rim-left: rgba(255, 255, 255, 0.55);
  --rim-bottom: rgba(15, 23, 42, 0.14);
  --sheen-grad: linear-gradient(
    126deg,
    rgba(255, 255, 255, 0.08) 0%,
    rgba(255, 255, 255, 0.02) 24%,
    transparent 46%
  );
  --occluded-ink: rgba(15, 23, 42, 0.14);
  --occluded-accent-mix: 22%;
  --connector-opacity: 0.92;
  --halo-ring: rgba(255, 255, 255, 0.96);
}

/* Blueprint architectural drafting sheet overlays */
.bp-grid-bg {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, rgba(15, 23, 42, 0.046) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(15, 23, 42, 0.046) 1px, transparent 1px),
    linear-gradient(to right, rgba(15, 23, 42, 0.075) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(15, 23, 42, 0.075) 1px, transparent 1px);
  background-size: 40px 40px, 40px 40px, 160px 160px, 160px 160px;
  pointer-events: none;
  z-index: 0;
}

.bp-drafting-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
}

.bp-margin-top {
  position: absolute;
  right: 52px;
  top: 44px;
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--meta);
  pointer-events: none;
  z-index: 6;
}

.bp-axis-x {
  position: absolute;
  bottom: 12px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--meta);
  letter-spacing: 0.04em;
  pointer-events: none;
  z-index: 6;
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
  text-rendering: geometricPrecision;
}

.sc-canvas {
  position: relative;
  width: var(--canvas-w);
  height: var(--canvas-h);
  overflow: hidden;
  background: var(--paper-bg);
  color: var(--ink);
}

/* Pure-code studio key light, ambient accent bounce & fine matte surface grain */
.sc-studio-keylight {
  position: absolute;
  inset: -120px;
  background: var(--key-light-bg);
  pointer-events: none;
  z-index: 0;
}

.sc-ambient-bounce {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 46% 58% at 72% 62%, color-mix(in srgb, var(--accent) var(--glow-strength), transparent) 0%, color-mix(in srgb, var(--accent) 4%, transparent) 46%, transparent 74%),
    radial-gradient(ellipse 38% 52% at 28% 66%, color-mix(in srgb, var(--accent) var(--glow-secondary), transparent) 0%, transparent 72%);
  pointer-events: none;
  z-index: 1;
}

.layout-window-callout .sc-ambient-bounce {
  background:
    radial-gradient(ellipse 48% 62% at 34% 62%, color-mix(in srgb, var(--accent) var(--glow-strength), transparent) 0%, color-mix(in srgb, var(--accent) 4%, transparent) 46%, transparent 74%),
    radial-gradient(ellipse 38% 56% at 84% 52%, color-mix(in srgb, var(--accent) var(--glow-secondary), transparent) 0%, transparent 72%);
}

.layout-workflow-steps.ws-style-staged .sc-ambient-bounce {
  background:
    radial-gradient(ellipse 56% 74% at 84% 48%, color-mix(in srgb, var(--accent) var(--glow-strength), transparent) 0%, color-mix(in srgb, var(--accent) 5%, transparent) 44%, transparent 74%),
    radial-gradient(ellipse 40% 48% at 54% 78%, color-mix(in srgb, var(--accent) var(--glow-secondary), transparent) 0%, transparent 70%);
}

.sc-grain {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: var(--grain-opacity);
  mix-blend-mode: var(--grain-blend);
  pointer-events: none;
  z-index: 25;
}

.sc-canvas > :not(.sc-studio-keylight):not(.sc-ambient-bounce):not(.sc-grain):not(.sc-connectors):not(.bp-grid-bg):not(.bp-drafting-svg):not(.bp-margin-top):not(.bp-axis-x):not(.sp-bg-shift):not(.sp-seam-ray):not(.gw-collage-title-block):not(.gw-collage-slot):not(.gw-foreground):not(.ws-staged-left):not(.ws-staged-arena):not(.dp-sculp-tag):not(.dp-desktop-frame):not(.dp-mobile-wrap):not(.dp-footer-meta):not(.cl-left-col):not(.cl-sculp-tag):not(.cl-shot-frame):not(.cl-shot-caption):not(.wc-stage-sculp):not(.wc-loupe-sculp):not(.wc-loupe-card):not(.wc-bp-sculp):not(.wc-bp-viewport-label):not(.wc-bp-meta) {
  position: relative;
  z-index: 2;
}

/* Optical top-left specular rim highlight & subtle oblique glass sheen on elevated frames */
.sc-frame::after,
.wc-window-frame::after,
.wc-callout-frame::after,
.wc-loupe-card::after,
.wc-bp-specimen::after,
.ba-frame::after,
.sp-frame::after,
.dp-desktop-frame::after,
.cl-shot-frame::after,
.ws-step-card::after,
.ws-stage-plane::after,
.mb-card::after,
.lb-tile::after,
.sa-plate::after,
.sa-showcase::after,
.gw-page-frame::after,
.gw-collage-slot::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--sheen-grad);
  box-shadow:
    inset 0 1.2px 0 var(--rim-top),
    inset 1px 0 0 var(--rim-left),
    inset 0 -1px 0 var(--rim-bottom);
  pointer-events: none;
  z-index: 5;
}

/* Behind-the-card occluded sculptural typography (replaces all bordered pills) */
.sc-occluded-header {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  padding: 0 12px 0 10px;
  margin-bottom: -14px;
  z-index: 1;
  pointer-events: none;
  flex: none;
}

.sc-occluded-header.is-compact-occlude {
  margin-bottom: -10px;
}

.sc-occluded-label {
  font-family: var(--font-sans);
  font-size: 76px;
  font-weight: 600;
  line-height: 0.80;
  letter-spacing: -0.058em;
  text-transform: uppercase;
  font-variant-numeric: tabular-nums;
  color: var(--occluded-ink);
  white-space: nowrap;
  user-select: none;
}

.sc-occluded-label.is-cjk {
  font-family: var(--font-sans);
  font-size: 56px;
  font-weight: 700;
  line-height: 0.90;
  letter-spacing: 0.02em;
  text-transform: none;
}

.display-serif .sc-occluded-label.is-cjk {
  font-family: var(--font-serif);
  font-weight: 600;
}

.sc-occluded-label.is-medium {
  font-size: 56px;
  letter-spacing: -0.044em;
}

.sc-occluded-label.is-medium.is-cjk {
  font-size: 46px;
  letter-spacing: 0.015em;
}

.sc-occluded-label.is-long {
  font-size: 42px;
  letter-spacing: -0.032em;
}

.sc-occluded-label.is-long.is-cjk {
  font-size: 38px;
  letter-spacing: 0.01em;
}

.sc-occluded-label.is-accent {
  color: color-mix(in srgb, var(--accent) var(--occluded-accent-mix), transparent);
  text-shadow: 0 0 36px color-mix(in srgb, var(--accent) 26%, transparent);
}

.sc-occluded-meta {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--meta);
  padding-bottom: 20px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sc-occluded-meta.is-accent {
  color: var(--accent);
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
  border-radius: 7px;
  object-fit: contain;
  display: block;
}

.sc-brand-dot {
  display: none;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--accent);
  box-shadow: 0 0 10px color-mix(in srgb, var(--accent) 45%, transparent);
  flex: none;
}

.theme-blueprint-grid .sc-brand-dot {
  border-radius: 2px;
  box-shadow: none;
}

.layout-split-compare .sc-brand,
.layout-device-pair .sc-brand,
.layout-changelog-card .sc-brand,
.layout-window-callout.wc-mode-stage .sc-brand,
.layout-window-callout.wc-mode-loupe .sc-brand,
.layout-window-callout.wc-mode-blueprint .sc-brand {
  gap: 10px;
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--eyebrow-ink);
}

.layout-split-compare .sc-brand-dot,
.layout-device-pair .sc-brand-dot,
.layout-changelog-card .sc-brand-dot,
.layout-window-callout.wc-mode-stage .sc-brand-dot,
.layout-window-callout.wc-mode-loupe .sc-brand-dot,
.layout-window-callout.wc-mode-blueprint .sc-brand-dot {
  display: inline-block;
}

.layout-split-compare .sc-brand-icon,
.layout-device-pair .sc-brand-icon,
.layout-changelog-card .sc-brand-icon,
.layout-window-callout.wc-mode-stage .sc-brand-icon,
.layout-window-callout.wc-mode-loupe .sc-brand-icon,
.layout-window-callout.wc-mode-blueprint .sc-brand-icon {
  width: 22px;
  height: 22px;
  border-radius: 5px;
}

.layout-split-compare .sc-brand-name,
.layout-device-pair .sc-brand-name,
.layout-changelog-card .sc-brand-name,
.layout-window-callout.wc-mode-stage .sc-brand-name,
.layout-window-callout.wc-mode-loupe .sc-brand-name,
.layout-window-callout.wc-mode-blueprint .sc-brand-name {
  font-weight: 500;
  letter-spacing: 0.08em;
  color: var(--eyebrow-ink);
}

.layout-split-compare .sc-brand-tag,
.layout-device-pair .sc-brand-tag,
.layout-changelog-card .sc-brand-tag,
.layout-window-callout.wc-mode-stage .sc-brand-tag,
.layout-window-callout.wc-mode-loupe .sc-brand-tag,
.layout-window-callout.wc-mode-blueprint .sc-brand-tag {
  font-size: 14px;
  letter-spacing: 0.08em;
  color: var(--eyebrow-ink);
}

.theme-dark-studio .sc-brand-icon {
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.55);
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
  letter-spacing: -0.036em;
  line-height: 1.06;
}

.display-sans .sc-title.is-cjk {
  font-weight: 600;
  letter-spacing: -0.018em;
  line-height: 1.18;
}

.sc-subtitle {
  font-family: var(--font-sans);
  font-size: 19px;
  font-weight: 400;
  line-height: 1.46;
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
  font-size: 14px;
  line-height: 1.45;
  color: var(--meta);
  letter-spacing: 0.04em;
  position: relative;
  z-index: 4;
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

.sc-layer-index {
  position: absolute;
  top: 10px;
  left: 12px;
  z-index: 4;
  font-family: var(--font-mono);
  font-size: 15px;
  font-weight: 600;
  color: var(--accent);
  text-shadow: 0 1px 10px var(--halo-ring);
  pointer-events: none;
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
  position: relative;
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
  z-index: 11;
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
  gap: 24px;
  padding: 44px 36px 36px 16px;
  overflow: hidden;
  z-index: 2;
}

.gw-page-col {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.layout-gallery-wall.is-tilted .gw-page-col {
  flex: 0 0 280px;
  transform: rotate(-4deg);
  transform-origin: top left;
}

.layout-gallery-wall.is-tilted .gw-page-col:nth-child(2) {
  margin-top: 14px;
}

.layout-gallery-wall.is-tilted .gw-page-col:nth-child(3) {
  margin-top: 28px;
}

.layout-gallery-wall.is-tilted .gw-page-col:nth-child(4) {
  margin-top: 42px;
}

.gw-page-label {
  font-family: var(--font-mono);
  font-size: 15.5px;
  color: var(--caption-ink);
  padding-left: 4px;
}

.gw-page-frame {
  position: relative;
  width: 100%;
  height: 740px;
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
}

.layout-gallery-wall.variant-tilted-pages.is-tilted.orient-portrait .gw-page-col,
.layout-gallery-wall.variant-tilted-pages.is-tilted.orient-square .gw-page-col {
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
  z-index: 4;
}

.layout-gallery-wall.is-tilted .gw-collage-slot {
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
  padding: 34px 52px 30px;
  gap: 16px;
}

.layout-window-callout.arrange-split.orient-landscape {
  padding: 40px 56px 32px;
  gap: 0;
}

.wc-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  flex: none;
  position: relative;
  z-index: 6;
}

.wc-title-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
}

.layout-window-callout .sc-title {
  font-size: 46px;
  line-height: 1.05;
}

.layout-window-callout .sc-title.is-cjk {
  font-size: 40px;
}

.layout-window-callout .sc-subtitle {
  font-size: 17.5px;
  max-width: 740px;
  line-height: 1.44;
}

/* Default split arrangement on landscape (16:9 / 4:3): left header+window + full-height right callout stack */
.wc-split-body {
  flex: 1 1 0;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.96fr) minmax(72px, 0.24fr) minmax(0, 0.98fr);
  align-items: stretch;
  position: relative;
  z-index: 4;
}

.wc-split-left {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  gap: 20px;
}

.wc-split-left .wc-main-stage {
  flex: 1 1 0;
  width: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
}

.wc-split-left .wc-window-wrap {
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  margin: 0;
}

.wc-gutter {
  pointer-events: none;
}

.wc-callouts-stack {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 30px;
  min-height: 0;
  height: 100%;
  padding-top: 10px;
  position: relative;
  z-index: 8;
}

.wc-callouts-stack.count-4 {
  gap: 16px;
  padding-top: 2px;
}

.wc-callout-unit {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1 1 0;
  max-height: 258px;
}

.wc-callouts-stack.count-2 .wc-callout-unit {
  max-height: 356px;
}

.wc-callouts-stack.count-4 .wc-callout-unit {
  max-height: 194px;
}

.wc-callout-behind {
  padding: 0 8px 0 6px;
  margin-bottom: -12px;
}

.wc-callout-num {
  font-size: 70px;
  line-height: 0.78;
  letter-spacing: -0.06em;
}

.wc-callouts-stack.count-4 .wc-callout-num {
  font-size: 56px;
}

.wc-callouts-stack.count-4 .wc-callout-behind {
  margin-bottom: -9px;
}

.wc-callout-meta-top {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  text-align: right;
  padding-bottom: 18px;
  min-width: 0;
  gap: 2px;
}

.wc-callouts-stack.count-4 .wc-callout-meta-top {
  padding-bottom: 14px;
}

.wc-callout-sub {
  font-family: var(--font-mono);
  font-size: 14px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--meta);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.wc-callout-frame {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  border-radius: 14px;
  background: var(--surface-bg);
  box-shadow: var(--shadow-plate);
  display: flex;
  flex-direction: column;
  z-index: 3;
}

.wc-callout-frame-inner {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  border-radius: 14px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.wc-target-dot {
  position: absolute;
  left: -4px;
  top: 50%;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  transform: translateY(-50%);
  background: var(--accent);
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--accent) 26%, transparent),
    0 0 12px color-mix(in srgb, var(--accent) 85%, transparent);
  z-index: 12;
  pointer-events: none;
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
  position: relative;
  z-index: 4;
}

.wc-window-wrap {
  position: relative;
  height: 100%;
  width: 84%;
  max-width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wc-window-label {
  font-family: var(--font-mono);
  font-size: 14px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--meta);
  flex: none;
}

.wc-window-frame {
  position: relative;
  flex: 1;
  min-height: 0;
  border-radius: 15px;
  overflow: hidden;
  background: var(--frame-bg);
  box-shadow: var(--shadow-hero);
}

.wc-window-frame.is-tilted {
  transform: perspective(1800px) rotateY(4deg) rotateX(1.2deg) rotateZ(-0.5deg);
  transform-origin: 45% 50%;
}

.wc-window-frame img {
  width: 100%;
  height: 100%;
  display: block;
}

/* Focus reticles live inside the window plane for zero-drift 1:1 coordinate lock */
.wc-reticle {
  position: absolute;
  border: 1.5px solid color-mix(in srgb, var(--accent) 82%, transparent);
  border-radius: 7px;
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  box-shadow:
    0 0 0 1px var(--halo-ring),
    0 0 22px color-mix(in srgb, var(--accent) 22%, transparent),
    inset 0 0 12px color-mix(in srgb, var(--accent) 10%, transparent);
  pointer-events: none;
  z-index: 4;
}

.wc-pinpoint {
  position: absolute;
  width: 14px;
  height: 14px;
  margin-left: -7px;
  margin-top: -7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 24%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 4;
}

.wc-anchor-dot {
  position: absolute;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--accent);
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--accent) 28%, transparent),
    0 0 12px color-mix(in srgb, var(--accent) 90%, transparent);
  pointer-events: none;
}

.wc-pinpoint .wc-anchor-dot {
  position: static;
}

.wc-runway {
  height: 30px;
  flex: none;
}

.wc-callouts-grid {
  flex: 0.92 1 0;
  min-height: 0;
  display: grid;
  gap: 20px;
  position: relative;
  z-index: 6;
}

.wc-callouts-grid.cols-2 { grid-template-columns: repeat(2, 1fr); }
.wc-callouts-grid.cols-3 { grid-template-columns: repeat(3, 1fr); }
.wc-callouts-grid.cols-4 { grid-template-columns: repeat(4, 1fr); }

.wc-callouts-grid .wc-callout-unit {
  max-height: none;
}

.wc-callouts-grid .wc-target-dot {
  left: 50%;
  top: -4px;
  transform: translateX(-50%);
}

.wc-callout-card {
  position: relative;
  overflow: hidden;
  border-radius: 14px;
  background: var(--surface-bg);
  box-shadow: var(--shadow-plate);
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1 1 0;
  z-index: 3;
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
  padding: 12px 16px 13px;
  border-top: 1px solid var(--hairline);
  background: var(--surface-bg);
  display: flex;
  flex-direction: column;
  gap: 4px;
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
  line-height: 1.24;
  letter-spacing: -0.015em;
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
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.40);
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 8;
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
  padding: 16px 22px;
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

.wc-row-num {
  font-family: var(--font-sans);
  font-size: 30px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.05em;
  color: color-mix(in srgb, var(--accent) 68%, transparent);
  margin-bottom: 2px;
}

/* ---------- 7. before-after ---------- */
.layout-before-after {
  display: flex;
  flex-direction: column;
  padding: 40px 58px 34px;
  gap: 18px;
}

.ba-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  flex: none;
  position: relative;
  z-index: 6;
}

.ba-title-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
}

.layout-before-after .sc-title {
  font-size: 48px;
  line-height: 1.05;
}

.layout-before-after .sc-title.is-cjk {
  font-size: 42px;
}

.layout-before-after .sc-subtitle {
  font-size: 17.5px;
  max-width: 760px;
}

/* Asymmetric upright side-by-side: recessed BEFORE vs dominant forward-stepping AFTER */
.ba-stage {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 0.84fr) 76px minmax(0, 1.08fr);
  align-items: stretch;
  z-index: 4;
}

.ba-pane {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
}

.layout-before-after.ba-orient-side-by-side .ba-pane.is-before {
  padding-top: 62px;
  z-index: 5;
}

.layout-before-after.ba-orient-side-by-side .ba-pane.is-after {
  padding-top: 0;
  z-index: 7;
}

.layout-before-after.ba-orient-side-by-side.is-tilted .ba-pane.is-before {
  transform: perspective(2200px) rotateY(3.2deg) rotateX(1.1deg) rotateZ(-1.4deg) scale(0.96);
  transform-origin: 50% 54%;
}

.layout-before-after.ba-orient-side-by-side.is-tilted .ba-pane.is-after {
  transform: perspective(2200px) rotateY(-2.6deg) rotateX(0.9deg) rotateZ(0.8deg) scale(1.02);
  transform-origin: 50% 52%;
}

.ba-frame {
  flex: 1 1 0;
  min-height: 0;
  position: relative;
  overflow: hidden;
  border-radius: 17px;
  background: var(--frame-bg);
  box-shadow: var(--shadow-card);
  z-index: 3;
}

.ba-pane.is-after .ba-frame {
  box-shadow:
    var(--shadow-hero),
    0 24px 62px color-mix(in srgb, var(--accent) 22%, transparent),
    0 0 0 1px color-mix(in srgb, var(--accent) 34%, transparent);
}

.ba-pane.is-after .ba-frame::after {
  box-shadow:
    inset 0 1.5px 0 color-mix(in srgb, var(--accent) 52%, #ffffff),
    inset 1.2px 0 0 color-mix(in srgb, var(--accent) 24%, var(--rim-left)),
    inset 0 -1px 0 var(--rim-bottom);
}

.ba-frame img {
  width: 100%;
  height: 100%;
  display: block;
}

.ba-caption {
  font-size: 15.5px;
  line-height: 1.44;
  color: var(--muted);
  margin-top: 12px;
  flex: none;
}

/* Minimal floating directional light arrow — zero circle, zero pill */
.ba-arrow-bridge {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  align-self: center;
  margin-top: 32px;
  color: var(--accent);
  z-index: 9;
  pointer-events: none;
}

.ba-arrow-bridge svg {
  filter: drop-shadow(0 0 12px color-mix(in srgb, var(--accent) 36%, transparent));
}

.ba-delta-inline {
  font-family: var(--font-mono);
  font-size: 14.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--accent);
  white-space: nowrap;
  text-shadow: 0 0 22px color-mix(in srgb, var(--accent) 30%, transparent);
}

/* Stacked before-after (timelines, waveforms & portrait) */
.layout-before-after.ba-orient-stacked .ba-stage {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: center;
  gap: 0;
}

.ba-stacked-plane {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.layout-before-after.ba-orient-stacked.orient-landscape.is-tilted .ba-stacked-plane {
  transform: perspective(2350px) rotateX(2.8deg) rotateY(-3.0deg) rotateZ(-1.0deg);
  transform-origin: 48% 52%;
}

.layout-before-after.ba-orient-stacked .ba-pane {
  flex: 1 1 0;
  min-height: 0;
}

.ba-divider-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  flex: none;
  margin: -4px 0;
  color: var(--accent);
}

.ba-divider-row::before,
.ba-divider-row::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--hairline);
}

.ba-after-track {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  align-items: stretch;
  gap: 0;
}

.ba-after-track .ba-frame {
  flex: none;
}

/* Optical trim hairline & monumental borderless delta display in reclaimed right-hand void */
.ba-reclaimed-zone {
  position: relative;
  flex: 1 1 0;
  min-width: 0;
  margin-left: 14px;
  padding: 18px 24px 18px 28px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 6px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--accent) 18%, transparent) 0%,
    color-mix(in srgb, var(--accent) 6%, transparent) 44%,
    transparent 96%
  );
  border-radius: 0 16px 16px 0;
}

.ba-trim-hairline {
  position: absolute;
  left: 0;
  top: 6%;
  bottom: 6%;
  width: 2px;
  background: linear-gradient(
    180deg,
    transparent 0%,
    color-mix(in srgb, var(--accent) 95%, #ffffff) 20%,
    var(--accent) 50%,
    color-mix(in srgb, var(--accent) 95%, #ffffff) 80%,
    transparent 100%
  );
  box-shadow:
    0 0 18px color-mix(in srgb, var(--accent) 85%, transparent),
    0 0 6px color-mix(in srgb, var(--accent) 90%, #ffffff);
}

.ba-plane-hairline {
  position: absolute;
  top: 46%;
  height: 62px;
  width: 1.5px;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--accent) 16%, transparent) 0%,
    color-mix(in srgb, var(--accent) 84%, transparent) 50%,
    color-mix(in srgb, var(--accent) 26%, transparent) 100%
  );
  box-shadow: 0 0 12px color-mix(in srgb, var(--accent) 52%, transparent);
  pointer-events: none;
  z-index: 2;
}

.ba-delta-hero {
  font-family: var(--font-sans);
  font-size: 46px;
  font-weight: 600;
  line-height: 0.94;
  letter-spacing: -0.045em;
  font-variant-numeric: tabular-nums;
  color: var(--accent);
  text-shadow: 0 0 34px color-mix(in srgb, var(--accent) 34%, transparent);
}

.ba-delta-sub {
  font-family: var(--font-mono);
  font-size: 14.5px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--caption-ink);
}

.ba-bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex: none;
  position: relative;
  z-index: 6;
}

.ba-note {
  font-family: var(--font-mono);
  font-size: 14px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--meta);
}

.layout-before-after.orient-portrait {
  padding: 44px 48px 38px;
  gap: 20px;
}

.layout-before-after.orient-portrait .ba-title-row,
.layout-before-after.orient-square .ba-title-row {
  flex-direction: column;
  gap: 8px;
}

.layout-before-after.orient-portrait.ba-orient-stacked .ba-stacked-plane {
  gap: 22px;
}

/* Uniform padded stage when fit: "contain" is used on media slots */
.ws-step-media.is-contain,
.mb-card-media.is-contain,
.ba-frame.is-contain,
.lb-tile.is-contain {
  padding: 18px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
}

.wc-callout-media.is-contain {
  padding: 12px 14px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ws-step-media.is-contain img,
.mb-card-media.is-contain img,
.ba-frame.is-contain img,
.lb-tile.is-contain img {
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  border-radius: 10px;
  box-shadow:
    0 12px 26px -10px rgba(0, 0, 0, 0.24),
    0 2px 6px rgba(0, 0, 0, 0.10);
}

.wc-callout-media.is-contain img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.theme-dark-studio .ws-step-media.is-contain img,
.theme-dark-studio .mb-card-media.is-contain img,
.theme-dark-studio .ba-frame.is-contain img,
.theme-dark-studio .lb-tile.is-contain img {
  box-shadow:
    0 14px 30px -10px rgba(0, 0, 0, 0.58),
    0 0 0 1px rgba(255, 255, 255, 0.08);
}

/* ---------- 8. workflow-steps ---------- */
.layout-workflow-steps {
  display: flex;
  flex-direction: column;
  padding: 32px 44px 30px;
  gap: 18px;
}

.ws-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  flex: none;
  position: relative;
  z-index: 6;
}

.ws-title-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.layout-workflow-steps .sc-title {
  font-size: 46px;
  line-height: 1.08;
}

.layout-workflow-steps .sc-title.is-cjk {
  font-size: 42px;
}

.layout-workflow-steps .sc-subtitle {
  font-size: 18.5px;
  max-width: 780px;
}

.ws-flow {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  align-items: stretch;
  gap: 0;
  position: relative;
  z-index: 4;
}

.ws-step-unit {
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}

.ws-step-behind {
  padding: 0 8px 0 8px;
  margin-bottom: -11px;
}

.ws-step-num {
  font-size: 62px;
  line-height: 0.78;
  letter-spacing: -0.06em;
}

.ws-step-card {
  position: relative;
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--surface-bg);
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  z-index: 3;
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
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.28;
  letter-spacing: -0.015em;
}

.ws-step-caption {
  font-size: 15px;
  line-height: 1.44;
  color: var(--muted);
}

.ws-arrow {
  width: 34px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  padding-top: 34px;
  color: var(--accent);
  opacity: 0.78;
}

/* Staged workflow-steps (asymmetric editorial left column + floating 3D planes + occluded numerals) */
.layout-workflow-steps.ws-style-staged.orient-landscape {
  padding: 0;
  gap: 0;
  display: block;
}

.ws-staged-left {
  position: absolute;
  left: 60px;
  top: 56px;
  bottom: 52px;
  width: 420px;
  display: flex;
  flex-direction: column;
  z-index: 15;
}

.ws-staged-left .sc-title {
  margin-top: 36px;
  font-size: 52px;
  line-height: 1.04;
}

.ws-staged-left .sc-title.is-cjk {
  font-size: 46px;
}

.ws-staged-left .sc-subtitle {
  margin-top: 16px;
  font-size: 17px;
  line-height: 1.52;
  max-width: 396px;
}

.ws-milestones {
  margin-top: 36px;
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--hairline);
}

.ws-milestone {
  display: grid;
  grid-template-columns: 38px 1fr;
  align-items: start;
  gap: 8px;
  padding: 16px 0;
  border-bottom: 1px solid var(--hairline);
}

.ws-milestone-idx {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  color: var(--accent);
  padding-top: 2px;
}

.ws-milestone-title {
  font-size: 16px;
  font-weight: 500;
  color: var(--ink);
  line-height: 1.28;
}

.ws-milestone-caption {
  margin-top: 4px;
  font-size: 14.5px;
  color: var(--muted);
  line-height: 1.42;
}

.ws-staged-left .sc-footer {
  margin-top: auto;
}

.ws-staged-arena {
  position: absolute;
  inset: 0;
  z-index: 4;
}

.ws-stage-num {
  position: absolute;
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 98px;
  line-height: 0.80;
  letter-spacing: -0.065em;
  font-variant-numeric: tabular-nums;
  user-select: none;
  pointer-events: none;
}

.ws-stage-plane {
  position: absolute;
  border-radius: 17px;
  overflow: hidden;
  background: var(--frame-bg);
  box-shadow: var(--shadow-hero);
}

.ws-stage-plane img {
  width: 100%;
  height: 100%;
  display: block;
}

.ws-stage-hero {
  left: 59%;
  top: 9%;
  width: 45%;
  height: 98%;
  border-radius: 22px;
  z-index: 4;
}

.ws-stage-num-hero {
  left: 63.5%;
  top: 2.5%;
  color: var(--occluded-ink);
  z-index: 3;
}

.ws-stage-fg-0 {
  left: 32.5%;
  top: 11.5%;
  width: 30.5%;
  height: 45%;
  z-index: 7;
}

.ws-stage-num-0 {
  left: 33%;
  top: 4.5%;
  color: var(--occluded-ink);
  z-index: 6;
}

.ws-stage-fg-1 {
  left: 35.5%;
  top: 63%;
  width: 34.5%;
  height: 29%;
  z-index: 10;
}

.ws-stage-num-1 {
  left: 36%;
  top: 56%;
  color: color-mix(in srgb, var(--accent) var(--occluded-accent-mix), transparent);
  text-shadow: 0 0 36px color-mix(in srgb, var(--accent) 28%, transparent);
  z-index: 9;
}

.ws-stage-fg-2 {
  left: 44%;
  top: 39%;
  width: 28%;
  height: 28%;
  z-index: 8;
}

.ws-stage-num-2 {
  left: 44.5%;
  top: 32%;
  color: var(--occluded-ink);
  z-index: 7;
}

.ws-style-staged.is-tilted .ws-stage-hero,
.ws-style-staged.is-tilted .ws-stage-num-hero {
  transform: perspective(2100px) rotateY(-8.2deg) rotateX(2.2deg) rotateZ(-1.8deg);
  transform-origin: 40% 45%;
}

.ws-style-staged.is-tilted .ws-stage-fg-0,
.ws-style-staged.is-tilted .ws-stage-num-0 {
  transform: perspective(2100px) rotateY(-5.6deg) rotateX(1.8deg) rotateZ(-1.3deg);
  transform-origin: 50% 50%;
}

.ws-style-staged.is-tilted .ws-stage-fg-1,
.ws-style-staged.is-tilted .ws-stage-num-1 {
  transform: perspective(2100px) rotateY(-4.2deg) rotateX(1.5deg) rotateZ(-0.9deg);
  transform-origin: 50% 50%;
}

.ws-style-staged.is-tilted .ws-stage-fg-2,
.ws-style-staged.is-tilted .ws-stage-num-2 {
  transform: perspective(2100px) rotateY(-5.0deg) rotateX(1.6deg) rotateZ(-1.1deg);
  transform-origin: 50% 50%;
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
  padding-top: 0;
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

/* ---------- 6b. window-callout: 0-callout Stage Mode ---------- */
.layout-window-callout.wc-mode-stage {
  padding: 34px 72px 32px;
  gap: 0;
}

.layout-window-callout.wc-mode-stage .wc-header {
  gap: 8px;
  max-width: 780px;
}

.layout-window-callout.wc-mode-stage .sc-title {
  font-size: 44px;
  line-height: 1.08;
  letter-spacing: -0.032em;
}

.layout-window-callout.wc-mode-stage .sc-title.is-cjk {
  font-size: 40px;
}

.layout-window-callout.wc-mode-stage .sc-subtitle {
  font-size: 17px;
  line-height: 1.42;
  max-width: 720px;
}

.wc-stage-center {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  padding-top: 38px;
  z-index: 4;
}

.wc-stage-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
}

.wc-stage-sculp {
  position: absolute;
  right: 2px;
  top: -48px;
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 84px;
  line-height: 0.85;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--occluded-ink);
  z-index: 1;
  user-select: none;
  pointer-events: none;
  white-space: nowrap;
}

.wc-stage-sculp.is-cjk {
  font-size: 62px;
  font-weight: 700;
  letter-spacing: 0.02em;
  text-transform: none;
  top: -44px;
}

.wc-stage-bezel {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 16px;
  z-index: 3;
  box-shadow: var(--shadow-hero);
  display: flex;
}

.theme-wallpaper-stage .wc-stage-bezel {
  padding: 8px;
  border-radius: 22px;
  background: var(--glass-bg);
  box-shadow:
    0 42px 104px -14px rgba(6, 4, 18, 0.76),
    0 18px 40px -8px rgba(6, 4, 18, 0.56),
    0 0 92px -12px color-mix(in srgb, var(--accent) 42%, rgba(235, 134, 102, 0.30)),
    0 0 0 1px rgba(255, 255, 255, 0.24),
    inset 0 1.4px 0 rgba(255, 255, 255, 0.48),
    inset 1.2px 0 0 rgba(255, 255, 255, 0.22);
}

.wc-stage-inner {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 15px;
  overflow: hidden;
  background: var(--frame-bg);
}

.theme-wallpaper-stage .wc-stage-inner {
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.42);
}

.wc-stage-inner img {
  width: 100%;
  height: 100%;
  display: block;
}

.wc-stage-inner::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--sheen-grad);
  box-shadow:
    inset 0 1.2px 0 var(--rim-top),
    inset 1px 0 0 var(--rim-left),
    inset 0 -1px 0 var(--rim-bottom);
  pointer-events: none;
  z-index: 5;
}

.theme-wallpaper-stage .wc-stage-inner::after {
  box-shadow: none;
}

.layout-window-callout.wc-mode-stage .sc-footer {
  margin-top: 16px;
  align-self: center;
}

.layout-window-callout.wc-mode-stage.orient-portrait,
.layout-window-callout.wc-mode-stage.orient-square {
  padding: 44px 48px 40px;
}

.layout-window-callout.wc-mode-stage.orient-portrait .wc-stage-center,
.layout-window-callout.wc-mode-stage.orient-square .wc-stage-center {
  justify-content: center;
  padding-top: 52px;
}

/* ---------- 6c. window-callout: 1-callout Single-Point Detail Loupe Mode ---------- */
.layout-window-callout.wc-mode-loupe {
  padding: 40px 72px 44px;
  gap: 0;
}

.layout-window-callout.wc-mode-loupe .wc-header {
  gap: 10px;
  max-width: 780px;
}

.layout-window-callout.wc-mode-loupe .sc-title {
  font-size: 44px;
  line-height: 1.1;
  letter-spacing: -0.032em;
}

.layout-window-callout.wc-mode-loupe .sc-title.is-cjk {
  font-size: 40px;
}

.layout-window-callout.wc-mode-loupe .sc-subtitle {
  font-size: 17px;
  line-height: 1.45;
  max-width: 740px;
}

.wc-loupe-grid {
  flex: 1 1 0;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.44fr) 92px minmax(0, 1fr);
  align-items: center;
  margin-top: 24px;
  position: relative;
  z-index: 4;
}

.wc-loupe-left {
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
}

.wc-loupe-left .wc-window-wrap {
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  margin: 0;
}

.wc-loupe-left .wc-window-frame {
  border-radius: 16px;
}

.wc-focus-mask-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 3;
}

.wc-loupe-focus-box {
  position: absolute;
  border-radius: 10px;
  border: 2px solid var(--accent);
  box-shadow:
    0 0 0 1px rgba(255, 255, 255, 0.25) inset,
    0 0 32px color-mix(in srgb, var(--accent) 56%, transparent),
    0 0 10px color-mix(in srgb, var(--accent) 45%, transparent);
  z-index: 4;
  pointer-events: none;
}

.wc-loupe-right {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  z-index: 5;
}

.wc-loupe-sculp {
  position: relative;
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 136px;
  line-height: 0.85;
  letter-spacing: -0.05em;
  color: color-mix(in srgb, var(--accent) 36%, transparent);
  margin-left: 2px;
  margin-bottom: -24px;
  z-index: 1;
  user-select: none;
  pointer-events: none;
}

.wc-loupe-card {
  position: relative;
  width: 100%;
  border-radius: 18px;
  background: var(--surface-bg);
  z-index: 4;
  box-shadow:
    var(--shadow-hero),
    0 0 64px -10px color-mix(in srgb, var(--accent) 24%, transparent);
}

.wc-loupe-card-inner {
  position: relative;
  border-radius: 18px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.wc-loupe-media {
  position: relative;
  width: 100%;
  height: 236px;
  background: var(--frame-bg);
  overflow: hidden;
  flex: none;
}

.wc-loupe-media img {
  width: 100%;
  height: 100%;
  display: block;
}

.wc-loupe-body {
  padding: 24px 28px 26px;
  border-top: 1px solid var(--hairline);
  background: var(--surface-bg);
}

.theme-dark-studio .wc-loupe-body {
  background: linear-gradient(180deg, #171a22 0%, #13161d 100%);
}

.wc-loupe-kicker {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 10px;
}

.wc-loupe-title {
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.2;
  color: var(--ink);
  margin-bottom: 10px;
}

.wc-loupe-desc {
  font-size: 16px;
  line-height: 1.55;
  color: var(--caption-ink);
}

.wc-loupe-metrics {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--hairline);
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--meta);
}

.wc-loupe-metrics strong {
  color: var(--body-ink);
  font-weight: 500;
}

.wc-loupe-card .wc-target-dot {
  left: -5px;
  top: 138px;
  width: 10px;
  height: 10px;
  transform: translateY(-50%);
}

.layout-window-callout.wc-mode-loupe.orient-portrait,
.layout-window-callout.wc-mode-loupe.orient-square {
  padding: 40px 44px 40px;
}

.layout-window-callout.wc-mode-loupe.orient-portrait .wc-loupe-grid,
.layout-window-callout.wc-mode-loupe.orient-square .wc-loupe-grid {
  display: flex;
  flex-direction: column;
  gap: 34px;
  margin-top: 24px;
}

.layout-window-callout.wc-mode-loupe.orient-portrait .wc-loupe-gutter,
.layout-window-callout.wc-mode-loupe.orient-square .wc-loupe-gutter {
  display: none;
}

.layout-window-callout.wc-mode-loupe.orient-portrait .wc-loupe-left,
.layout-window-callout.wc-mode-loupe.orient-square .wc-loupe-left {
  width: 100%;
  flex: 1 1 0;
  height: auto;
  align-items: center;
}

.layout-window-callout.wc-mode-loupe.orient-portrait .wc-loupe-right,
.layout-window-callout.wc-mode-loupe.orient-square .wc-loupe-right {
  width: 100%;
  flex: none;
}

.layout-window-callout.wc-mode-loupe.orient-portrait .wc-loupe-sculp,
.layout-window-callout.wc-mode-loupe.orient-square .wc-loupe-sculp {
  font-size: 96px;
  margin-bottom: -18px;
}

.layout-window-callout.wc-mode-loupe.orient-portrait .wc-loupe-media,
.layout-window-callout.wc-mode-loupe.orient-square .wc-loupe-media {
  height: 220px;
}

.layout-window-callout.wc-mode-loupe.orient-portrait .wc-loupe-card .wc-target-dot,
.layout-window-callout.wc-mode-loupe.orient-square .wc-loupe-card .wc-target-dot {
  left: 50%;
  top: -5px;
  transform: translateX(-50%);
}

/* ---------- 6d. window-callout: Blueprint CAD Mode ---------- */
.layout-window-callout.wc-mode-blueprint {
  padding: 34px 72px 72px;
  gap: 0;
}

.layout-window-callout.wc-mode-blueprint .wc-header {
  gap: 8px;
  max-width: 860px;
}

.layout-window-callout.wc-mode-blueprint .sc-title {
  font-size: 42px;
  line-height: 1.08;
  letter-spacing: -0.03em;
}

.layout-window-callout.wc-mode-blueprint .sc-title.is-cjk {
  font-size: 38px;
}

.layout-window-callout.wc-mode-blueprint .sc-subtitle {
  font-size: 17px;
  line-height: 1.42;
  max-width: 820px;
}

.wc-bp-body {
  flex: 1 1 0;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 84px 380px;
  align-items: stretch;
  margin-top: 64px;
  position: relative;
  z-index: 4;
}

.wc-bp-left {
  position: relative;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: flex-start;
}

.wc-bp-left .wc-window-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  margin: 0;
}

.wc-bp-sculp {
  position: absolute;
  right: 36px;
  top: -44px;
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 68px;
  line-height: 0.85;
  letter-spacing: -0.02em;
  color: color-mix(in srgb, var(--accent) 22%, transparent);
  z-index: 1;
  user-select: none;
  pointer-events: none;
  white-space: nowrap;
}

.wc-bp-sculp.is-cjk {
  font-size: 52px;
  font-weight: 700;
}

.wc-bp-specimen {
  border-radius: 10px;
  z-index: 3;
}

.wc-bp-right {
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  min-width: 0;
  min-height: 0;
  padding-top: 40px;
  z-index: 5;
}

.wc-bp-items {
  display: flex;
  flex-direction: column;
  gap: 56px;
}

.wc-bp-items.count-3 {
  gap: 32px;
}

.wc-bp-items.count-4 {
  gap: 20px;
}

.wc-bp-item {
  position: relative;
  padding-top: 16px;
  border-top: 1px solid var(--hairline);
}

.wc-bp-target-dot {
  position: absolute;
  left: 0;
  top: -1px;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  transform: translate(-50%, -50%);
  background: var(--accent);
  pointer-events: none;
  z-index: 8;
}

.wc-bp-code {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 8px;
}

.wc-bp-heading {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.24;
  color: var(--ink);
  margin-bottom: 8px;
}

.wc-bp-text {
  font-size: 15px;
  line-height: 1.52;
  color: var(--muted);
}

.wc-bp-meta {
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
  gap: 7px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--meta);
}

.wc-bp-meta-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.wc-bp-meta-row em {
  font-style: normal;
  color: var(--meta);
}

.wc-bp-meta-row strong {
  color: var(--ink);
  font-weight: 500;
}

.wc-bp-viewport-label {
  position: absolute;
  bottom: 44px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--meta);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 0 8px;
  z-index: 6;
  pointer-events: none;
  white-space: nowrap;
}

.layout-window-callout.wc-mode-blueprint.orient-portrait,
.layout-window-callout.wc-mode-blueprint.orient-square {
  padding: 42px 44px 52px;
}

.layout-window-callout.wc-mode-blueprint.orient-portrait .wc-bp-body,
.layout-window-callout.wc-mode-blueprint.orient-square .wc-bp-body {
  display: flex;
  flex-direction: column;
  gap: 28px;
  margin-top: 34px;
}

.layout-window-callout.wc-mode-blueprint.orient-portrait .wc-bp-gutter,
.layout-window-callout.wc-mode-blueprint.orient-square .wc-bp-gutter {
  display: none;
}

.layout-window-callout.wc-mode-blueprint.orient-portrait .wc-bp-left,
.layout-window-callout.wc-mode-blueprint.orient-square .wc-bp-left {
  flex: none;
  width: 100%;
  align-items: center;
}

.layout-window-callout.wc-mode-blueprint.orient-portrait .wc-bp-right,
.layout-window-callout.wc-mode-blueprint.orient-square .wc-bp-right {
  width: 100%;
  padding-top: 0;
  gap: 20px;
}

.layout-window-callout.wc-mode-blueprint.orient-portrait .wc-bp-items,
.layout-window-callout.wc-mode-blueprint.orient-square .wc-bp-items {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 22px;
}

.layout-window-callout.wc-mode-blueprint.orient-portrait .wc-bp-target-dot,
.layout-window-callout.wc-mode-blueprint.orient-square .wc-bp-target-dot {
  display: none;
}

/* ---------- 9. split-compare ---------- */
.layout-split-compare {
  display: flex;
  flex-direction: column;
  padding: 32px 72px 18px;
  gap: 0;
}

.sp-bg-shift {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    linear-gradient(
      90deg,
      rgba(15, 23, 42, 0.045) 0%,
      rgba(15, 23, 42, 0.025) calc(var(--split-pct) - 0.1%),
      color-mix(in srgb, var(--accent) 5%, rgba(255, 255, 255, 0.24)) calc(var(--split-pct) + 0.1%),
      color-mix(in srgb, var(--accent) 4%, rgba(255, 255, 255, 0.12)) 100%
    );
}

.theme-dark-studio .sp-bg-shift,
.theme-wallpaper-stage .sp-bg-shift {
  background:
    linear-gradient(
      90deg,
      rgba(0, 0, 0, 0.22) 0%,
      rgba(0, 0, 0, 0.10) calc(var(--split-pct) - 0.1%),
      color-mix(in srgb, var(--accent) 9%, transparent) calc(var(--split-pct) + 0.1%),
      color-mix(in srgb, var(--accent) 5%, transparent) 100%
    );
}

.sp-seam-ray {
  position: absolute;
  left: var(--split-pct);
  top: 0;
  bottom: 0;
  width: 1px;
  background: linear-gradient(
    180deg,
    rgba(15, 23, 42, 0.03) 0%,
    color-mix(in srgb, var(--accent) 24%, transparent) 24%,
    color-mix(in srgb, var(--accent) 28%, transparent) 85%,
    rgba(15, 23, 42, 0.04) 100%
  );
  pointer-events: none;
  z-index: 1;
}

.sp-header {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
  max-width: 780px;
  flex: none;
  z-index: 5;
}

.layout-split-compare .sc-title {
  font-size: 44px;
  line-height: 1.1;
}

.layout-split-compare .sc-title.is-cjk {
  font-size: 40px;
}

.layout-split-compare .sc-subtitle {
  font-size: 17px;
  line-height: 1.42;
}

.sp-stage {
  flex: 1 1 0;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  padding-top: 8px;
  z-index: 3;
}

.sp-column {
  width: 1160px;
  max-width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.sp-occluded-row {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  padding: 0 4px;
  margin-bottom: -15px;
  z-index: 1;
  pointer-events: none;
  flex: none;
}

.sp-sculp-label {
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 70px;
  line-height: 0.85;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  user-select: none;
  white-space: nowrap;
}

.sp-sculp-label.is-cjk {
  font-size: 52px;
  font-weight: 700;
  letter-spacing: 0.02em;
  text-transform: none;
}

.sp-sculp-before {
  color: var(--occluded-ink);
}

.sp-sculp-after {
  color: color-mix(in srgb, var(--accent) 30%, transparent);
}

.sp-frame-wrap {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  width: 100%;
  border-radius: 16px;
  z-index: 3;
  box-shadow: var(--shadow-hero);
}

.sp-frame {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 16px;
  overflow: hidden;
  background: var(--frame-bg);
}

.sp-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
}

.sp-img-left {
  clip-path: inset(0 calc(100% - var(--split-pct)) 0 0);
}

.sp-img-right {
  clip-path: inset(0 0 0 var(--split-pct));
}

.sp-divider-line {
  position: absolute;
  left: var(--split-pct);
  top: 0;
  bottom: 0;
  width: 2px;
  transform: translateX(-50%);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, #ffffff 50%, rgba(255, 255, 255, 0.92) 100%);
  box-shadow:
    0 0 22px 2px color-mix(in srgb, var(--accent) 55%, transparent),
    0 0 6px 1px rgba(15, 23, 42, 0.24);
  z-index: 8;
  pointer-events: none;
}

.sp-divider-handle {
  position: absolute;
  left: var(--split-pct);
  top: 50%;
  width: 44px;
  height: 44px;
  transform: translate(-50%, -50%);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  border: 1.5px solid rgba(255, 255, 255, 0.98);
  box-shadow:
    0 10px 28px rgba(15, 23, 42, 0.26),
    0 2px 8px color-mix(in srgb, var(--accent) 32%, transparent),
    inset 0 1px 0 #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9;
  pointer-events: none;
}

.sp-caption-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding-top: 14px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--muted);
  flex: none;
  z-index: 5;
}

.sp-caption-right {
  color: var(--accent);
  font-weight: 500;
  text-align: right;
}

.layout-split-compare.orient-portrait,
.layout-split-compare.orient-square {
  padding: 40px 44px 30px;
}

.layout-split-compare.orient-portrait .sp-column,
.layout-split-compare.orient-square .sp-column {
  width: 100%;
}

.layout-split-compare.orient-portrait .sp-sculp-label,
.layout-split-compare.orient-square .sp-sculp-label {
  font-size: 54px;
}

.layout-split-compare.orient-portrait .sp-sculp-label.is-cjk,
.layout-split-compare.orient-square .sp-sculp-label.is-cjk {
  font-size: 44px;
}

/* ---------- 10. device-pair ---------- */
.layout-device-pair {
  padding: 40px 72px 16px;
}

.dp-header {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  max-width: 740px;
  z-index: 5;
}

.layout-device-pair .sc-title {
  font-size: 46px;
  line-height: 1.1;
}

.layout-device-pair .sc-title.is-cjk {
  font-size: 42px;
}

.layout-device-pair .sc-subtitle {
  font-size: 17px;
  line-height: 1.45;
}

.dp-sculp-tag {
  position: absolute;
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 68px;
  line-height: 0.85;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  user-select: none;
  pointer-events: none;
  white-space: nowrap;
}

.dp-sculp-tag.is-cjk {
  font-size: 50px;
  font-weight: 700;
  letter-spacing: 0.02em;
  text-transform: none;
}

.dp-sculp-desktop {
  left: 53.5%;
  top: 158px;
  color: var(--occluded-ink);
  z-index: 1;
}

.dp-sculp-mobile {
  right: 88px;
  top: 186px;
  color: color-mix(in srgb, var(--accent) 26%, transparent);
  z-index: 3;
}

.dp-desktop-frame {
  position: absolute;
  left: 72px;
  top: 200px;
  width: 72.75%;
  height: calc(100% - 248px);
  border-radius: 16px;
  overflow: hidden;
  background: var(--frame-bg);
  z-index: 2;
  box-shadow: var(--shadow-hero);
}

.dp-desktop-frame img {
  width: 100%;
  height: 100%;
  display: block;
}

.dp-mobile-wrap {
  position: absolute;
  right: 92px;
  top: 228px;
  width: 312px;
  height: calc(100% - 268px);
  z-index: 4;
  border-radius: 44px;
  padding: 9px;
  background: linear-gradient(150deg, #32363e 0%, #191c22 45%, #111318 100%);
  box-shadow:
    -18px 26px 56px -8px rgba(15, 23, 42, 0.34),
    14px 32px 68px -10px rgba(24, 22, 18, 0.30),
    0 8px 20px -4px rgba(15, 23, 42, 0.22),
    0 0 0 1px rgba(255, 255, 255, 0.18),
    inset 0 1.5px 0 rgba(255, 255, 255, 0.42),
    inset 1.5px 0 0 rgba(255, 255, 255, 0.20);
}

.dp-mobile-screen {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 35px;
  overflow: hidden;
  background: var(--frame-bg);
  display: flex;
  flex-direction: column;
}

.dp-mobile-safe-top {
  height: 30px;
  flex: 0 0 30px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dp-mobile-safe-top.is-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  background: transparent;
  z-index: 6;
  pointer-events: none;
}

.dp-dynamic-island {
  width: 78px;
  height: 20px;
  border-radius: 12px;
  background: #0c0e12;
  box-shadow: inset 0 -1px 0 rgba(255, 255, 255, 0.08);
}

.dp-mobile-img-wrap {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.dp-mobile-img-wrap img {
  width: 100%;
  height: 100%;
  display: block;
}

.dp-mobile-home-bar {
  position: absolute;
  left: 50%;
  bottom: 8px;
  transform: translateX(-50%);
  width: 96px;
  height: 4px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.24);
  z-index: 5;
  pointer-events: none;
}

.dp-mobile-rim {
  position: absolute;
  inset: 0;
  border-radius: 35px;
  pointer-events: none;
  box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.08);
  z-index: 6;
}

.dp-footer-meta {
  position: absolute;
  left: 72px;
  bottom: 16px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--meta);
  z-index: 5;
}

.layout-device-pair.orient-portrait,
.layout-device-pair.orient-square {
  padding: 40px 44px 32px;
}

.layout-device-pair.orient-portrait .dp-desktop-frame {
  left: 44px;
  top: 248px;
  width: calc(100% - 88px);
  height: 580px;
}

.layout-device-pair.orient-portrait.is-tall .dp-desktop-frame {
  top: 270px;
  height: 680px;
}

.layout-device-pair.orient-portrait .dp-sculp-desktop {
  left: 52px;
  top: 208px;
  font-size: 56px;
}

.layout-device-pair.orient-portrait.is-tall .dp-sculp-desktop {
  top: 230px;
}

.layout-device-pair.orient-portrait .dp-mobile-wrap {
  right: 54px;
  top: 590px;
  width: 340px;
  height: 700px;
}

.layout-device-pair.orient-portrait.is-tall .dp-mobile-wrap {
  right: 60px;
  top: 840px;
  width: 400px;
  height: 820px;
}

.layout-device-pair.orient-portrait .dp-sculp-mobile {
  right: 68px;
  top: 548px;
  font-size: 56px;
}

.layout-device-pair.orient-portrait.is-tall .dp-sculp-mobile {
  top: 794px;
}

.layout-device-pair.orient-portrait .dp-footer-meta {
  left: 44px;
  bottom: 24px;
}

.layout-device-pair.orient-square .dp-desktop-frame {
  left: 44px;
  top: 224px;
  width: 82%;
  height: 620px;
}

.layout-device-pair.orient-square .dp-sculp-desktop {
  left: 46%;
  top: 184px;
  font-size: 56px;
}

.layout-device-pair.orient-square .dp-mobile-wrap {
  right: 44px;
  top: 340px;
  width: 310px;
  height: 640px;
}

.layout-device-pair.orient-square .dp-sculp-mobile {
  right: 62px;
  top: 298px;
  font-size: 56px;
}

.layout-device-pair.orient-square .dp-footer-meta {
  left: 44px;
  bottom: 24px;
}

/* ---------- 11. changelog-card ---------- */
.layout-changelog-card {
  padding: 52px 72px 48px;
}

.cl-left-col {
  position: absolute;
  left: 72px;
  top: 52px;
  bottom: 48px;
  width: 33.5%;
  display: flex;
  flex-direction: column;
  z-index: 4;
}

.cl-left-col .sc-brand {
  margin-bottom: 24px;
}

.cl-version-stage {
  position: relative;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--hairline);
  margin-bottom: 24px;
}

.cl-version-num {
  font-family: var(--font-serif);
  font-weight: 400;
  font-size: 132px;
  line-height: 0.86;
  letter-spacing: -0.045em;
  color: var(--ink);
  white-space: nowrap;
}

.cl-version-v {
  font-family: var(--font-sans);
  font-weight: 400;
  font-size: 74px;
  color: var(--meta);
  margin-right: 4px;
  vertical-align: 2px;
}

.cl-version-dot {
  color: var(--accent);
}

.cl-release-title {
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 32px;
  line-height: 1.18;
  letter-spacing: -0.028em;
  color: var(--ink);
  margin-bottom: 22px;
}

.cl-release-title.is-cjk {
  font-size: 30px;
  letter-spacing: -0.014em;
  line-height: 1.24;
}

.cl-highlights {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--hairline);
}

.cl-hl-item {
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 12px;
  padding: 18px 0;
  border-bottom: 1px solid var(--hairline);
  align-items: start;
}

.cl-highlights.count-4 .cl-hl-item {
  padding: 13px 0;
}

.cl-hl-idx {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  color: var(--accent);
  padding-top: 3px;
}

.cl-hl-title {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.018em;
  line-height: 1.26;
  color: var(--ink);
  margin-bottom: 5px;
}

.cl-hl-desc {
  font-size: 15px;
  line-height: 1.48;
  color: var(--muted);
}

.cl-left-footer {
  margin-top: auto;
  padding-top: 16px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--meta);
  letter-spacing: 0.02em;
}

.cl-sculp-tag {
  position: absolute;
  right: 76px;
  top: 94px;
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 92px;
  line-height: 0.85;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  color: var(--occluded-ink);
  z-index: 1;
  user-select: none;
  pointer-events: none;
  white-space: nowrap;
}

.cl-sculp-tag.is-cjk {
  font-size: 64px;
  font-weight: 700;
  text-transform: none;
  top: 104px;
}

.cl-shot-frame {
  position: absolute;
  left: 41.75%;
  top: 150px;
  width: calc(58.25% - 72px);
  height: calc(100% - 278px);
  border-radius: 16px;
  overflow: hidden;
  background: var(--frame-bg);
  z-index: 3;
  box-shadow: var(--shadow-hero);
}

.cl-shot-frame img {
  width: 100%;
  height: 100%;
  display: block;
}

.cl-shot-caption {
  position: absolute;
  left: 41.75%;
  right: 72px;
  bottom: 84px;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--muted);
  z-index: 4;
}

.layout-changelog-card.orient-portrait,
.layout-changelog-card.orient-square {
  display: flex;
  flex-direction: column;
  padding: 40px 44px 36px;
  gap: 22px;
}

.layout-changelog-card.orient-portrait .cl-left-col,
.layout-changelog-card.orient-square .cl-left-col {
  position: relative;
  left: auto;
  top: auto;
  bottom: auto;
  width: 100%;
  display: contents;
}

.layout-changelog-card.orient-portrait .cl-top-block,
.layout-changelog-card.orient-square .cl-top-block {
  order: 1;
}

.layout-changelog-card.orient-portrait .cl-version-num,
.layout-changelog-card.orient-square .cl-version-num {
  font-size: 96px;
}

.layout-changelog-card.orient-portrait .cl-version-v,
.layout-changelog-card.orient-square .cl-version-v {
  font-size: 56px;
}

.layout-changelog-card.orient-portrait .cl-shot-stage,
.layout-changelog-card.orient-square .cl-shot-stage {
  order: 2;
  position: relative;
  flex: 1 1 0;
  min-height: 360px;
  display: flex;
  flex-direction: column;
}

.layout-changelog-card.orient-portrait .cl-sculp-tag,
.layout-changelog-card.orient-square .cl-sculp-tag {
  position: relative;
  right: auto;
  top: auto;
  align-self: flex-end;
  font-size: 62px;
  margin-bottom: -14px;
  margin-right: 4px;
}

.layout-changelog-card.orient-portrait .cl-shot-frame,
.layout-changelog-card.orient-square .cl-shot-frame {
  position: relative;
  left: auto;
  top: auto;
  width: 100%;
  height: auto;
  flex: 1 1 0;
  min-height: 280px;
}

.layout-changelog-card.orient-portrait .cl-shot-caption,
.layout-changelog-card.orient-square .cl-shot-caption {
  position: relative;
  left: auto;
  right: auto;
  bottom: auto;
  padding-top: 12px;
}

.layout-changelog-card.orient-portrait .cl-highlights,
.layout-changelog-card.orient-square .cl-highlights {
  order: 3;
}

.layout-changelog-card.orient-landscape .cl-shot-stage {
  display: contents;
}

.layout-changelog-card.orient-portrait .cl-left-footer,
.layout-changelog-card.orient-square .cl-left-footer {
  order: 4;
  margin-top: 0;
  padding-top: 4px;
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
  const connOpacity = cs.getPropertyValue('--connector-opacity').trim() || '0.78';
  const ns = 'http://www.w3.org/2000/svg';
  let connLayer = null;

  if (svg) {
    svg.setAttribute('viewBox', `0 0 ${cRect.width} ${cRect.height}`);
    svg.innerHTML = '';
    const defs = document.createElementNS(ns, 'defs');
    const flt = document.createElementNS(ns, 'filter');
    flt.setAttribute('id', 'sc-conn-glow');
    flt.setAttribute('x', '-20%');
    flt.setAttribute('y', '-20%');
    flt.setAttribute('width', '140%');
    flt.setAttribute('height', '140%');
    const blur = document.createElementNS(ns, 'feGaussianBlur');
    blur.setAttribute('stdDeviation', '2.8');
    blur.setAttribute('result', 'blur');
    const merge = document.createElementNS(ns, 'feMerge');
    const n1 = document.createElementNS(ns, 'feMergeNode');
    n1.setAttribute('in', 'blur');
    const n2 = document.createElementNS(ns, 'feMergeNode');
    n2.setAttribute('in', 'SourceGraphic');
    merge.appendChild(n1);
    merge.appendChild(n2);
    flt.appendChild(blur);
    flt.appendChild(merge);
    defs.appendChild(flt);
    svg.appendChild(defs);
    connLayer = document.createElementNS(ns, 'g');
    connLayer.setAttribute('filter', 'url(#sc-conn-glow)');
    svg.appendChild(connLayer);
  }

  function appendDot(cx, cy, r, fill, opacity) {
    if (!connLayer) return;
    const circle = document.createElementNS(ns, 'circle');
    circle.setAttribute('cx', cx.toFixed(1));
    circle.setAttribute('cy', cy.toFixed(1));
    circle.setAttribute('r', String(r));
    circle.setAttribute('fill', fill);
    if (opacity !== undefined) circle.setAttribute('fill-opacity', String(opacity));
    connLayer.appendChild(circle);
  }

  function appendBeamPath(d, dash) {
    if (!connLayer) return;
    const halo = document.createElementNS(ns, 'path');
    halo.setAttribute('d', d);
    halo.setAttribute('fill', 'none');
    halo.setAttribute('stroke', accent);
    halo.setAttribute('stroke-opacity', '0.20');
    halo.setAttribute('stroke-width', '4.2');
    halo.setAttribute('stroke-linecap', 'round');
    connLayer.appendChild(halo);

    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', accent);
    path.setAttribute('stroke-opacity', connOpacity);
    path.setAttribute('stroke-width', '1.65');
    path.setAttribute('stroke-dasharray', dash || '6 5');
    path.setAttribute('stroke-linecap', 'round');
    connLayer.appendChild(path);
  }

  function getRenderedImageRect(imgEl, containerEl) {
    const w = containerEl ? containerEl.clientWidth : imgEl.clientWidth;
    const h = containerEl ? containerEl.clientHeight : imgEl.clientHeight;
    if (!imgEl.naturalWidth || !imgEl.naturalHeight || !w || !h) {
      return { left: 0, top: 0, width: w, height: h };
    }
    const style = getComputedStyle(imgEl);
    if (style.objectFit !== 'contain') {
      return { left: 0, top: 0, width: w, height: h };
    }
    const scale = Math.min(w / imgEl.naturalWidth, h / imgEl.naturalHeight);
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
      left: (w - rw) * px,
      top: (h - rh) * py,
      width: rw,
      height: rh,
    };
  }

  function centerInCanvas(el) {
    const r = el.getBoundingClientRect();
    return {
      x: r.left - cRect.left + r.width / 2,
      y: r.top - cRect.top + r.height / 2,
    };
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

      let d;
      if (Math.abs(y2 - y1) < 36) {
        d = `M ${x1.toFixed(1)} ${y1.toFixed(1)} L ${x2.toFixed(1)} ${y1.toFixed(1)}`;
        appendDot(x1, y1, 4, accent);
        appendDot(x2, y1, 4, accent);
      } else {
        const midX = (x1 + x2) / 2;
        d = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${midX.toFixed(1)} ${y1.toFixed(1)}, ${midX.toFixed(1)} ${y2.toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
        appendDot(x1, y1, 4, accent);
        appendDot(x2, y2, 4, accent);
      }
      appendBeamPath(d, '4 4');
    });
  }

  // 2a. window-callout Stage Mode (0 callouts): auto-hug centered window to native ratio
  if (canvas.classList.contains('wc-mode-stage')) {
    const stageCenter = document.querySelector('.wc-stage-center');
    const stageWrap = document.querySelector('.wc-stage-wrap');
    const stageImg = stageWrap ? stageWrap.querySelector('.wc-stage-inner img') : null;
    if (stageCenter && stageWrap && stageImg && stageImg.naturalWidth > 0 && stageImg.naturalHeight > 0) {
      const centerCs = getComputedStyle(stageCenter);
      const padT = parseFloat(centerCs.paddingTop) || 0;
      const padB = parseFloat(centerCs.paddingBottom) || 0;
      const stageRect = stageCenter.getBoundingClientRect();
      const availW = Math.floor(stageRect.width);
      const availH = Math.max(180, Math.floor(stageRect.height - padT - padB));
      const bezelPad = canvas.classList.contains('theme-wallpaper-stage') ? 16 : 0;
      const innerAvailW = Math.max(160, availW - bezelPad);
      const innerAvailH = Math.max(160, availH - bezelPad);
      const fitScale = Math.min(innerAvailW / stageImg.naturalWidth, innerAvailH / stageImg.naturalHeight);
      const boxW = Math.max(260, Math.round(stageImg.naturalWidth * fitScale) + bezelPad);
      const boxH = Math.max(180, Math.round(stageImg.naturalHeight * fitScale) + bezelPad);
      stageWrap.style.width = `${boxW}px`;
      stageWrap.style.height = `${boxH}px`;
    }
    return;
  }

  // 2b. window-callout Single-Point Detail Loupe Mode (1 callout)
  if (canvas.classList.contains('wc-mode-loupe')) {
    const mainWindow = document.querySelector('.wc-window-frame');
    const mainWrap = document.querySelector('.wc-window-wrap');
    const mainLeft = document.querySelector('.wc-loupe-left');
    const loupeCard = document.querySelector('.wc-loupe-card');
    const isLandscape = canvas.classList.contains('orient-landscape');
    if (mainWindow && loupeCard) {
      const winImg = mainWindow.querySelector('img');
      if (winImg && winImg.naturalWidth > 0 && winImg.naturalHeight > 0 && mainWrap && mainLeft) {
        const stageRect = mainLeft.getBoundingClientRect();
        const availW = Math.floor(stageRect.width);
        const availH = Math.max(180, Math.floor(stageRect.height));
        if (availW > 120 && availH > 120) {
          const fitScale = Math.min(availW / winImg.naturalWidth, availH / winImg.naturalHeight);
          const boxW = Math.max(260, Math.round(winImg.naturalWidth * fitScale));
          const boxH = Math.max(180, Math.round(winImg.naturalHeight * fitScale));
          mainWrap.style.width = `${boxW}px`;
          mainWrap.style.height = `${boxH}px`;
        }
      }

      const localImgRect = winImg
        ? getRenderedImageRect(winImg, mainWindow)
        : { left: 0, top: 0, width: mainWindow.clientWidth, height: mainWindow.clientHeight };

      const txPct = parseFloat(loupeCard.getAttribute('data-target-x') || '26');
      const tyPct = parseFloat(loupeCard.getAttribute('data-target-y') || '89');
      const twPct = parseFloat(loupeCard.getAttribute('data-target-w') || '48');
      const thPct = parseFloat(loupeCard.getAttribute('data-target-h') || '18');

      const rw = Math.max(24, localImgRect.width * (twPct / 100));
      const rh = Math.max(24, localImgRect.height * (thPct / 100));
      const rx = Math.max(4, Math.min(mainWindow.clientWidth - rw - 4, localImgRect.left + localImgRect.width * (txPct / 100) - rw / 2));
      const ry = Math.max(4, Math.min(mainWindow.clientHeight - rh - 4, localImgRect.top + localImgRect.height * (tyPct / 100) - rh / 2));

      const focusBox = mainWindow.querySelector('.wc-loupe-focus-box');
      if (focusBox) {
        focusBox.style.left = `${rx.toFixed(1)}px`;
        focusBox.style.top = `${ry.toFixed(1)}px`;
        focusBox.style.width = `${rw.toFixed(1)}px`;
        focusBox.style.height = `${rh.toFixed(1)}px`;
        const anchor = focusBox.querySelector('.wc-anchor-dot');
        if (anchor) {
          if (isLandscape) {
            anchor.style.right = '-5px';
            anchor.style.top = '50%';
            anchor.style.left = '';
            anchor.style.bottom = '';
            anchor.style.transform = 'translateY(-50%)';
          } else {
            anchor.style.left = '50%';
            anchor.style.bottom = '-5px';
            anchor.style.right = '';
            anchor.style.top = '';
            anchor.style.transform = 'translateX(-50%)';
          }
        }
      }

      const maskSvg = mainWindow.querySelector('.wc-focus-mask-svg');
      if (maskSvg) {
        const mw = mainWindow.clientWidth || 796;
        const mh = mainWindow.clientHeight || 661;
        maskSvg.setAttribute('viewBox', `0 0 ${mw} ${mh}`);
        const bgRect = maskSvg.querySelector('.wc-mask-bg');
        const dimRect = maskSvg.querySelector('.wc-mask-dim');
        const holeRect = maskSvg.querySelector('.wc-mask-hole');
        if (bgRect) { bgRect.setAttribute('width', String(mw)); bgRect.setAttribute('height', String(mh)); }
        if (dimRect) { dimRect.setAttribute('width', String(mw)); dimRect.setAttribute('height', String(mh)); }
        if (holeRect) {
          holeRect.setAttribute('x', rx.toFixed(1));
          holeRect.setAttribute('y', ry.toFixed(1));
          holeRect.setAttribute('width', rw.toFixed(1));
          holeRect.setAttribute('height', rh.toFixed(1));
        }
      }

      if (svg && connLayer && focusBox) {
        const anchor = focusBox.querySelector('.wc-anchor-dot') || focusBox;
        const dstDot = loupeCard.querySelector('.wc-target-dot');
        if (dstDot) {
          const pA = centerInCanvas(anchor);
          const pB = centerInCanvas(dstDot);
          let d;
          if (isLandscape) {
            const c1x = pA.x + (pB.x - pA.x) * 0.50;
            const c2x = pA.x + (pB.x - pA.x) * 0.65;
            d = `M ${pA.x.toFixed(1)} ${pA.y.toFixed(1)} C ${c1x.toFixed(1)} ${pA.y.toFixed(1)}, ${c2x.toFixed(1)} ${pB.y.toFixed(1)}, ${pB.x.toFixed(1)} ${pB.y.toFixed(1)}`;
          } else {
            const spanY = Math.max(40, pB.y - pA.y);
            const lift = Math.max(46, Math.min(140, spanY * 0.48));
            d = `M ${pA.x.toFixed(1)} ${pA.y.toFixed(1)} C ${pA.x.toFixed(1)} ${(pA.y + lift).toFixed(1)}, ${pB.x.toFixed(1)} ${(pB.y - lift).toFixed(1)}, ${pB.x.toFixed(1)} ${pB.y.toFixed(1)}`;
          }
          const halo = document.createElementNS(ns, 'path');
          halo.setAttribute('d', d);
          halo.setAttribute('fill', 'none');
          halo.setAttribute('stroke', accent);
          halo.setAttribute('stroke-opacity', '0.32');
          halo.setAttribute('stroke-width', '6');
          halo.setAttribute('stroke-linecap', 'round');
          connLayer.appendChild(halo);

          const path = document.createElementNS(ns, 'path');
          path.setAttribute('d', d);
          path.setAttribute('fill', 'none');
          path.setAttribute('stroke', accent);
          path.setAttribute('stroke-width', '2.2');
          path.setAttribute('stroke-dasharray', '7 6');
          path.setAttribute('stroke-linecap', 'round');
          connLayer.appendChild(path);

          const paperBase = cs.getPropertyValue('--paper-base').trim() || '#0c0e11';
          const surfaceBg = cs.getPropertyValue('--surface-bg').trim() || '#14171e';
          for (const [pt, ringStroke] of [[pA, paperBase], [pB, surfaceBg]]) {
            appendDot(pt.x, pt.y, 9, accent, 0.25);
            const dot = document.createElementNS(ns, 'circle');
            dot.setAttribute('cx', pt.x.toFixed(1));
            dot.setAttribute('cy', pt.y.toFixed(1));
            dot.setAttribute('r', '5');
            dot.setAttribute('fill', accent);
            dot.setAttribute('stroke', ringStroke);
            dot.setAttribute('stroke-width', '2');
            connLayer.appendChild(dot);
          }
        }
      }
    }
    return;
  }

  // 2c. window-callout Blueprint CAD Mode
  if (canvas.classList.contains('wc-mode-blueprint')) {
    const mainWindow = document.querySelector('.wc-window-frame');
    const mainWrap = document.querySelector('.wc-window-wrap');
    const mainLeft = document.querySelector('.wc-bp-left');
    const isLandscape = canvas.classList.contains('orient-landscape');
    if (mainWindow) {
      const winImg = mainWindow.querySelector('img');
      if (winImg && winImg.naturalWidth > 0 && winImg.naturalHeight > 0 && mainWrap && mainLeft) {
        if (isLandscape) {
          const stageRect = mainLeft.getBoundingClientRect();
          const availW = Math.floor(stageRect.width);
          const availH = Math.max(180, Math.floor(stageRect.height));
          if (availW > 120 && availH > 120) {
            const fitScale = Math.min(availW / winImg.naturalWidth, availH / winImg.naturalHeight);
            const boxW = Math.max(260, Math.round(winImg.naturalWidth * fitScale));
            const boxH = Math.max(180, Math.round(winImg.naturalHeight * fitScale));
            mainWrap.style.width = `${boxW}px`;
            mainWrap.style.height = `${boxH}px`;
          }
        } else {
          const natRatio = winImg.naturalWidth / winImg.naturalHeight;
          mainWindow.style.aspectRatio = Math.max(1.0, Math.min(2.2, natRatio)).toFixed(4);
        }
      }

      const localImgRect = winImg
        ? getRenderedImageRect(winImg, mainWindow)
        : { left: 0, top: 0, width: mainWindow.clientWidth, height: mainWindow.clientHeight };
      const wRect = mainWindow.getBoundingClientRect();
      const winLeft = wRect.left - cRect.left;
      const winTop = wRect.top - cRect.top;
      const winRight = wRect.right - cRect.left;
      const winBottom = wRect.bottom - cRect.top;

      // Dimension bracket under specimen plate when viewportLabel is present
      const vpLabel = document.querySelector('.wc-bp-viewport-label');
      if (vpLabel && svg) {
        const bracketY = winBottom + 16;
        const centerX = (winLeft + winRight) / 2;
        vpLabel.style.left = `${centerX.toFixed(1)}px`;
        vpLabel.style.top = `${bracketY.toFixed(1)}px`;
        vpLabel.style.bottom = 'auto';
        vpLabel.style.transform = 'translate(-50%, -50%)';
        const lblW = vpLabel.getBoundingClientRect().width || 160;
        const gapHalf = lblW / 2 + 12;

        const dimGroup = document.createElementNS(ns, 'g');
        dimGroup.setAttribute('stroke', 'rgba(15, 23, 42, 0.28)');
        dimGroup.setAttribute('stroke-width', '1');
        const segs = [
          [winLeft, bracketY, Math.max(winLeft, centerX - gapHalf), bracketY],
          [Math.min(winRight, centerX + gapHalf), bracketY, winRight, bracketY],
          [winLeft, bracketY - 6, winLeft, bracketY + 6],
          [winRight, bracketY - 6, winRight, bracketY + 6],
        ];
        for (const [x1, y1, x2, y2] of segs) {
          const ln = document.createElementNS(ns, 'line');
          ln.setAttribute('x1', x1.toFixed(1));
          ln.setAttribute('y1', y1.toFixed(1));
          ln.setAttribute('x2', x2.toFixed(1));
          ln.setAttribute('y2', y2.toFixed(1));
          dimGroup.appendChild(ln);
        }
        svg.appendChild(dimGroup);
      }

      if (isLandscape && svg) {
        const bpItems = document.querySelectorAll('.wc-bp-item[data-target-x][data-target-y]');
        const cadGroup = document.createElementNS(ns, 'g');
        svg.appendChild(cadGroup);
        bpItems.forEach((item) => {
          const dstDot = item.querySelector('.wc-bp-target-dot');
          if (!dstDot) return;
          const txPct = parseFloat(item.getAttribute('data-target-x') || '75');
          const tyPct = parseFloat(item.getAttribute('data-target-y') || '50');
          const ax = winLeft + localImgRect.left + localImgRect.width * (txPct / 100);
          const ay = winTop + localImgRect.top + localImgRect.height * (tyPct / 100);
          const pB = centerInCanvas(dstDot);
          const elbow1X = Math.min(pB.x - 42, winRight + 30);
          const elbow2X = Math.min(pB.x - 14, elbow1X + 28);

          const poly = document.createElementNS(ns, 'polyline');
          poly.setAttribute(
            'points',
            `${ax.toFixed(1)},${ay.toFixed(1)} ${elbow1X.toFixed(1)},${ay.toFixed(1)} ${elbow2X.toFixed(1)},${pB.y.toFixed(1)} ${pB.x.toFixed(1)},${pB.y.toFixed(1)}`
          );
          poly.setAttribute('fill', 'none');
          poly.setAttribute('stroke', accent);
          poly.setAttribute('stroke-width', '1.5');
          poly.setAttribute('stroke-dasharray', '4 4');
          cadGroup.appendChild(poly);

          const halo = document.createElementNS(ns, 'circle');
          halo.setAttribute('cx', ax.toFixed(1));
          halo.setAttribute('cy', ay.toFixed(1));
          halo.setAttribute('r', '8');
          halo.setAttribute('fill', accent);
          halo.setAttribute('fill-opacity', '0.16');
          cadGroup.appendChild(halo);

          const orig = document.createElementNS(ns, 'circle');
          orig.setAttribute('cx', ax.toFixed(1));
          orig.setAttribute('cy', ay.toFixed(1));
          orig.setAttribute('r', '4');
          orig.setAttribute('fill', accent);
          orig.setAttribute('stroke', '#ffffff');
          orig.setAttribute('stroke-width', '1.6');
          cadGroup.appendChild(orig);

          const dest = document.createElementNS(ns, 'circle');
          dest.setAttribute('cx', pB.x.toFixed(1));
          dest.setAttribute('cy', pB.y.toFixed(1));
          dest.setAttribute('r', '3.5');
          dest.setAttribute('fill', accent);
          cadGroup.appendChild(dest);
        });
      } else {
        const pins = mainWindow.querySelectorAll('.wc-pin-marker[data-target-x][data-target-y]');
        pins.forEach((pin) => {
          const txPct = parseFloat(pin.getAttribute('data-target-x') || '50');
          const tyPct = parseFloat(pin.getAttribute('data-target-y') || '50');
          pin.style.left = `${(localImgRect.left + localImgRect.width * (txPct / 100)).toFixed(1)}px`;
          pin.style.top = `${(localImgRect.top + localImgRect.height * (tyPct / 100)).toFixed(1)}px`;
        });
      }
    }
    return;
  }

  // 2d. window-callout standard mode (2-4 callouts): sizing, reticles, zero-drift connectors & portrait pins
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
        const labelH = labelEl ? (labelEl.getBoundingClientRect().height + 8) : 0;
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
        const frameH = mainWindow.clientHeight;
        if (frameH > 20 && stageRect.width > 100) {
          const idealW = Math.round(frameH * natRatio);
          const clampedW = Math.min(Math.floor(stageRect.width), Math.max(260, idealW));
          mainWrap.style.width = `${clampedW}px`;
        }
      } else if (!canvas.classList.contains('orient-landscape')) {
        const clampedRatio = Math.max(1.0, Math.min(2.3, natRatio));
        mainWindow.style.aspectRatio = clampedRatio.toFixed(4);
      }
    }

    const localImgRect = winImg
      ? getRenderedImageRect(winImg, mainWindow)
      : { left: 0, top: 0, width: mainWindow.clientWidth, height: mainWindow.clientHeight };

    if (canvas.classList.contains('orient-landscape') && svg) {
      const units = document.querySelectorAll('.wc-callout-unit[data-callout-idx]');
      // Pass 1: position every in-plane reticle/pinpoint in exact un-transformed local frame coords
      units.forEach((unit) => {
        const idx = unit.getAttribute('data-callout-idx');
        const marker = mainWindow.querySelector(`[data-marker-idx="${idx}"]`);
        const dstDot = unit.querySelector('.wc-target-dot');
        if (!marker || !dstDot) return;

        const txPct = parseFloat(unit.getAttribute('data-target-x') || '50');
        const tyPct = parseFloat(unit.getAttribute('data-target-y') || '50');
        const twAttr = unit.getAttribute('data-target-w');
        const thAttr = unit.getAttribute('data-target-h');
        const cx = localImgRect.left + localImgRect.width * (txPct / 100);
        const cy = localImgRect.top + localImgRect.height * (tyPct / 100);
        const anchor = marker.querySelector('.wc-anchor-dot');

        if (twAttr && thAttr) {
          const rw = localImgRect.width * (parseFloat(twAttr) / 100);
          const rh = localImgRect.height * (parseFloat(thAttr) / 100);
          marker.style.left = `${(cx - rw / 2).toFixed(1)}px`;
          marker.style.top = `${(cy - rh / 2).toFixed(1)}px`;
          marker.style.width = `${rw.toFixed(1)}px`;
          marker.style.height = `${rh.toFixed(1)}px`;
          if (anchor) {
            const dstPos = centerInCanvas(dstDot);
            const winTopInCanvas = mainWindow.getBoundingClientRect().top - cRect.top;
            const overTop = isSplit && txPct < 24 && tyPct < 28 && dstPos.y < (winTopInCanvas + cy - 16);
            if (overTop) {
              anchor.style.right = '2px';
              anchor.style.top = '-4px';
              anchor.style.left = '';
              anchor.style.bottom = '';
              anchor.style.transform = '';
              marker.setAttribute('data-route', 'over-top');
            } else if (isSplit) {
              anchor.style.right = '-4px';
              anchor.style.top = '50%';
              anchor.style.left = '';
              anchor.style.bottom = '';
              anchor.style.transform = 'translateY(-50%)';
              marker.setAttribute('data-route', 'right');
            } else {
              anchor.style.left = '50%';
              anchor.style.bottom = '-4px';
              anchor.style.right = '';
              anchor.style.top = '';
              anchor.style.transform = 'translateX(-50%)';
              marker.setAttribute('data-route', 'down');
            }
          }
        } else {
          marker.style.left = `${cx.toFixed(1)}px`;
          marker.style.top = `${cy.toFixed(1)}px`;
          marker.setAttribute('data-route', isSplit ? 'right' : 'down');
        }
      });

      // Pass 2: measure exact post-layout (and post-3D-transform if tilted!) screen coordinates
      units.forEach((unit) => {
        const idx = unit.getAttribute('data-callout-idx');
        const marker = mainWindow.querySelector(`[data-marker-idx="${idx}"]`);
        const dstDot = unit.querySelector('.wc-target-dot');
        if (!marker || !dstDot) return;
        const anchor = marker.querySelector('.wc-anchor-dot') || marker;
        const pA = centerInCanvas(anchor);
        const pB = centerInCanvas(dstDot);
        const route = marker.getAttribute('data-route') || (isSplit ? 'right' : 'down');

        let d;
        if (isSplit && route === 'over-top') {
          d = `M ${pA.x.toFixed(1)} ${pA.y.toFixed(1)} C ${pA.x.toFixed(1)} ${(pB.y + 2).toFixed(1)}, ${(pA.x + 110).toFixed(1)} ${pB.y.toFixed(1)}, ${pB.x.toFixed(1)} ${pB.y.toFixed(1)}`;
        } else if (isSplit) {
          const spanX = Math.max(48, pB.x - pA.x);
          const pull = Math.max(44, Math.min(210, spanX * 0.46));
          d = `M ${pA.x.toFixed(1)} ${pA.y.toFixed(1)} C ${(pA.x + pull).toFixed(1)} ${pA.y.toFixed(1)}, ${(pB.x - pull).toFixed(1)} ${pB.y.toFixed(1)}, ${pB.x.toFixed(1)} ${pB.y.toFixed(1)}`;
        } else {
          const spanY = Math.max(40, pB.y - pA.y);
          const lift = Math.max(46, Math.min(150, spanY * 0.46));
          d = `M ${pA.x.toFixed(1)} ${pA.y.toFixed(1)} C ${pA.x.toFixed(1)} ${(pA.y + lift).toFixed(1)}, ${pB.x.toFixed(1)} ${(pB.y - lift).toFixed(1)}, ${pB.x.toFixed(1)} ${pB.y.toFixed(1)}`;
        }
        appendBeamPath(d, '6 5');
      });
    } else {
      // Portrait / square: position numbered pin markers relative to rendered image content rect
      const pins = mainWindow.querySelectorAll('.wc-pin-marker[data-target-x][data-target-y]');
      pins.forEach((pin) => {
        const txPct = parseFloat(pin.getAttribute('data-target-x') || '50');
        const tyPct = parseFloat(pin.getAttribute('data-target-y') || '50');
        const localX = localImgRect.left + localImgRect.width * (txPct / 100);
        const localY = localImgRect.top + localImgRect.height * (tyPct / 100);
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
    else:
        parts.append('<span class="sc-brand-dot" aria-hidden="true"></span>')
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


def occluded_label_classes(text: str, *, accent: bool = False, extra: str = "") -> tuple[str, bool]:
    """Return CSS classes for a behind-the-card occluded label and whether compact occlusion is needed."""
    is_cjk = has_cjk(text)
    length = len(text.strip())
    size_cls = ""
    compact = is_cjk
    if is_cjk:
        if length > 8:
            size_cls = " is-long"
        elif length > 5:
            size_cls = " is-medium"
    else:
        if length > 13:
            size_cls = " is-long"
            compact = True
        elif length > 8:
            size_cls = " is-medium"
            compact = True
    parts = ["sc-occluded-label"]
    if is_cjk:
        parts.append("is-cjk")
    if size_cls:
        parts.append(size_cls.strip())
    if accent:
        parts.append("is-accent")
    if extra:
        parts.append(extra.strip())
    return " ".join(parts), compact


def render_layer_card(
    layer: dict[str, Any],
    extra_cls: str,
    show_badge: bool,
    *,
    inline_relative: bool = False,
) -> str:
    trans_cls = " is-transparent" if layer.get("transparent") else ""
    badge = (
        f'<span class="sc-layer-index">{esc(layer["index"])}</span>'
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
        contain_cls = " is-contain" if item["fit"] == "contain" else ""
        fit_style = build_img_style(item["fit"], item["position"])
        tile_bg = build_bg_style(item.get("bg", ""))
        tile_style_attr = f' style="{tile_bg}"' if tile_bg else ""
        tiles.append(
            f'<div class="lb-tile{span_cls}{contain_cls}"{tile_style_attr}>'
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
        contain_cls = " is-contain" if item["fit"] == "contain" else ""
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
            f'<div class="mb-card-media{contain_cls}"{media_style_attr}>'
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
    wc_mode = card.get("wcMode", "standard")
    is_landscape = card["orientation"] == "landscape"
    is_split = is_landscape and card.get("arrangement", "split") == "split"
    win_tilt_cls = " is-tilted" if (is_landscape and card.get("tilt", False)) else ""
    brand_html = render_brand_html(card["brand"])

    win_bg = build_bg_style(win.get("bg", ""))
    win_style_attr = f' style="{win_bg}"' if win_bg else ""
    win_img_style = build_img_style(win["fit"], win["position"])
    win_label_html = (
        f'<div class="wc-window-label">{esc(win["label"])}</div>'
        if win.get("label")
        else ""
    )

    # Mode A: 0-callout Stage Mode (e.g. wallpaper-stage hero presentation)
    if wc_mode == "stage":
        stage_lbl = card.get("stageLabel", "")
        sculp_cls = "wc-stage-sculp is-cjk" if has_cjk(stage_lbl) else "wc-stage-sculp"
        sculp_html = f'<div class="{sculp_cls}" aria-hidden="true">{esc(stage_lbl)}</div>' if stage_lbl else ""
        stage_img_style = build_img_style(win["fit"], win["position"])
        return f"""
        <div class="wc-header">
          {brand_html}
          <div class="wc-title-row">
            {render_title_html(card["title"])}
            {render_subtitle_html(card["subtitle"])}
          </div>
        </div>
        <div class="wc-stage-center">
          <div class="wc-stage-wrap">
            {sculp_html}
            <div class="wc-stage-bezel">
              <div class="wc-stage-inner"{win_style_attr}>
                <img src="{esc(win["image"])}" alt="{esc(win["label"] or card["title"])}" style="{stage_img_style}">
              </div>
            </div>
          </div>
        </div>
        {render_footer_html(card["footer"])}
        """

    # Mode B: 1-callout Single-Point Detail Loupe Mode
    if wc_mode == "loupe" and callouts:
        co = callouts[0]
        media_bg = build_bg_style(co.get("bg", ""))
        media_style_attr = f' style="{media_bg}"' if media_bg else ""
        co_img_style = build_img_style(co["fit"], co["position"])
        contain_cls = " is-contain" if co["fit"] == "contain" else ""
        dim_fill = (
            "rgba(6, 8, 12, 0.46)"
            if card["theme"] in ("dark-studio", "wallpaper-stage")
            else "rgba(15, 23, 42, 0.24)"
        )
        kicker_text = co.get("eyebrow") or co.get("tag") or ""
        kicker_html = f'<div class="wc-loupe-kicker">{esc(kicker_text)}</div>' if kicker_text else ""
        desc_html = f'<p class="wc-loupe-desc">{esc(co["caption"])}</p>' if co.get("caption") else ""
        specs_html = ""
        if co.get("specs"):
            spec_spans = "".join(
                f'<span>{esc(sp["label"])} <strong>{esc(sp["value"])}</strong></span>'
                for sp in co["specs"]
            )
            specs_html = f'<div class="wc-loupe-metrics">{spec_spans}</div>'
        tw = co.get("targetW") if co.get("targetW") is not None else 48.0
        th = co.get("targetH") if co.get("targetH") is not None else 18.0
        mask_id = f"wc-spot-mask-{re.sub(r'[^A-Za-z0-9_-]', '', card['id'])}"
        return f"""
        <div class="wc-header">
          {brand_html}
          <div class="wc-title-row">
            {render_title_html(card["title"])}
            {render_subtitle_html(card["subtitle"])}
          </div>
        </div>
        <div class="wc-loupe-grid">
          <div class="wc-loupe-left">
            <div class="wc-window-wrap">
              <div class="wc-window-frame"{win_style_attr}>
                <img src="{esc(win["image"])}" alt="{esc(win["label"] or card["title"])}" style="{win_img_style}">
                <svg class="wc-focus-mask-svg" viewBox="0 0 796 661" preserveAspectRatio="none" aria-hidden="true">
                  <defs>
                    <mask id="{mask_id}">
                      <rect class="wc-mask-bg" x="0" y="0" width="796" height="661" fill="white"/>
                      <rect class="wc-mask-hole" x="6" y="530" width="396" height="124" rx="10" fill="black"/>
                    </mask>
                  </defs>
                  <rect class="wc-mask-dim" x="0" y="0" width="796" height="661" fill="{dim_fill}" mask="url(#{mask_id})"/>
                </svg>
                <div class="wc-loupe-focus-box">
                  <span class="wc-anchor-dot" aria-hidden="true"></span>
                </div>
              </div>
            </div>
          </div>
          <div class="wc-loupe-gutter" aria-hidden="true"></div>
          <div class="wc-loupe-right">
            <div class="wc-loupe-sculp" aria-hidden="true">{esc(co["index"])}</div>
            <div class="wc-loupe-card" data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}" data-target-w="{tw}" data-target-h="{th}">
              <div class="wc-loupe-card-inner">
                <div class="wc-loupe-media{contain_cls}"{media_style_attr}>
                  <img src="{esc(co["image"])}" alt="{esc(co["title"])}" style="{co_img_style}">
                </div>
                <div class="wc-loupe-body">
                  {kicker_html}
                  <h2 class="wc-loupe-title">{esc(co["title"])}</h2>
                  {desc_html}
                  {specs_html}
                </div>
              </div>
              <span class="wc-target-dot" aria-hidden="true"></span>
            </div>
          </div>
        </div>
        {render_footer_html(card["footer"])}
        <svg class="sc-connectors" aria-hidden="true"></svg>
        """

    # Mode C: Blueprint CAD Mode (orthogonal dashed leader lines + spec column + metadata)
    if wc_mode == "blueprint":
        stage_lbl = card.get("stageLabel") or "SPEC 01"
        sculp_cls = "wc-bp-sculp is-cjk" if has_cjk(stage_lbl) else "wc-bp-sculp"
        sculp_html = f'<div class="{sculp_cls}" aria-hidden="true">{esc(stage_lbl)}</div>'
        bp_pins = ""
        if not is_landscape:
            bp_pins = "".join(
                f'<span class="wc-pin-marker" data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}" '
                f'style="left:{co["targetX"]}%;top:{co["targetY"]}%;">{esc(co["index"])}</span>'
                for co in callouts
            )
        spec_blocks: list[str] = []
        for idx, co in enumerate(callouts):
            code_text = co.get("eyebrow") or (
                f"A·{co['index']} · {co['tag']}" if co.get("tag") else f"A·{co['index']}"
            )
            desc_html = f'<p class="wc-bp-text">{esc(co["caption"])}</p>' if co.get("caption") else ""
            spec_blocks.append(
                f'<div class="wc-bp-item" data-callout-idx="{idx}" '
                f'data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}">'
                f'<span class="wc-bp-target-dot" aria-hidden="true"></span>'
                f'<div class="wc-bp-code">{esc(code_text)}</div>'
                f'<h2 class="wc-bp-heading">{esc(co["title"])}</h2>'
                f"{desc_html}</div>"
            )
        meta_html = ""
        if card.get("specs"):
            meta_rows = "".join(
                f'<div class="wc-bp-meta-row"><em>{esc(sp["label"])}</em><strong>{esc(sp["value"])}</strong></div>'
                for sp in card["specs"]
            )
            meta_html = f'<div class="wc-bp-meta">{meta_rows}</div>'
        vp_html = (
            f'<div class="wc-bp-viewport-label">{esc(card["viewportLabel"])}</div>'
            if card.get("viewportLabel")
            else ""
        )
        return f"""
        <div class="wc-header">
          {brand_html}
          <div class="wc-title-row">
            {render_title_html(card["title"])}
            {render_subtitle_html(card["subtitle"])}
          </div>
        </div>
        <div class="wc-bp-body">
          <div class="wc-bp-left">
            <div class="wc-window-wrap">
              {sculp_html}
              <div class="wc-window-frame wc-bp-specimen"{win_style_attr}>
                <img src="{esc(win["image"])}" alt="{esc(win["label"] or card["title"])}" style="{win_img_style}">
                {bp_pins}
              </div>
            </div>
          </div>
          <div class="wc-bp-gutter" aria-hidden="true"></div>
          <div class="wc-bp-right">
            <div class="wc-bp-items count-{len(callouts)}">{"".join(spec_blocks)}</div>
            {meta_html}
          </div>
        </div>
        {vp_html}
        {render_footer_html(card["footer"])}
        <svg class="sc-connectors" aria-hidden="true"></svg>
        """

    # Mode D: Standard 2-4 callout split / stacked mode (existing v0.3.0 behavior)

    # In-plane focus reticles/pinpoints on landscape; high-contrast numbered pins on portrait/square.
    markers_html = ""
    if is_landscape:
        marker_parts: list[str] = []
        for idx, co in enumerate(callouts):
            has_rect = co.get("targetW") is not None and co.get("targetH") is not None
            if has_rect:
                marker_parts.append(
                    f'<div class="wc-reticle" data-marker-idx="{idx}">'
                    f'<span class="wc-anchor-dot" aria-hidden="true"></span></div>'
                )
            else:
                marker_parts.append(
                    f'<div class="wc-pinpoint" data-marker-idx="{idx}">'
                    f'<span class="wc-anchor-dot" aria-hidden="true"></span></div>'
                )
        markers_html = "".join(marker_parts)
    else:
        markers_html = "".join(
            f'<span class="wc-pin-marker" data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}" '
            f'style="left:{co["targetX"]}%;top:{co["targetY"]}%;">'
            f'{esc(co["index"])}</span>'
            for co in callouts
        )

    callout_cards: list[str] = []
    for idx, co in enumerate(callouts):
        media_bg = build_bg_style(co.get("bg", ""))
        media_style_attr = f' style="{media_bg}"' if media_bg else ""
        img_style = build_img_style(co["fit"], co["position"])
        size_attrs = ""
        if co.get("targetW") is not None and co.get("targetH") is not None:
            size_attrs = f' data-target-w="{co["targetW"]}" data-target-h="{co["targetH"]}"'
        contain_cls = " is-contain" if co["fit"] == "contain" else ""

        if is_landscape:
            sub_text = co.get("caption") or co.get("tag") or ""
            sub_html = (
                f'<span class="wc-callout-sub">{esc(sub_text)}</span>'
                if sub_text
                else ""
            )
            behind_header = (
                f'<div class="sc-occluded-header wc-callout-behind">'
                f'<span class="sc-occluded-label is-accent wc-callout-num">{esc(co["index"])}</span>'
                f'<div class="wc-callout-meta-top">'
                f'<span class="wc-callout-title">{esc(co["title"])}</span>'
                f"{sub_html}</div></div>"
            )
            callout_cards.append(
                f'<div class="wc-callout-unit" data-callout-idx="{idx}" '
                f'data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}"{size_attrs}>'
                f"{behind_header}"
                f'<div class="wc-callout-frame">'
                f'<div class="wc-callout-frame-inner">'
                f'<div class="wc-callout-media{contain_cls}"{media_style_attr}>'
                f'<img src="{esc(co["image"])}" alt="{esc(co["title"])}" style="{img_style}"></div>'
                f'</div><span class="wc-target-dot" aria-hidden="true"></span></div></div>'
            )
        else:
            tag_html = (
                f'<span class="wc-callout-tag">{esc(co["tag"])}</span>'
                if co.get("tag")
                else ""
            )
            caption_html = (
                f'<div class="wc-callout-caption">{esc(co["caption"])}</div>'
                if co.get("caption")
                else ""
            )
            meta_html = (
                f'<div class="wc-callout-meta">'
                f'<div class="wc-row-num">{esc(co["index"])}</div>'
                f'<div class="wc-callout-head">'
                f'<span class="wc-callout-title">{esc(co["title"])}</span>{tag_html}</div>'
                f"{caption_html}</div>"
            )
            callout_cards.append(
                f'<div class="wc-callout-card" data-target-x="{co["targetX"]}" data-target-y="{co["targetY"]}"{size_attrs}>'
                f'<div class="wc-callout-media{contain_cls}"{media_style_attr}>'
                f'<img src="{esc(co["image"])}" alt="{esc(co["title"])}" style="{img_style}"></div>'
                f"{meta_html}</div>"
            )

    svg_html = '<svg class="sc-connectors" aria-hidden="true"></svg>' if is_landscape else ""

    if is_split:
        stack_cls = f"wc-callouts-stack count-{len(callouts)}"
        return f"""
        <div class="wc-split-body">
          <div class="wc-split-left">
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
                <div class="wc-window-frame{win_tilt_cls}"{win_style_attr}>
                  <img src="{esc(win["image"])}" alt="{esc(win["label"])}" style="{win_img_style}">
                  {markers_html}
                </div>
              </div>
            </div>
            {render_footer_html(card["footer"])}
          </div>
          <div class="wc-gutter" aria-hidden="true"></div>
          <div class="{stack_cls}">{"".join(callout_cards)}</div>
        </div>
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
        <div class="wc-window-frame{win_tilt_cls}"{win_style_attr}>
          <img src="{esc(win["image"])}" alt="{esc(win["label"])}" style="{win_img_style}">
          {markers_html}
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
    is_stacked = card.get("compareOrientation") == "stacked"
    delta_badge = card.get("deltaBadge", "")
    after_wp = after.get("widthPercent")
    use_reclaimed_zone = is_stacked and after_wp is not None and after_wp < 100
    brand_html = render_brand_html(card["brand"])

    def render_delta_monumental(text: str) -> str:
        if not text:
            return ""
        if "·" in text:
            head, sub = (part.strip() for part in text.split("·", 1))
            sub_html = f'<div class="ba-delta-sub">{esc(sub)}</div>' if sub else ""
            return f'<div class="ba-delta-hero">{esc(head)}</div>{sub_html}'
        return f'<div class="ba-delta-hero">{esc(text)}</div>'

    def render_pane(side: dict[str, Any], is_after: bool) -> str:
        contain_cls = " is-contain" if side["fit"] == "contain" else ""
        img_style = build_img_style(side["fit"], side["position"])
        caption_html = (
            f'<div class="ba-caption">{esc(side["caption"])}</div>'
            if side.get("caption")
            else ""
        )
        cls = "ba-pane is-after" if is_after else "ba-pane is-before"
        lbl_cls, compact = occluded_label_classes(side["label"], accent=is_after)
        hdr_cls = "sc-occluded-header is-compact-occlude" if compact else "sc-occluded-header"
        meta_text = side.get("meta", "")
        meta_cls = "sc-occluded-meta is-accent" if is_after else "sc-occluded-meta"
        meta_html = f'<span class="{meta_cls}">{esc(meta_text)}</span>' if meta_text else ""
        occluded_hdr = (
            f'<div class="{hdr_cls}">'
            f'<span class="{lbl_cls}">{esc(side["label"])}</span>'
            f"{meta_html}</div>"
        )

        if is_after and use_reclaimed_zone:
            frame_style = build_bg_style(
                side.get("bg", ""), extra=f"width:{after_wp:g}%;"
            )
            frame_attr = f' style="{frame_style}"' if frame_style else ""
            reclaimed_inner = render_delta_monumental(delta_badge)
            media_block = (
                f'<div class="ba-after-track">'
                f'<div class="ba-frame{contain_cls}"{frame_attr}>'
                f'<img src="{esc(side["image"])}" alt="{esc(side["label"])}" style="{img_style}"></div>'
                f'<div class="ba-reclaimed-zone">'
                f'<div class="ba-trim-hairline" aria-hidden="true"></div>'
                f"{reclaimed_inner}</div>"
                f"</div>"
            )
        else:
            frame_bg = build_bg_style(side.get("bg", ""))
            frame_attr = f' style="{frame_bg}"' if frame_bg else ""
            media_block = (
                f'<div class="ba-frame{contain_cls}"{frame_attr}>'
                f'<img src="{esc(side["image"])}" alt="{esc(side["label"])}" style="{img_style}"></div>'
            )
        return f'<div class="{cls}">{occluded_hdr}{media_block}{caption_html}</div>'

    if is_stacked:
        arrow_svg = (
            '<svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">'
            '<path d="M14 4V22.5M14 22.5L7.5 16M14 22.5L20.5 16" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
    else:
        arrow_svg = (
            '<svg width="64" height="32" viewBox="0 0 64 32" fill="none" aria-hidden="true">'
            '<defs><linearGradient id="baArrowBeam" x1="2" y1="16" x2="56" y2="16" gradientUnits="userSpaceOnUse">'
            '<stop offset="0%" stop-color="currentColor" stop-opacity="0.12"/>'
            '<stop offset="58%" stop-color="currentColor" stop-opacity="0.72"/>'
            '<stop offset="100%" stop-color="currentColor" stop-opacity="1"/>'
            '</linearGradient></defs>'
            '<line x1="2" y1="16" x2="55" y2="16" stroke="url(#baArrowBeam)" stroke-width="2" stroke-linecap="round"/>'
            '<path d="M47 8.5L56.5 16L47 23.5" stroke="currentColor" stroke-width="2.1" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
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

    if is_stacked:
        show_inline_delta = bool(delta_badge) and not use_reclaimed_zone
        delta_inline_html = (
            f'<span class="ba-delta-inline">{esc(delta_badge)}</span>'
            if show_inline_delta
            else ""
        )
        plane_hairline = (
            f'<div class="ba-plane-hairline" style="left:{after_wp:g}%;" aria-hidden="true"></div>'
            if use_reclaimed_zone
            else ""
        )
        divider_html = (
            ""
            if use_reclaimed_zone
            else f'<div class="ba-divider-row" aria-hidden="true">{arrow_svg}{delta_inline_html}</div>'
        )
        stage_inner = (
            f'<div class="ba-stacked-plane">'
            f"{render_pane(before, False)}"
            f"{plane_hairline}"
            f"{divider_html}"
            f"{render_pane(after, True)}"
            f"</div>"
        )
    else:
        delta_inline_html = (
            f'<span class="ba-delta-inline">{esc(delta_badge)}</span>'
            if delta_badge
            else ""
        )
        stage_inner = (
            f"{render_pane(before, False)}"
            f'<div class="ba-arrow-bridge" aria-hidden="true">{arrow_svg}{delta_inline_html}</div>'
            f"{render_pane(after, True)}"
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
      {stage_inner}
    </div>
    {bottom_bar}
    """


def build_workflow_steps_body(card: dict[str, Any]) -> str:
    steps = card["steps"]
    orient = card["orientation"]
    ws_style = card.get("wsStyle", "row")
    brand_html = render_brand_html(card["brand"])

    if ws_style == "staged" and orient == "landscape":
        milestones: list[str] = []
        for st in steps:
            cap_html = (
                f'<div class="ws-milestone-caption">{esc(st["caption"])}</div>'
                if st.get("caption")
                else ""
            )
            milestones.append(
                f'<div class="ws-milestone">'
                f'<div class="ws-milestone-idx">{esc(st["index"])}</div>'
                f'<div><div class="ws-milestone-title">{esc(st["title"])}</div>'
                f"{cap_html}</div></div>"
            )

        hero_step = steps[-1]
        fg_steps = steps[:-1]
        hero_bg = build_bg_style(hero_step.get("bg", ""))
        hero_attr = f' style="{hero_bg}"' if hero_bg else ""
        hero_img_style = build_img_style(hero_step["fit"], hero_step["position"])
        hero_contain = " is-contain" if hero_step["fit"] == "contain" else ""

        arena_parts: list[str] = [
            f'<div class="ws-stage-num ws-stage-num-hero">{esc(hero_step["index"])}</div>',
            f'<div class="ws-stage-plane ws-stage-hero{hero_contain}"{hero_attr}>'
            f'<img src="{esc(hero_step["image"])}" alt="{esc(hero_step["title"])}" style="{hero_img_style}"></div>',
        ]
        for fi, st in enumerate(fg_steps):
            fg_bg = build_bg_style(st.get("bg", ""))
            fg_attr = f' style="{fg_bg}"' if fg_bg else ""
            fg_img_style = build_img_style(st["fit"], st["position"])
            fg_contain = " is-contain" if st["fit"] == "contain" else ""
            arena_parts.append(
                f'<div class="ws-stage-num ws-stage-num-{fi}">{esc(st["index"])}</div>'
            )
            arena_parts.append(
                f'<div class="ws-stage-plane ws-stage-fg-{fi}{fg_contain}"{fg_attr}>'
                f'<img src="{esc(st["image"])}" alt="{esc(st["title"])}" style="{fg_img_style}"></div>'
            )

        return f"""
        <div class="ws-staged-left">
          {brand_html}
          {render_title_html(card["title"])}
          {render_subtitle_html(card["subtitle"])}
          <div class="ws-milestones">{"".join(milestones)}</div>
          {render_footer_html(card["footer"])}
        </div>
        <div class="ws-staged-arena">
          {"".join(arena_parts)}
        </div>
        """

    use_vertical = orient == "portrait" and (len(steps) == 3 or card.get("isTall", False))
    use_grid = (orient == "square") or (orient == "portrait" and not use_vertical)

    h_arrow = (
        '<div class="ws-arrow" aria-hidden="true">'
        '<svg width="20" height="20" viewBox="0 0 20 20" fill="none">'
        '<path d="M3 10H16.5M16.5 10L12 5.5M16.5 10L12 14.5" '
        'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
    )
    v_arrow = (
        '<div class="ws-arrow" aria-hidden="true">'
        '<svg width="20" height="20" viewBox="0 0 20 20" fill="none">'
        '<path d="M10 3V16.5M10 16.5L5.5 12M10 16.5L14.5 12" '
        'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
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
        contain_cls = " is-contain" if step["fit"] == "contain" else ""
        if use_vertical:
            parts.append(
                f'<div class="ws-step-card">'
                f'<div class="ws-step-media{contain_cls}"{media_attr}>'
                f'<img src="{esc(step["image"])}" alt="{esc(step["title"])}" style="{img_style}"></div>'
                f'<div class="ws-step-body">'
                f'<div class="wc-row-num">{esc(step["index"])}</div>'
                f'<div class="ws-step-head">'
                f'<span class="ws-step-title">{esc(step["title"])}</span></div>'
                f"{caption_html}</div></div>"
            )
        else:
            parts.append(
                f'<div class="ws-step-unit">'
                f'<div class="sc-occluded-header ws-step-behind">'
                f'<span class="sc-occluded-label is-accent ws-step-num">{esc(step["index"])}</span>'
                f"</div>"
                f'<div class="ws-step-card">'
                f'<div class="ws-step-media{contain_cls}"{media_attr}>'
                f'<img src="{esc(step["image"])}" alt="{esc(step["title"])}" style="{img_style}"></div>'
                f'<div class="ws-step-body">'
                f'<div class="ws-step-head">'
                f'<span class="ws-step-title">{esc(step["title"])}</span></div>'
                f"{caption_html}</div></div></div>"
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


def build_split_compare_body(card: dict[str, Any]) -> str:
    before = card["before"]
    after = card["after"]
    split_pct = card.get("splitPercent", 50.0)
    brand_html = render_brand_html(card["brand"])

    before_lbl_cls = "sp-sculp-label sp-sculp-before is-cjk" if has_cjk(before["label"]) else "sp-sculp-label sp-sculp-before"
    after_lbl_cls = "sp-sculp-label sp-sculp-after is-cjk" if has_cjk(after["label"]) else "sp-sculp-label sp-sculp-after"

    frame_bg = build_bg_style(after.get("bg") or before.get("bg") or "")
    frame_attr = f' style="{frame_bg}"' if frame_bg else ""
    left_img_style = build_img_style(before["fit"], before["position"])
    right_img_style = build_img_style(after["fit"], after["position"])

    caption_row = ""
    if before.get("caption") or after.get("caption"):
        left_cap = f'<span class="sp-caption sp-caption-left">{esc(before["caption"])}</span>' if before.get("caption") else "<span></span>"
        right_cap = f'<span class="sp-caption sp-caption-right">{esc(after["caption"])}</span>' if after.get("caption") else "<span></span>"
        caption_row = f'<div class="sp-caption-row">{left_cap}{right_cap}</div>'

    return f"""
    <div class="sp-bg-shift" style="--split-pct:{split_pct:g}%;" aria-hidden="true"></div>
    <div class="sp-seam-ray" style="--split-pct:{split_pct:g}%;" aria-hidden="true"></div>
    <header class="sp-header">
      {brand_html}
      {render_title_html(card["title"])}
      {render_subtitle_html(card["subtitle"])}
    </header>
    <div class="sp-stage" style="--split-pct:{split_pct:g}%;">
      <div class="sp-column">
        <div class="sp-occluded-row" aria-hidden="true">
          <span class="{before_lbl_cls}">{esc(before["label"])}</span>
          <span class="{after_lbl_cls}">{esc(after["label"])}</span>
        </div>
        <div class="sp-frame-wrap">
          <div class="sp-frame"{frame_attr}>
            <img class="sp-img sp-img-left" src="{esc(before["image"])}" alt="{esc(before["label"])}" style="{left_img_style}">
            <img class="sp-img sp-img-right" src="{esc(after["image"])}" alt="{esc(after["label"])}" style="{right_img_style}">
            <div class="sp-divider-line" aria-hidden="true"></div>
            <div class="sp-divider-handle" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M7.5 6L3.5 10L7.5 14" stroke="#0f172a" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M12.5 6L16.5 10L12.5 14" stroke="{esc(card["accent"])}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </div>
          </div>
        </div>
        {caption_row}
      </div>
    </div>
    {render_footer_html(card["footer"])}
    """


def build_device_pair_body(card: dict[str, Any]) -> str:
    desktop = card["desktop"]
    mobile = card["mobile"]
    brand_html = render_brand_html(card["brand"])

    desk_lbl_cls = "dp-sculp-tag dp-sculp-desktop is-cjk" if has_cjk(desktop["label"]) else "dp-sculp-tag dp-sculp-desktop"
    mob_lbl_cls = "dp-sculp-tag dp-sculp-mobile is-cjk" if has_cjk(mobile["label"]) else "dp-sculp-tag dp-sculp-mobile"

    desk_bg = build_bg_style(desktop.get("bg", ""))
    desk_attr = f' style="{desk_bg}"' if desk_bg else ""
    desk_img_style = build_img_style(desktop["fit"], desktop["position"])

    mob_bg = build_bg_style(mobile.get("bg", ""))
    mob_attr = f' style="{mob_bg}"' if mob_bg else ""
    mob_img_style = build_img_style(mobile["fit"], mobile["position"])

    safe_top_color = mobile.get("safeTopBg") or mobile.get("bg") or ""
    if safe_top_color:
        safe_top_html = (
            f'<div class="dp-mobile-safe-top" style="background:{esc(safe_top_color)};">'
            f'<div class="dp-dynamic-island"></div></div>'
        )
    else:
        safe_top_html = (
            '<div class="dp-mobile-safe-top is-overlay">'
            '<div class="dp-dynamic-island"></div></div>'
        )

    footer_html = (
        f'<div class="dp-footer-meta">{esc_multiline(card["footer"])}</div>'
        if card.get("footer")
        else ""
    )

    return f"""
    <header class="dp-header">
      {brand_html}
      {render_title_html(card["title"])}
      {render_subtitle_html(card["subtitle"])}
    </header>
    <div class="{desk_lbl_cls}" aria-hidden="true">{esc(desktop["label"])}</div>
    <div class="{mob_lbl_cls}" aria-hidden="true">{esc(mobile["label"])}</div>
    <div class="dp-desktop-frame"{desk_attr}>
      <img src="{esc(desktop["image"])}" alt="{esc(desktop["label"])}" style="{desk_img_style}">
    </div>
    <div class="dp-mobile-wrap">
      <div class="dp-mobile-screen"{mob_attr}>
        {safe_top_html}
        <div class="dp-mobile-img-wrap">
          <img src="{esc(mobile["image"])}" alt="{esc(mobile["label"])}" style="{mob_img_style}">
        </div>
        <div class="dp-mobile-home-bar" aria-hidden="true"></div>
        <div class="dp-mobile-rim" aria-hidden="true"></div>
      </div>
    </div>
    {footer_html}
    """


def format_version_html(version: str) -> str:
    """Format a version string like 'v0.4' with muted 'v' prefix and accent dots."""
    text = version.strip()
    if not text:
        return ""
    parts: list[str] = []
    if len(text) > 1 and text[0] in ("v", "V") and text[1].isdigit():
        parts.append(f'<span class="cl-version-v">{esc(text[0])}</span>')
        rest = text[1:]
    else:
        rest = text
    for ch in rest:
        if ch == ".":
            parts.append('<span class="cl-version-dot">.</span>')
        else:
            parts.append(esc(ch))
    return "".join(parts)


def build_changelog_card_body(card: dict[str, Any]) -> str:
    shot = card["shot"]
    highlights = card["highlights"]
    brand_html = render_brand_html(card["brand"])

    title_cls = "cl-release-title is-cjk" if has_cjk(card["title"]) else "cl-release-title"
    hl_items: list[str] = []
    for hl in highlights:
        desc_html = f'<div class="cl-hl-desc">{esc(hl["desc"])}</div>' if hl.get("desc") else ""
        hl_items.append(
            f'<div class="cl-hl-item">'
            f'<div class="cl-hl-idx">{esc(hl["index"])}</div>'
            f'<div><div class="cl-hl-title">{esc(hl["title"])}</div>{desc_html}</div>'
            f"</div>"
        )

    left_footer_html = (
        f'<div class="cl-left-footer">{esc_multiline(card["footer"])}</div>'
        if card.get("footer")
        else ""
    )

    tag_text = shot.get("tag", "")
    tag_cls = "cl-sculp-tag is-cjk" if has_cjk(tag_text) else "cl-sculp-tag"
    tag_html = f'<div class="{tag_cls}" aria-hidden="true">{esc(tag_text)}</div>' if tag_text else ""

    shot_bg = build_bg_style(shot.get("bg", ""))
    shot_attr = f' style="{shot_bg}"' if shot_bg else ""
    shot_img_style = build_img_style(shot["fit"], shot["position"])

    shot_caption_html = ""
    if shot.get("metaLeft") or shot.get("metaRight"):
        shot_caption_html = (
            f'<div class="cl-shot-caption">'
            f'<span>{esc(shot.get("metaLeft", ""))}</span>'
            f'<span>{esc(shot.get("metaRight", ""))}</span>'
            f"</div>"
        )

    return f"""
    <aside class="cl-left-col">
      <div class="cl-top-block">
        {brand_html}
        <div class="cl-version-stage">
          <div class="cl-version-num">{format_version_html(card["version"])}</div>
        </div>
        <h1 class="{title_cls}">{esc_multiline(card["title"])}</h1>
      </div>
      <div class="cl-highlights count-{len(highlights)}">{"".join(hl_items)}</div>
      {left_footer_html}
    </aside>
    <div class="cl-shot-stage">
      {tag_html}
      <div class="cl-shot-frame"{shot_attr}>
        <img src="{esc(shot["image"])}" alt="{esc(card["title"])}" style="{shot_img_style}">
      </div>
      {shot_caption_html}
    </div>
    """


def build_blueprint_backdrop_html(card: dict[str, Any]) -> str:
    """Render architectural drafting grid, registration crosshairs, and perimeter coordinate ticks."""
    w, h = card["width"], card["height"]
    accent = card["accent"]
    crosshairs: list[str] = []
    for gx in range(160, w, 160):
        for gy in range(160, h, 160):
            crosshairs.append(
                f'<g stroke="{esc(accent)}" stroke-opacity="0.30" stroke-width="1">'
                f'<line x1="{gx - 5}" y1="{gy}" x2="{gx + 5}" y2="{gy}"/>'
                f'<line x1="{gx}" y1="{gy - 5}" x2="{gx}" y2="{gy + 5}"/>'
                f"</g>"
            )
    grid_label = card.get("gridLabel") or "SCALE 2:1 · GRID 40PX"
    tick_count = 5 if w >= 1200 else 3
    ticks: list[str] = []
    for i in range(tick_count):
        tx = int(round((w / tick_count) * (i + 0.5) - 16))
        ticks.append(f'<div class="bp-axis-x" style="left:{tx}px;" aria-hidden="true">X·{i + 1:02d}</div>')

    return (
        '<div class="bp-grid-bg" aria-hidden="true"></div>'
        f'<svg class="bp-drafting-svg" viewBox="0 0 {w} {h}" aria-hidden="true">'
        f'<rect x="28" y="24" width="{w - 56}" height="{h - 58}" fill="none" stroke="rgba(15, 23, 42, 0.12)" stroke-width="1"/>'
        f'{"".join(crosshairs)}'
        "</svg>"
        f'<div class="bp-margin-top" aria-hidden="true">{esc(grid_label)}</div>'
        f'{"".join(ticks)}'
    )


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
    elif layout == "split-compare":
        body_inner = build_split_compare_body(card)
    elif layout == "device-pair":
        body_inner = build_device_pair_body(card)
    elif layout == "changelog-card":
        body_inner = build_changelog_card_body(card)
    else:
        raise ValueError(f"不支持的版式：{layout}")

    variant_cls = (
        f" variant-{card['variant']}" if layout == "gallery-wall" else ""
    )
    arrange_cls = (
        f" arrange-{card['arrangement']}" if layout == "window-callout" else ""
    )
    wc_mode_cls = (
        f" wc-mode-{card.get('wcMode', 'standard')}" if layout == "window-callout" else ""
    )
    ba_orient_cls = (
        f" ba-orient-{card['compareOrientation']}" if layout == "before-after" else ""
    )
    ws_style_cls = (
        f" ws-style-{card.get('wsStyle', 'row')}" if layout == "workflow-steps" else ""
    )
    stage_palette_cls = (
        f" stage-palette-{card.get('stagePalette', 'twilight')}"
        if card["theme"] == "wallpaper-stage"
        else ""
    )
    tilt_cls = " is-tilted" if card.get("tilt", False) else ""
    theme_cls = f"theme-{card['theme']}"
    display_cls = f"display-{card['displayStyle']}"
    tall_cls = " is-tall" if card.get("isTall") else ""
    extra_script = (
        CONNECTOR_SCRIPT if layout in ("section-assembly", "window-callout") else ""
    )
    blueprint_backdrop = (
        build_blueprint_backdrop_html(card) if card["theme"] == "blueprint-grid" else ""
    )
    grain_freq = THEME_GRAIN_FREQUENCY.get(card["theme"], "0.86")
    w, h = card["width"], card["height"]
    grain_svg = (
        f'<svg class="sc-grain" aria-hidden="true" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
        f'<filter id="sc-grain-filter">'
        f'<feTurbulence type="fractalNoise" baseFrequency="{grain_freq}" numOctaves="3" stitchTiles="stitch"/>'
        f'<feColorMatrix type="saturate" values="0"/>'
        f"</filter>"
        f'<rect width="{w}" height="{h}" filter="url(#sc-grain-filter)"/>'
        f"</svg>"
    )

    html_lang = "zh-CN" if is_cjk_card(card) else "en"

    return f"""<!doctype html>
<html lang="{html_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(card["id"])}</title>
  <link rel="stylesheet" href="{esc(fonts_css_href)}">
  <style>{COMMON_CSS}</style>
</head>
<body class="{esc(theme_cls)}{esc(stage_palette_cls)}">
  <main
    class="sc-canvas {esc(theme_cls)}{esc(stage_palette_cls)} {esc(display_cls)} layout-{esc(layout)} orient-{esc(card["orientation"])}{tall_cls}{variant_cls}{arrange_cls}{wc_mode_cls}{ba_orient_cls}{ws_style_cls}{tilt_cls}"
    style="--canvas-w:{w}px;--canvas-h:{h}px;--accent:{esc(card["accent"])};--accent-ink:{esc(card["accentInk"])};"
  >
    {blueprint_backdrop}
    <div class="sc-studio-keylight" aria-hidden="true"></div>
    <div class="sc-ambient-bounce" aria-hidden="true"></div>
    {body_inner}
    {grain_svg}
  </main>
  {extra_script}
</body>
</html>
"""


def build_contact_sheet_html(
    results: list[dict[str, Any]],
    spec_name: str,
    *,
    is_cjk: bool = False,
) -> str:
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
        "wallpaper-stage": {
            "bg": "#141226",
            "ink": "#ffffff",
            "sub": "#c4bed6",
            "card": "#1c1934",
            "thumb": "#100e20",
            "border": "rgba(255,255,255,0.14)",
            "badge_bg": "rgba(255,255,255,0.10)",
            "badge_ink": "#e4def4",
            "dim": "#9c96b2",
            "link_line": "rgba(255,255,255,0.30)",
            "heading_font": "'Inter', 'PingFang SC', sans-serif",
        },
        "blueprint-grid": {
            "bg": "#f3f5f8",
            "ink": "#0b1220",
            "sub": "#475569",
            "card": "#ffffff",
            "thumb": "#e8edf4",
            "border": "rgba(15,23,42,0.14)",
            "badge_bg": "rgba(29,78,216,0.08)",
            "badge_ink": "#1d4ed8",
            "dim": "#64748b",
            "link_line": "rgba(29,78,216,0.34)",
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

    html_lang = "zh-CN" if is_cjk else "en"
    page_title = (
        f"展示卡片总览 · {esc(spec_name)}" if is_cjk else f"Showcase Cards · {esc(spec_name)}"
    )
    heading_text = "展示卡片总览" if is_cjk else "Showcase Contact Sheet"
    count_text = (
        f"{esc(spec_name)} · 共 {len(results)} 张卡片"
        if is_cjk
        else f"{esc(spec_name)} · {len(results)} cards"
    )

    return f"""<!doctype html>
<html lang="{html_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
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
    <h1>{heading_text}</h1>
    <div class="cs-sub">{count_text}</div>
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
    scale_err = validate_scale(args.scale)
    if scale_err:
        print(
            json.dumps(
                {"ok": False, "errors": [scale_err]},
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

    out_dir = Path(args.out_dir).expanduser().resolve()
    html_dir = out_dir / "html"
    png_dir = out_dir / "png"
    fonts_css_path = (html_dir / "fonts.css").resolve()
    contact_sheet = (out_dir / "index.html").resolve()

    input_paths: set[Path] = set()
    for c in all_cards:
        for p_str in c.get("_inputPaths", []):
            input_paths.add(Path(p_str).resolve())

    overwrite_errors: list[str] = []
    planned_outputs: list[Path] = [fonts_css_path, contact_sheet]
    for card in cards:
        planned_outputs.append((html_dir / f"{card['id']}.html").resolve())
        planned_outputs.append((png_dir / f"{card['id']}.png").resolve())
    for out_file in planned_outputs:
        if out_file in input_paths:
            overwrite_errors.append(
                f"输出路径与输入素材冲突，已拒绝覆盖原图：{out_file}"
            )
    if overwrite_errors:
        print(
            json.dumps(
                {"ok": False, "spec": str(spec_path), "errors": overwrite_errors},
                ensure_ascii=False,
                indent=2,
            )
        )
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

    html_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)

    atomic_write_text(fonts_css_path, build_inlined_fonts_css())

    results: list[dict[str, Any]] = []
    try:
        with ChromeSession(chrome_path) as browser:
            for card in cards:
                card_html_path = html_dir / f"{card['id']}.html"
                card_png_path = png_dir / f"{card['id']}.png"
                atomic_write_text(card_html_path, build_card_html(card, "fonts.css"))
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
                        "lang": card["lang"],
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
                    "lang": card["lang"],
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

    cjk_count = sum(1 for item in sheet_entries if item.get("lang") == "zh")
    sheet_is_cjk = bool(sheet_entries) and (cjk_count * 2 >= len(sheet_entries))
    atomic_write_text(
        contact_sheet,
        build_contact_sheet_html(sheet_entries, spec_path.name, is_cjk=sheet_is_cjk),
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
    scale_err = validate_scale(args.scale)
    if scale_err:
        print(json.dumps({"ok": False, "errors": [scale_err]}, ensure_ascii=False, indent=2))
        return 1

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

    out_path = Path(args.out).expanduser().resolve()
    if out_path == html_path:
        print(
            json.dumps(
                {"ok": False, "errors": [f"输出路径与输入文件冲突，已拒绝覆盖：{out_path}"]},
                ensure_ascii=False,
                indent=2,
            )
        )
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
    validation_errors: list[str] = []
    scale_err = validate_scale(args.scale)
    if scale_err:
        validation_errors.append(scale_err)
    if not isinstance(args.wait_ms, int) or not (0 <= args.wait_ms <= 15000):
        validation_errors.append(
            f"--wait-ms 必须在 [0, 15000] 毫秒范围内，收到 {args.wait_ms}。"
        )
    if validation_errors:
        print(
            json.dumps({"ok": False, "errors": validation_errors}, ensure_ascii=False, indent=2)
        )
        return 1

    target = args.target.strip()
    local_path: Path | None = None
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

    out_path = Path(args.out).expanduser().resolve()
    if local_path is not None and out_path == local_path:
        print(
            json.dumps(
                {"ok": False, "errors": [f"输出路径与输入文件冲突，已拒绝覆盖：{out_path}"]},
                ensure_ascii=False,
                indent=2,
            )
        )
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
        description="Render editorial product showcase cards (warm-paper, dark-studio, cool-gallery) to @2x PNGs."
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
