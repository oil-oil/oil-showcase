#!/usr/bin/env python3
"""Unit and real headless-Chrome integration tests for oil-showcase."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = SKILL_ROOT / "scripts" / "render_cards.py"


def make_png(path: Path, width: int, height: int, rgba: tuple[int, int, int, int]) -> None:
    """Create a valid RGBA PNG using Python stdlib only."""
    r, g, b, a = rgba
    raw_rows = bytearray()
    row = bytes([r, g, b, a]) * width
    for _ in range(height):
        raw_rows.append(0)  # Filter type 0 (None)
        raw_rows.extend(row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"Not a valid PNG: {path}")
    return struct.unpack(">II", header[16:24])


def run_cli(*args: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"CLI did not output valid JSON (rc={proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        ) from exc
    return proc.returncode, payload


class RenderCardsValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="osc-val-")
        self.tmp_dir = Path(self._tmp.name)
        self.img = self.tmp_dir / "sample.png"
        make_png(self.img, 64, 40, (42, 78, 122, 255))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_doctor_succeeds(self) -> None:
        rc, payload = run_cli("doctor")
        self.assertEqual(rc, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["chrome"]["found"])
        self.assertTrue(payload["fonts"]["ready"])

    def test_no_micro_font_sizes_below_14px(self) -> None:
        import re
        src = SCRIPT_PATH.read_text(encoding="utf-8")
        sizes = [float(m.group(1)) for m in re.finditer(r"font-size:\s*([0-9]+(?:\.[0-9]+)?)px", src)]
        self.assertGreater(len(sizes), 20)
        too_small = [s for s in sizes if s < 14.0]
        self.assertEqual(too_small, [], f"Found unreadable micro font-sizes < 14px: {too_small}")

    def test_security_no_allow_file_access_from_files_flag(self) -> None:
        src = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("--allow-file-access-from-files", src)
        self.assertIn("POSITION_RE", src)

    def test_malformed_json_rejected(self) -> None:
        spec = self.tmp_dir / "broken.json"
        spec.write_text("{not valid json", encoding="utf-8")
        rc, payload = run_cli("render", str(spec), "--out-dir", str(self.tmp_dir / "out"))
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("JSON" in err for err in payload["errors"]))

    def test_missing_image_rejected(self) -> None:
        spec = self.tmp_dir / "missing.json"
        spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "card-missing",
                            "layout": "module-bento",
                            "title": "Modules",
                            "items": [
                                {"image": "sample.png"},
                                {"image": "does-not-exist.png"},
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc, payload = run_cli("render", str(spec), "--out-dir", str(self.tmp_dir / "out"))
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("does-not-exist.png" in err for err in payload["errors"]))

    def test_invalid_layout_and_ratio_rejected(self) -> None:
        spec = self.tmp_dir / "invalid.json"
        spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "bad-layout",
                            "layout": "unknown-grid",
                            "title": "Oops",
                        },
                        {
                            "id": "bad-ratio",
                            "layout": "module-bento",
                            "ratio": "99:1",
                            "title": "Oops",
                            "items": [{"image": "sample.png"}, {"image": "sample.png"}],
                        },
                        {
                            "id": "bad-accent",
                            "layout": "module-bento",
                            "accent": "red",
                            "title": "Oops",
                            "items": [{"image": "sample.png"}, {"image": "sample.png"}],
                        },
                        {
                            "id": "bad-fit-bg",
                            "layout": "module-bento",
                            "title": "Oops",
                            "items": [
                                {"image": "sample.png", "fit": "stretch", "bg": "not-a-color"},
                                {"image": "sample.png"},
                            ],
                        },
                        {
                            "id": "bad-theme",
                            "layout": "before-after",
                            "theme": "neon-glow",
                            "title": "Oops",
                            "before": {"image": "sample.png"},
                            "after": {"image": "sample.png"},
                        },
                        {
                            "id": "bad-display",
                            "layout": "before-after",
                            "displayStyle": "comic",
                            "title": "Oops",
                            "before": {"image": "sample.png"},
                            "after": {"image": "sample.png"},
                        },
                        {
                            "id": "bad-arrangement",
                            "layout": "window-callout",
                            "arrangement": "diagonal",
                            "title": "Oops",
                            "window": {"image": "sample.png"},
                            "callouts": [
                                {"title": "A", "image": "sample.png", "targetX": 20, "targetY": 50},
                                {"title": "B", "image": "sample.png", "targetX": 80, "targetY": 50},
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc, payload = run_cli("render", str(spec), "--out-dir", str(self.tmp_dir / "out"))
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])
        self.assertGreaterEqual(len(payload["errors"]), 8)

    def test_font_license_and_notices_present(self) -> None:
        fonts_dir = SKILL_ROOT / "assets" / "fonts"
        ofl = fonts_dir / "OFL.txt"
        notices = fonts_dir / "THIRD-PARTY-NOTICES.txt"
        self.assertTrue(ofl.is_file(), "Missing OFL.txt in assets/fonts/")
        self.assertTrue(notices.is_file(), "Missing THIRD-PARTY-NOTICES.txt in assets/fonts/")
        ofl_text = ofl.read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", ofl_text)
        self.assertIn("PERMISSION & CONDITIONS", ofl_text)
        notices_text = notices.read_text(encoding="utf-8")
        for required in ("Inter", "JetBrains Mono", "Playfair Display", "SIL Open Font License 1.1"):
            self.assertIn(required, notices_text)

    def test_position_css_injection_rejected(self) -> None:
        spec = self.tmp_dir / "css-inject.json"
        spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "bad-pos-1",
                            "layout": "before-after",
                            "title": "Security check",
                            "before": {
                                "image": "sample.png",
                                "position": "center; background:url(http://evil.example/a)",
                            },
                            "after": {
                                "image": "sample.png",
                                "position": "top); color:red",
                            },
                        },
                        {
                            "id": "bad-pos-2",
                            "layout": "module-bento",
                            "title": "Security check 2",
                            "items": [
                                {"image": "sample.png", "position": "url(https://evil.example/x)"},
                                {"image": "sample.png", "position": "left 10px top 20px"},
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc, payload = run_cli("render", str(spec), "--out-dir", str(self.tmp_dir / "out"))
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])
        pos_errors = [err for err in payload["errors"] if "position" in err]
        self.assertEqual(len(pos_errors), 3, payload["errors"])

    def test_invalid_scale_and_wait_ms_rejected(self) -> None:
        fixture = self.tmp_dir / "dummy.html"
        fixture.write_text("<!doctype html><html><body>ok</body></html>", encoding="utf-8")

        for bad_scale in ("0", "-1", "5"):
            rc_s, payload_s = run_cli(
                "shoot", str(fixture), "--out", str(self.tmp_dir / "s.png"), "--scale", bad_scale
            )
            self.assertEqual(rc_s, 1, payload_s)
            self.assertFalse(payload_s["ok"])
            self.assertTrue(any("--scale" in e for e in payload_s["errors"]))

            rc_c, payload_c = run_cli(
                "capture", str(fixture), "--out", str(self.tmp_dir / "c.png"), "--scale", bad_scale
            )
            self.assertEqual(rc_c, 1, payload_c)
            self.assertFalse(payload_c["ok"])
            self.assertTrue(any("--scale" in e for e in payload_c["errors"]))

        for bad_wait in ("-1", "15001", "99999"):
            rc_w, payload_w = run_cli(
                "capture",
                str(fixture),
                "--out",
                str(self.tmp_dir / "w.png"),
                "--wait-ms",
                bad_wait,
            )
            self.assertEqual(rc_w, 1, payload_w)
            self.assertFalse(payload_w["ok"])
            self.assertTrue(any("--wait-ms" in e for e in payload_w["errors"]))

    def test_input_image_overwrite_guard(self) -> None:
        out_dir = self.tmp_dir / "guard-out"
        input_png = out_dir / "png" / "conflict-card.png"
        make_png(input_png, 80, 60, (120, 40, 60, 255))
        original_bytes = input_png.read_bytes()

        spec = self.tmp_dir / "overwrite.json"
        spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "conflict-card",
                            "layout": "before-after",
                            "title": "Overwrite check",
                            "before": {"image": str(input_png)},
                            "after": {"image": "sample.png"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc, payload = run_cli("render", str(spec), "--out-dir", str(out_dir))
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("拒绝覆盖" in err for err in payload["errors"]), payload["errors"])
        self.assertEqual(input_png.read_bytes(), original_bytes)

    def test_invalid_before_after_orientation_and_width_percent_rejected(self) -> None:
        spec = self.tmp_dir / "bad-ba-stacked.json"
        spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "bad-ba-orient",
                            "layout": "before-after",
                            "orientation": "diagonal",
                            "title": "Invalid orient",
                            "before": {"image": "sample.png"},
                            "after": {"image": "sample.png", "widthPercent": 20},
                        },
                        {
                            "id": "bad-ba-wp-high",
                            "layout": "before-after",
                            "orientation": "stacked",
                            "title": "Invalid wp high",
                            "before": {"image": "sample.png"},
                            "after": {"image": "sample.png", "widthPercent": 120},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc, payload = run_cli("render", str(spec), "--out-dir", str(self.tmp_dir / "out"))
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("orientation" in e for e in payload["errors"]), payload["errors"])
        wp_errors = [e for e in payload["errors"] if "widthPercent" in e]
        self.assertEqual(len(wp_errors), 2, payload["errors"])


class RenderCardsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="osc-int-")
        cls.tmp_dir = Path(cls._tmp.name)
        cls.assets = cls.tmp_dir / "assets"
        make_png(cls.assets / "icon.png", 32, 32, (27, 28, 25, 255))
        make_png(cls.assets / "bg.png", 320, 200, (212, 224, 236, 255))
        make_png(cls.assets / "ui.png", 320, 200, (250, 250, 248, 255))
        make_png(cls.assets / "fg.png", 320, 200, (32, 128, 178, 130))
        make_png(cls.assets / "hero.png", 640, 400, (236, 242, 248, 255))
        make_png(cls.assets / "tall.png", 320, 680, (244, 241, 235, 255))
        for i in range(1, 7):
            make_png(
                cls.assets / f"mod-{i}.png",
                360,
                240,
                (40 + i * 25, 65 + i * 18, 90 + i * 15, 255),
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_render_all_archetypes_and_ratios(self) -> None:
        spec_data = {
            "brand": {
                "name": "Demo Studio",
                "tag": "showcase-kit",
                "icon": "assets/icon.png",
            },
            "cards": [
                {
                    "id": "01-layered-hero",
                    "layout": "layered-hero",
                    "ratio": "16:9",
                    "accent": "#2563eb",
                    "title": "How the hero\nis built.",
                    "subtitle": "Background plate, live UI, and transparent foreground.",
                    "eyebrow": "LIVE WEB DEMO · CAIRN",
                    "layers": [
                        {"label": "Background art", "image": "assets/bg.png"},
                        {"label": "Interactive UI", "image": "assets/ui.png"},
                        {"label": "Foreground glass", "image": "assets/fg.png", "transparent": True},
                    ],
                    "hero": {"image": "assets/hero.png", "tilt": True},
                },
                {
                    "id": "02-layered-bento-zh",
                    "layout": "layered-bento",
                    "ratio": "16:9",
                    "accent": "#a62639",
                    "title": "同一套画风，\n从首屏做到整页",
                    "subtitle": "底图、真实界面与前景叠加，右侧是 6 个页面模块。",
                    "layers": [
                        {"label": "底图", "image": "assets/bg.png"},
                        {"label": "界面", "image": "assets/ui.png"},
                        {"label": "前景", "image": "assets/fg.png", "transparent": True},
                    ],
                    "items": [{"image": f"assets/mod-{i}.png"} for i in range(1, 7)],
                },
                {
                    "id": "03-section-assembly",
                    "layout": "section-assembly",
                    "ratio": "16:9",
                    "accent": "#a62639",
                    "title": "Built section\nby section.",
                    "subtitle": "Each plate maps directly into its live section on the page.",
                    "eyebrow": "LIVE WEB DEMO · KOVA",
                    "footer": "4 source plates -> live HTML",
                    "sections": [
                        {
                            "caption": "HERO PLATE · HORIZON",
                            "summary": "Hero horizon + foreground",
                            "image": "assets/mod-1.png",
                            "targetY": 16,
                        },
                        {
                            "caption": "BENTO CARDS · 1:2 DUAL",
                            "summary": "Asymmetric dual plates",
                            "images": ["assets/mod-2.png", "assets/mod-3.png"],
                            "ratios": [1, 2],
                            "targetY": 44,
                        },
                        {
                            "caption": "MID SECTION · AERIAL",
                            "summary": "Aerial shift — zero repeat",
                            "image": "assets/mod-4.png",
                            "targetY": 72,
                        },
                        {
                            "caption": "CLOSING · TWILIGHT",
                            "summary": "Closing CTA & footer",
                            "image": "assets/mod-5.png",
                            "targetY": 92,
                        },
                    ],
                    "showcase": {"image": "assets/tall.png", "tilt": True},
                },
                {
                    "id": "04-module-bento-zh",
                    "layout": "module-bento",
                    "ratio": "16:9",
                    "accent": "#15803d",
                    "title": "不同画风里的常用业务模块",
                    "items": [
                        {"image": f"assets/mod-{i}.png", "label": f"Module {i}"}
                        for i in range(1, 7)
                    ],
                },
                {
                    "id": "05-gallery-tilted-pages",
                    "layout": "gallery-wall",
                    "variant": "tilted-pages",
                    "ratio": "16:9",
                    "title": "From hero\nto footer.",
                    "subtitle": "Multi-screen pages carried across every section.",
                    "footer": "Built live pages",
                    "items": [
                        {"image": "assets/tall.png", "label": "Plinth"},
                        {"image": "assets/tall.png", "label": "Ignis"},
                        {"image": "assets/tall.png", "label": "Aquarelle"},
                        {"image": "assets/tall.png", "label": "Atkins"},
                    ],
                },
                {
                    "id": "06-gallery-collage",
                    "layout": "gallery-wall",
                    "variant": "collage",
                    "ratio": "16:9",
                    "title": "Desktop & app\nconcepts.",
                    "subtitle": "Six product views composed on a warm paper board.",
                    "footer": "Design previews",
                    "items": [{"image": f"assets/mod-{i}.png"} for i in range(1, 7)],
                    "foreground": "assets/fg.png",
                },
                {
                    "id": "07-vertical-adaptation",
                    "layout": "layered-bento",
                    "ratio": "3:4",
                    "accent": "#2563eb",
                    "title": "竖屏卡片自动重排",
                    "subtitle": "3:4 画幅改为上文下图布局。",
                    "layers": [
                        {"label": "底图", "image": "assets/bg.png"},
                        {"label": "界面", "image": "assets/ui.png"},
                        {"label": "前景", "image": "assets/fg.png", "transparent": True},
                    ],
                    "items": [{"image": f"assets/mod-{i}.png"} for i in range(1, 5)],
                },
            ],
        }
        spec_path = self.tmp_dir / "cards.spec.json"
        spec_path.write_text(json.dumps(spec_data, ensure_ascii=False, indent=2), encoding="utf-8")

        out_dir = self.tmp_dir / "out-all"
        rc, payload = run_cli("render", str(spec_path), "--out-dir", str(out_dir), "--scale", "2")
        self.assertEqual(rc, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["rendered"], 7)

        contact_sheet = Path(payload["contactSheet"])
        self.assertTrue(contact_sheet.is_file())
        sheet_html = contact_sheet.read_text(encoding="utf-8")

        for card in payload["cards"]:
            png_path = Path(card["png"])
            html_path = Path(card["html"])
            self.assertTrue(png_path.is_file())
            self.assertTrue(html_path.is_file())
            self.assertGreater(png_path.stat().st_size, 5000)
            self.assertEqual(card["warnings"], [], f"Unexpected warnings for {card['id']}: {card['warnings']}")
            actual_w, actual_h = png_size(png_path)
            if card["ratio"] == "16:9":
                self.assertEqual((actual_w, actual_h), (3200, 1800))
            elif card["ratio"] == "3:4":
                self.assertEqual((actual_w, actual_h), (2160, 2880))
            self.assertIn(f"png/{card['id']}.png", sheet_html)

        # Verify --only preserves existing cards in contact sheet in spec order
        rc_preserve, payload_preserve = run_cli(
            "render",
            str(spec_path),
            "--out-dir",
            str(out_dir),
            "--scale",
            "2",
            "--only",
            "04-module-bento-zh",
        )
        self.assertEqual(rc_preserve, 0, payload_preserve)
        self.assertEqual(payload_preserve["rendered"], 1)
        self.assertEqual(payload_preserve["contactSheetCards"], 7)
        preserved_sheet = Path(payload_preserve["contactSheet"]).read_text(encoding="utf-8")
        positions = [preserved_sheet.index(f"png/{card['id']}.png") for card in spec_data["cards"]]
        self.assertEqual(positions, sorted(positions))

        # Verify --only into a fresh directory only lists rendered card
        out_only = self.tmp_dir / "out-only"
        rc_only, payload_only = run_cli(
            "render",
            str(spec_path),
            "--out-dir",
            str(out_only),
            "--scale",
            "1",
            "--only",
            "04-module-bento-zh",
        )
        self.assertEqual(rc_only, 0, payload_only)
        self.assertEqual(payload_only["rendered"], 1)
        self.assertEqual(payload_only["contactSheetCards"], 1)
        self.assertEqual(payload_only["cards"][0]["id"], "04-module-bento-zh")
        self.assertEqual(png_size(Path(payload_only["cards"][0]["png"])), (1600, 900))

    def test_portrait_badges_and_slot_fit_position_bg(self) -> None:
        spec_data = {
            "brand": {"name": "Dark App", "tag": "desktop"},
            "cards": [
                {
                    "id": "p-hero-nobadge",
                    "layout": "layered-hero",
                    "ratio": "3:4",
                    "accent": "#2fbf71",
                    "showIndexBadges": False,
                    "title": "竖屏无角标",
                    "subtitle": "验证 portrait showIndexBadges 与 contain + bg。",
                    "eyebrow": "DARK DESKTOP",
                    "footer": "LOCAL · READ-ONLY",
                    "layers": [
                        {"label": "一层", "image": "assets/bg.png", "fit": "contain", "position": "center", "bg": "#1d2026"},
                        {"label": "二层", "image": "assets/ui.png", "fit": "cover", "position": "top left", "bg": "#15181d"},
                    ],
                    "hero": {"image": "assets/hero.png", "tilt": False, "fit": "contain", "position": "center", "bg": "#1d2026"},
                },
                {
                    "id": "p-bento-nobadge",
                    "layout": "layered-bento",
                    "ratio": "3:4",
                    "accent": "#2fbf71",
                    "showIndexBadges": False,
                    "title": "竖屏双层无角标",
                    "layers": [
                        {"label": "底图", "image": "assets/bg.png", "bg": "#1d2026"},
                        {"label": "界面", "image": "assets/ui.png"},
                    ],
                    "items": [
                        {"image": "assets/mod-1.png", "fit": "contain", "position": "center", "bg": "#1d2026"},
                        {"image": "assets/mod-2.png", "fit": "cover", "position": "top left"},
                    ],
                },
                {
                    "id": "m-bento-indexed",
                    "layout": "module-bento",
                    "ratio": "16:9",
                    "accent": "#2fbf71",
                    "title": "模块编号与深色底",
                    "eyebrow": "MODULES",
                    "footer": "6 MODULES · CONTAIN",
                    "items": [
                        {"image": "assets/mod-1.png", "index": "01", "label": "工作区", "fit": "contain", "position": "top left", "bg": "#1d2026"},
                        {"image": "assets/mod-2.png", "index": "02", "caption": "提交图", "fit": "cover", "position": "center", "bg": "#111317"},
                    ],
                },
                {
                    "id": "s-assembly-objects",
                    "layout": "section-assembly",
                    "ratio": "16:9",
                    "accent": "#2fbf71",
                    "title": "切片对象参数",
                    "sections": [
                        {
                            "caption": "DUAL",
                            "summary": "双图独立 fit 与 bg",
                            "images": [
                                {"image": "assets/mod-1.png", "fit": "contain", "position": "top left", "bg": "#1d2026"},
                                {"image": "assets/mod-2.png", "fit": "cover", "position": "center", "bg": "#22252b"},
                            ],
                            "targetY": 28,
                        },
                        {
                            "caption": "SINGLE",
                            "summary": "单图继承段级参数",
                            "image": "assets/mod-3.png",
                            "fit": "contain",
                            "position": "center",
                            "bg": "#1d2026",
                            "targetY": 72,
                        },
                    ],
                    "showcase": {"image": "assets/tall.png", "fit": "contain", "position": "top center", "bg": "#1d2026"},
                },
                {
                    "id": "g-wall-pages-alias",
                    "layout": "gallery-wall",
                    "variant": "tilted-pages",
                    "ratio": "16:9",
                    "accent": "#2fbf71",
                    "title": "多页别名",
                    "pages": [
                        {"image": "assets/tall.png", "label": "Dark", "fit": "contain", "position": "top center", "bg": "#1d2026"},
                        {"image": "assets/tall.png", "label": "Light", "fit": "cover", "position": "top left", "bg": "#f8f7f4"},
                    ],
                },
            ],
        }
        spec_path = self.tmp_dir / "parity.spec.json"
        spec_path.write_text(json.dumps(spec_data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_dir = self.tmp_dir / "out-parity"
        rc, payload = run_cli("render", str(spec_path), "--out-dir", str(out_dir), "--scale", "1")
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["rendered"], 5)

        p_hero_html = (out_dir / "html" / "p-hero-nobadge.html").read_text(encoding="utf-8")
        self.assertNotIn('<span class="sc-layer-badge">', p_hero_html)
        self.assertIn("DARK DESKTOP", p_hero_html)
        self.assertIn("background:#1d2026;", p_hero_html)
        self.assertIn("object-fit:contain;object-position:center;", p_hero_html)

        p_bento_html = (out_dir / "html" / "p-bento-nobadge.html").read_text(encoding="utf-8")
        self.assertNotIn('<span class="sc-layer-badge">', p_bento_html)
        self.assertIn("background:#1d2026;", p_bento_html)

        m_bento_html = (out_dir / "html" / "m-bento-indexed.html").read_text(encoding="utf-8")
        self.assertIn('<span class="sc-idx">01</span><span>工作区</span>', m_bento_html)
        self.assertIn('<span class="sc-idx">02</span><span>提交图</span>', m_bento_html)
        self.assertNotIn('<div class="sc-eyebrow">', m_bento_html)
        self.assertIn("6 MODULES", m_bento_html)
        self.assertIn("background:#1d2026;", m_bento_html)

        sa_html = (out_dir / "html" / "s-assembly-objects.html").read_text(encoding="utf-8")
        self.assertIn("background:#1d2026;", sa_html)
        self.assertIn("object-fit:contain;object-position:top left;", sa_html)

        gw_html = (out_dir / "html" / "g-wall-pages-alias.html").read_text(encoding="utf-8")
        self.assertIn("background:#1d2026;", gw_html)
        self.assertIn("object-fit:contain;object-position:top center;", gw_html)

    def test_universal_themes_and_new_layouts(self) -> None:
        spec_data = {
            "theme": "dark-studio",
            "brand": {"name": "Studio Cut", "tag": "desktop workbench"},
            "cards": [
                {
                    "id": "01-window-callout-dark",
                    "layout": "window-callout",
                    "theme": "dark-studio",
                    "ratio": "16:9",
                    "accent": "#38bdf8",
                    "eyebrow": "DARK STUDIO · TIMELINE",
                    "title": "Everything on one timeline.",
                    "subtitle": "Zoomed waveform, inspector, and subtitle tracks mapped directly to the main window.",
                    "footer": "LOCAL NATIVE APP · ZERO CLOUD UPLOAD",
                    "window": {
                        "image": "assets/hero.png",
                        "label": "MAIN WORKSPACE · 16:10",
                        "fit": "cover",
                        "bg": "#14161b",
                    },
                    "callouts": [
                        {
                            "tag": "AUDIO",
                            "title": "Auto-silence trim",
                            "caption": "Detects pauses cleanly in milliseconds",
                            "image": "assets/mod-1.png",
                            "targetX": 24,
                            "targetY": 72,
                        },
                        {
                            "tag": "ZOOM",
                            "title": "Smart cursor follow",
                            "caption": "Smooth eased camera keyframes",
                            "image": "assets/mod-2.png",
                            "targetX": 52,
                            "targetY": 44,
                        },
                        {
                            "tag": "CAPTIONS",
                            "title": "Word-level subtitles",
                            "caption": "Editable timing synced to waveform",
                            "image": "assets/mod-3.png",
                            "targetX": 78,
                            "targetY": 78,
                        },
                    ],
                },
                {
                    "id": "02-window-callout-vertical",
                    "layout": "window-callout",
                    "theme": "dark-studio",
                    "ratio": "3:4",
                    "accent": "#38bdf8",
                    "title": "竖屏窗口标注解剖",
                    "subtitle": "3:4 自动切换为顶部窗口定位针 + 下方 2×2 局部放大卡。",
                    "window": {"image": "assets/hero.png", "bg": "#14161b"},
                    "callouts": [
                        {"title": "音轨波形", "caption": "自动识别气口", "image": "assets/mod-1.png", "targetX": 22, "targetY": 76},
                        {"title": "镜头缩放", "caption": "平滑聚焦光标", "image": "assets/mod-2.png", "targetX": 50, "targetY": 40},
                        {"title": "实时字幕", "caption": "逐词时间对齐", "image": "assets/mod-3.png", "targetX": 75, "targetY": 72},
                        {"title": "一键导出", "caption": "原生硬件加速", "image": "assets/mod-4.png", "targetX": 88, "targetY": 16},
                    ],
                },
                {
                    "id": "03-before-after-cool",
                    "layout": "before-after",
                    "theme": "cool-gallery",
                    "ratio": "16:9",
                    "accent": "#2563eb",
                    "eyebrow": "VISUAL COMPARISON",
                    "title": "One click to crisp framing.",
                    "subtitle": "Raw screen capture vs. balanced padding, shadow, and auto-zoom.",
                    "compareNote": "1440×900 CAPTURE · ZERO MANUAL KEYFRAMES",
                    "footer": "COOL GALLERY THEME",
                    "before": {
                        "label": "开启前",
                        "image": "assets/mod-1.png",
                        "caption": "原始全屏录屏，焦点分散且缺少留白",
                    },
                    "after": {
                        "label": "开启后",
                        "image": "assets/mod-5.png",
                        "caption": "自动居中聚焦，背景层次与阴影一次到位",
                    },
                },
                {
                    "id": "04-before-after-vertical",
                    "layout": "before-after",
                    "theme": "warm-paper",
                    "displayStyle": "serif",
                    "ratio": "3:4",
                    "accent": "#a62639",
                    "title": "竖屏前后对比",
                    "subtitle": "3:4 自动切换为上下堆叠与垂直过渡箭头。",
                    "before": {"label": "BEFORE", "image": "assets/mod-1.png", "caption": "Raw capture"},
                    "after": {"label": "AFTER", "image": "assets/mod-2.png", "caption": "Polished output"},
                },
                {
                    "id": "05-workflow-steps-cool",
                    "layout": "workflow-steps",
                    "theme": "cool-gallery",
                    "ratio": "16:9",
                    "accent": "#15803d",
                    "eyebrow": "END-TO-END WORKFLOW",
                    "title": "Record, refine, and ship.",
                    "subtitle": "Three calm steps from live screen capture to publish-ready video.",
                    "footer": "3-STEP VISUAL JOURNEY",
                    "steps": [
                        {"title": "Capture", "caption": "Pick a window or region in one click", "image": "assets/mod-1.png"},
                        {"title": "Refine", "caption": "Trim silences and adjust zoom curves", "image": "assets/mod-3.png"},
                        {"title": "Export", "caption": "Render crisp 60fps MP4 locally", "image": "assets/mod-5.png"},
                    ],
                },
                {
                    "id": "06-workflow-steps-vertical",
                    "layout": "workflow-steps",
                    "theme": "dark-studio",
                    "ratio": "3:4",
                    "accent": "#2fbf71",
                    "title": "三步完成本地交付",
                    "subtitle": "3:4 自动重排为竖向步骤流。",
                    "steps": [
                        {"title": "录制画面", "caption": "无边框锁定目标窗口", "image": "assets/mod-1.png"},
                        {"title": "精剪节奏", "caption": "一键删停顿与加字幕", "image": "assets/mod-2.png"},
                        {"title": "高清导出", "caption": "本地硬件加速成片", "image": "assets/mod-4.png"},
                    ],
                },
            ],
        }
        spec_path = self.tmp_dir / "universal.spec.json"
        spec_path.write_text(json.dumps(spec_data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_dir = self.tmp_dir / "out-universal"
        rc, payload = run_cli("render", str(spec_path), "--out-dir", str(out_dir), "--scale", "2")
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["rendered"], 6)
        self.assertEqual(payload["contactSheetCards"], 6)

        sheet_html = Path(payload["contactSheet"]).read_text(encoding="utf-8")
        # Majority theme is dark-studio -> contact sheet uses dark-studio surface and shows theme badges
        self.assertIn("background: #101114;", sheet_html)
        self.assertIn("dark-studio", sheet_html)
        self.assertIn("cool-gallery", sheet_html)
        self.assertIn("warm-paper", sheet_html)

        for card in payload["cards"]:
            self.assertEqual(card["warnings"], [], f"Unexpected warnings for {card['id']}: {card['warnings']}")
            w, h = png_size(Path(card["png"]))
            if card["ratio"] == "16:9":
                self.assertEqual((w, h), (3200, 1800))
            elif card["ratio"] == "3:4":
                self.assertEqual((w, h), (2160, 2880))

        wc_land_html = (out_dir / "html" / "01-window-callout-dark.html").read_text(encoding="utf-8")
        self.assertIn("theme-dark-studio display-sans", wc_land_html)
        self.assertIn("arrange-split", wc_land_html)
        self.assertIn("wc-split-body", wc_land_html)
        self.assertIn("wc-callouts-stack", wc_land_html)
        self.assertNotIn('<div class="sc-eyebrow">', wc_land_html)
        self.assertIn('<svg class="sc-connectors"', wc_land_html)
        self.assertIn("getRenderedImageRect", wc_land_html)
        self.assertIn("text-wrap: balance;", wc_land_html)

        wc_vert_html = (out_dir / "html" / "02-window-callout-vertical.html").read_text(encoding="utf-8")
        self.assertIn('class="wc-pin-marker" data-target-x="22.0" data-target-y="76.0"', wc_vert_html)
        self.assertIn("object-fit:contain;object-position:center;", wc_vert_html)

        ba_cool_html = (out_dir / "html" / "03-before-after-cool.html").read_text(encoding="utf-8")
        self.assertIn("theme-cool-gallery display-sans", ba_cool_html)
        self.assertIn('class="ba-arrow-bridge"', ba_cool_html)
        self.assertIn("sc-occluded-label", ba_cool_html)
        self.assertNotIn("ba-pill", ba_cool_html)
        self.assertNotIn('<div class="sc-eyebrow">', ba_cool_html)

        ba_warm_html = (out_dir / "html" / "04-before-after-vertical.html").read_text(encoding="utf-8")
        self.assertIn("theme-warm-paper display-serif", ba_warm_html)
        self.assertIn("sc-studio-keylight", ba_warm_html)
        self.assertIn("feTurbulence", ba_warm_html)

        ws_cool_html = (out_dir / "html" / "05-workflow-steps-cool.html").read_text(encoding="utf-8")
        self.assertIn('class="ws-arrow"', ws_cool_html)
        self.assertIn("sc-occluded-label", ws_cool_html)
        self.assertNotIn('<div class="sc-eyebrow">', ws_cool_html)

        # Verify window-callout split (default + focus reticle) AND stacked opt-in + 3:4 horizontal-split callouts
        default_wc_spec = self.tmp_dir / "wc-contain.spec.json"
        default_wc_spec.write_text(
            json.dumps(
                {
                    "theme": "dark-studio",
                    "cards": [
                        {
                            "id": "wc-auto-hug-split",
                            "layout": "window-callout",
                            "ratio": "16:9",
                            "accent": "#f4503c",
                            "title": "Auto-hug split window",
                            "window": {"image": "assets/hero.png"},
                            "callouts": [
                                {"title": "Left", "caption": "Zoomed region", "image": "assets/mod-1.png", "targetX": 18, "targetY": 65, "targetW": 22, "targetH": 18},
                                {"title": "Right", "caption": "Inspector panel", "image": "assets/mod-2.png", "targetX": 82, "targetY": 35},
                            ],
                        },
                        {
                            "id": "wc-stacked-optin",
                            "layout": "window-callout",
                            "arrangement": "stacked",
                            "ratio": "16:9",
                            "accent": "#38bdf8",
                            "title": "Stacked opt-in",
                            "window": {"image": "assets/hero.png"},
                            "callouts": [
                                {"title": "Left", "image": "assets/mod-1.png", "targetX": 20, "targetY": 70},
                                {"title": "Right", "image": "assets/mod-2.png", "targetX": 80, "targetY": 40},
                            ],
                        },
                        {
                            "id": "wc-3x4-hsplit",
                            "layout": "window-callout",
                            "ratio": "3:4",
                            "accent": "#2fbf71",
                            "title": "竖屏三卡横排",
                            "window": {"image": "assets/hero.png"},
                            "callouts": [
                                {"title": "侧栏", "caption": "文件分组", "image": "assets/mod-1.png", "targetX": 15, "targetY": 30},
                                {"title": "提交图", "caption": "分支拓扑", "image": "assets/mod-2.png", "targetX": 45, "targetY": 50},
                                {"title": "差异", "caption": "行号代码差异", "image": "assets/mod-3.png", "targetX": 78, "targetY": 68},
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        wc_out = self.tmp_dir / "out-wc-hug"
        rc_wc, payload_wc = run_cli("render", str(default_wc_spec), "--out-dir", str(wc_out), "--scale", "1")
        self.assertEqual(rc_wc, 0, payload_wc)
        for item in payload_wc["cards"]:
            self.assertEqual(item["warnings"], [], f"Unexpected warnings for {item['id']}: {item['warnings']}")
        wc_hug_html = (wc_out / "html" / "wc-auto-hug-split.html").read_text(encoding="utf-8")
        self.assertIn("arrange-split", wc_hug_html)
        self.assertIn('data-target-w="22.0" data-target-h="18.0"', wc_hug_html)
        self.assertIn("object-fit:contain;object-position:center;", wc_hug_html)
        wc_stacked_html = (wc_out / "html" / "wc-stacked-optin.html").read_text(encoding="utf-8")
        self.assertIn("arrange-stacked", wc_stacked_html)
        self.assertIn("wc-callouts-grid cols-2", wc_stacked_html)

    def test_9x16_and_4x3_ratios(self) -> None:
        spec_data = {
            "theme": "dark-studio",
            "brand": {"name": "Studio Cut", "tag": "mobile & gallery"},
            "cards": [
                {
                    "id": "01-steps-9x16",
                    "layout": "workflow-steps",
                    "ratio": "9:16",
                    "accent": "#f4503c",
                    "title": "四步竖屏闭环",
                    "subtitle": "9:16 全屏竖版将四步自动排为竖向步骤流。",
                    "steps": [
                        {"title": "录制", "caption": "锁定窗口无边框录制", "image": "assets/mod-1.png"},
                        {"title": "清理", "caption": "自动识别停顿气口", "image": "assets/mod-2.png"},
                        {"title": "字幕", "caption": "本机逐词对齐字幕", "image": "assets/mod-3.png"},
                        {"title": "导出", "caption": "原生硬件加速成片", "image": "assets/mod-4.png"},
                    ],
                },
                {
                    "id": "02-callout-9x16",
                    "layout": "window-callout",
                    "ratio": "9:16",
                    "accent": "#38bdf8",
                    "title": "全屏竖版窗口解剖",
                    "subtitle": "顶部主窗口与下方三张横排局部卡片。",
                    "window": {"image": "assets/hero.png", "bg": "#14161b"},
                    "callouts": [
                        {"title": "波形时间线", "caption": "自动识别静音片段", "image": "assets/mod-1.png", "targetX": 22, "targetY": 72},
                        {"title": "合成画布", "caption": "实时背景与圆角预览", "image": "assets/mod-2.png", "targetX": 50, "targetY": 40},
                        {"title": "检查器", "caption": "一键切换镜头与字幕", "image": "assets/mod-3.png", "targetX": 82, "targetY": 28},
                    ],
                },
                {
                    "id": "03-before-after-4x3",
                    "layout": "before-after",
                    "theme": "cool-gallery",
                    "ratio": "4:3",
                    "accent": "#2563eb",
                    "title": "4:3 对比展板",
                    "subtitle": "验证 4:3 画幅 @2x 精确输出 2880×2160。",
                    "before": {"label": "BEFORE", "image": "assets/mod-1.png", "caption": "原始输入"},
                    "after": {"label": "AFTER", "image": "assets/mod-5.png", "caption": "成片效果"},
                },
            ],
        }
        spec_path = self.tmp_dir / "ratios.spec.json"
        spec_path.write_text(json.dumps(spec_data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_dir = self.tmp_dir / "out-ratios"
        rc, payload = run_cli("render", str(spec_path), "--out-dir", str(out_dir), "--scale", "2")
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["rendered"], 3)

        expected = {
            "01-steps-9x16": (2160, 3840),
            "02-callout-9x16": (2160, 3840),
            "03-before-after-4x3": (2880, 2160),
        }
        for card in payload["cards"]:
            self.assertEqual(card["warnings"], [], f"Unexpected warnings for {card['id']}: {card['warnings']}")
            self.assertEqual(png_size(Path(card["png"])), expected[card["id"]])

        steps_html = (out_dir / "html" / "01-steps-9x16.html").read_text(encoding="utf-8")
        self.assertIn("is-tall", steps_html)
        self.assertIn("ws-flow is-vertical", steps_html)

    def test_shoot_and_capture_helpers(self) -> None:
        fixture_html = self.tmp_dir / "fixture.html"
        fixture_html.write_text(
            """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #f6f3ec; font-family: sans-serif; padding: 40px; }
    #module-card {
      width: 420px;
      height: 260px;
      border-radius: 16px;
      background: #1b1c19;
      color: #f6f3ec;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
    }
    .spacer { height: 1400px; }
  </style>
</head>
<body>
  <section id="module-card">Real Product Module</section>
  <div class="spacer"></div>
</body>
</html>
""",
            encoding="utf-8",
        )

        # 1. shoot at 4:3 @2x -> 2880x2160
        shoot_png = self.tmp_dir / "shot-4x3.png"
        rc_s, payload_s = run_cli(
            "shoot", str(fixture_html), "--out", str(shoot_png), "--ratio", "4:3", "--scale", "2"
        )
        self.assertEqual(rc_s, 0, payload_s)
        self.assertEqual(png_size(shoot_png), (2880, 2160))

        # 2. capture viewport 1200x800 @2x -> 2400x1600
        vp_png = self.tmp_dir / "capture-vp.png"
        rc_v, payload_v = run_cli(
            "capture",
            str(fixture_html),
            "--out",
            str(vp_png),
            "--viewport",
            "1200x800",
            "--scale",
            "2",
        )
        self.assertEqual(rc_v, 0, payload_v)
        self.assertEqual(png_size(vp_png), (2400, 1600))

        # 3. capture specific CSS selector #module-card (420x260 @2x -> 840x520)
        sel_png = self.tmp_dir / "capture-sel.png"
        rc_c, payload_c = run_cli(
            "capture",
            str(fixture_html),
            "--out",
            str(sel_png),
            "--viewport",
            "1200x800",
            "--selector",
            "#module-card",
            "--scale",
            "2",
        )
        self.assertEqual(rc_c, 0, payload_c)
        self.assertEqual(png_size(sel_png), (840, 520))

        # 4. capture full-page (height > 1600 because of 1400px spacer)
        fp_png = self.tmp_dir / "capture-full.png"
        rc_f, payload_f = run_cli(
            "capture",
            str(fixture_html),
            "--out",
            str(fp_png),
            "--viewport",
            "1200x800",
            "--full-page",
            "--scale",
            "1",
        )
        self.assertEqual(rc_f, 0, payload_f)
        fp_w, fp_h = png_size(fp_png)
        self.assertEqual(fp_w, 1200)
        self.assertGreater(fp_h, 1500)

    def test_before_after_cjk_and_english_default_labels(self) -> None:
        zh_spec = self.tmp_dir / "zh-ba.spec.json"
        zh_spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "ba-zh-auto",
                            "layout": "before-after",
                            "ratio": "16:9",
                            "title": "改版前后对比",
                            "before": {"image": "assets/mod-1.png"},
                            "after": {"image": "assets/mod-2.png"},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        zh_out = self.tmp_dir / "out-zh-ba"
        rc_zh, payload_zh = run_cli("render", str(zh_spec), "--out-dir", str(zh_out), "--scale", "1")
        self.assertEqual(rc_zh, 0, payload_zh)
        zh_html = (zh_out / "html" / "ba-zh-auto.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="zh-CN">', zh_html)
        self.assertIn('sc-occluded-label is-cjk">对比前</span>', zh_html)
        self.assertIn('sc-occluded-label is-cjk is-accent">对比后</span>', zh_html)
        self.assertNotIn("ba-pill", zh_html)
        zh_sheet = (zh_out / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="zh-CN">', zh_sheet)
        self.assertIn("展示卡片总览", zh_sheet)
        self.assertIn("共 1 张卡片", zh_sheet)

        en_spec = self.tmp_dir / "en-ba.spec.json"
        en_spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "ba-en-auto",
                            "layout": "before-after",
                            "ratio": "16:9",
                            "title": "Before and after comparison",
                            "before": {"image": "assets/mod-1.png"},
                            "after": {"image": "assets/mod-2.png"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        en_out = self.tmp_dir / "out-en-ba"
        rc_en, payload_en = run_cli("render", str(en_spec), "--out-dir", str(en_out), "--scale", "1")
        self.assertEqual(rc_en, 0, payload_en)
        en_html = (en_out / "html" / "ba-en-auto.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', en_html)
        self.assertIn('sc-occluded-label">BEFORE</span>', en_html)
        self.assertIn('sc-occluded-label is-accent">AFTER</span>', en_html)
        self.assertNotIn("ba-pill", en_html)
        en_sheet = (en_out / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', en_sheet)
        self.assertIn("Showcase Contact Sheet", en_sheet)
        self.assertIn("1 cards", en_sheet)

    def test_before_after_stacked_landscape_and_contain_stage_padding(self) -> None:
        spec_data = {
            "theme": "dark-studio",
            "brand": {"name": "Oil Studio", "tag": "timeline editor"},
            "cards": [
                {
                    "id": "ba-stacked-16x9",
                    "layout": "before-after",
                    "orientation": "stacked",
                    "ratio": "16:9",
                    "accent": "#2fbf71",
                    "title": "一键剪掉 8 处停顿气口",
                    "subtitle": "原始 32 秒波形与自动粗剪后的 24.9 秒成片时间轴同轴对比。",
                    "deltaBadge": "-7.1 s · 删去 8 处停顿",
                    "before": {
                        "label": "原片 · 0:32",
                        "image": "assets/hero.png",
                        "bg": "#15181d",
                    },
                    "after": {
                        "label": "精剪后 · 0:24.9",
                        "image": "assets/hero.png",
                        "widthPercent": 75,
                        "bg": "#15181d",
                    },
                },
                {
                    "id": "ws-contain-padded",
                    "layout": "workflow-steps",
                    "ratio": "16:9",
                    "accent": "#38bdf8",
                    "title": "三步端到端工作流",
                    "steps": [
                        {
                            "title": "采集",
                            "caption": "锁定窗口无边框抓图",
                            "image": "assets/mod-1.png",
                            "fit": "contain",
                            "bg": "#15181d",
                        },
                        {
                            "title": "编排",
                            "caption": "自动均匀留白舞台",
                            "image": "assets/mod-2.png",
                            "fit": "contain",
                            "bg": "#15181d",
                        },
                        {
                            "title": "交付",
                            "caption": "导出 @2x 高清展板",
                            "image": "assets/mod-3.png",
                            "fit": "contain",
                            "bg": "#15181d",
                        },
                    ],
                },
                {
                    "id": "mb-contain-padded",
                    "layout": "module-bento",
                    "ratio": "16:9",
                    "accent": "#38bdf8",
                    "title": "统一留白模块总览",
                    "items": [
                        {"image": "assets/mod-1.png", "label": "面板 A", "fit": "contain", "bg": "#15181d"},
                        {"image": "assets/mod-2.png", "label": "面板 B", "fit": "contain", "bg": "#15181d"},
                    ],
                },
            ],
        }
        spec_path = self.tmp_dir / "tension.spec.json"
        spec_path.write_text(json.dumps(spec_data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_dir = self.tmp_dir / "out-tension"
        rc, payload = run_cli("render", str(spec_path), "--out-dir", str(out_dir), "--scale", "2")
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["rendered"], 3)

        for card in payload["cards"]:
            self.assertEqual(card["warnings"], [], f"Unexpected warnings for {card['id']}: {card['warnings']}")
            self.assertEqual(png_size(Path(card["png"])), (3200, 1800))

        ba_html = (out_dir / "html" / "ba-stacked-16x9.html").read_text(encoding="utf-8")
        self.assertIn("ba-orient-stacked", ba_html)
        self.assertIn("ba-after-track", ba_html)
        self.assertIn("width:75%;", ba_html)
        self.assertIn("ba-reclaimed-zone", ba_html)
        self.assertIn("ba-trim-hairline", ba_html)
        self.assertIn("-7.1 s", ba_html)
        self.assertIn("删去 8 处停顿", ba_html)
        self.assertNotIn("ba-pill", ba_html)
        self.assertNotIn('ba-orient-stacked is-tilted"', ba_html)

        ws_html = (out_dir / "html" / "ws-contain-padded.html").read_text(encoding="utf-8")
        self.assertIn("ws-step-media is-contain", ws_html)

        mb_html = (out_dir / "html" / "mb-contain-padded.html").read_text(encoding="utf-8")
        self.assertIn("mb-card-media is-contain", mb_html)

    def test_v030_studio_lighting_tilt_controls_and_staged_workflow(self) -> None:
        spec_data = {
            "brand": {"name": "Studio Kit", "tag": "v0.3.0"},
            "cards": [
                {
                    "id": "studio-warm-ba-upright",
                    "layout": "before-after",
                    "theme": "warm-paper",
                    "ratio": "16:9",
                    "accent": "#0284c7",
                    "title": "Upright by default.",
                    "before": {"image": "assets/mod-1.png", "meta": "Base artwork"},
                    "after": {"image": "assets/mod-2.png", "meta": "Live product"},
                },
                {
                    "id": "studio-cool-ba-tilted",
                    "layout": "before-after",
                    "theme": "cool-gallery",
                    "ratio": "16:9",
                    "accent": "#0284c7",
                    "tilt": True,
                    "title": "Opt-in tilt comparison.",
                    "before": {"image": "assets/mod-1.png"},
                    "after": {"image": "assets/mod-2.png"},
                },
                {
                    "id": "studio-dark-wc-upright",
                    "layout": "window-callout",
                    "theme": "dark-studio",
                    "ratio": "16:9",
                    "accent": "#2fbf71",
                    "title": "Razor sharp upright window.",
                    "window": {"image": "assets/hero.png", "bg": "#15181d"},
                    "callouts": [
                        {"title": "Pane A", "caption": "Tree", "image": "assets/mod-1.png", "targetX": 15, "targetY": 20, "targetW": 20, "targetH": 16},
                        {"title": "Pane B", "caption": "Diff", "image": "assets/mod-2.png", "targetX": 72, "targetY": 62, "targetW": 40, "targetH": 22},
                    ],
                },
                {
                    "id": "studio-staged-default",
                    "layout": "workflow-steps",
                    "style": "staged",
                    "theme": "dark-studio",
                    "ratio": "16:9",
                    "accent": "#f4503c",
                    "title": "Hand edits to\nyour AI agent.",
                    "subtitle": "Prompt, review cuts, and capture autonomously.",
                    "footer": "LOCAL CLI · MACOS",
                    "steps": [
                        {"title": "Prompt CLI", "caption": "Submit structured plan", "image": "assets/mod-1.png"},
                        {"title": "Review cuts", "caption": "Inspect proposed retakes", "image": "assets/mod-2.png"},
                        {"title": "Record live", "caption": "Autonomous demo stage", "image": "assets/hero.png"},
                    ],
                },
                {
                    "id": "studio-staged-flat",
                    "layout": "workflow-steps",
                    "style": "staged",
                    "tilt": False,
                    "theme": "cool-gallery",
                    "ratio": "16:9",
                    "accent": "#2563eb",
                    "title": "Flat staged workflow.",
                    "steps": [
                        {"title": "Step one", "image": "assets/mod-1.png"},
                        {"title": "Step two", "image": "assets/mod-2.png"},
                        {"title": "Step three", "image": "assets/hero.png"},
                    ],
                },
            ],
        }
        spec_path = self.tmp_dir / "v030.spec.json"
        spec_path.write_text(json.dumps(spec_data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_dir = self.tmp_dir / "out-v030"
        rc, payload = run_cli("render", str(spec_path), "--out-dir", str(out_dir), "--scale", "1")
        self.assertEqual(rc, 0, payload)
        self.assertEqual(payload["rendered"], 5)

        for card in payload["cards"]:
            self.assertEqual(card["warnings"], [], f"Unexpected warnings for {card['id']}: {card['warnings']}")
            html_text = Path(card["html"]).read_text(encoding="utf-8")
            self.assertIn("sc-studio-keylight", html_text)
            self.assertIn("sc-ambient-bounce", html_text)
            self.assertIn("sc-grain", html_text)
            self.assertIn("feTurbulence", html_text)
            self.assertNotIn("ba-pill", html_text)
            self.assertNotIn("sc-layer-badge", html_text)

        ba_upright = (out_dir / "html" / "studio-warm-ba-upright.html").read_text(encoding="utf-8")
        self.assertNotIn(" is-tilted", ba_upright)
        self.assertIn("sc-occluded-label", ba_upright)
        self.assertIn("BASE ARTWORK" if False else "Base artwork", ba_upright)

        ba_tilted = (out_dir / "html" / "studio-cool-ba-tilted.html").read_text(encoding="utf-8")
        self.assertIn("ba-orient-side-by-side is-tilted", ba_tilted)

        wc_upright = (out_dir / "html" / "studio-dark-wc-upright.html").read_text(encoding="utf-8")
        self.assertNotIn("wc-window-frame is-tilted", wc_upright)
        self.assertIn("wc-reticle", wc_upright)
        self.assertIn("sc-occluded-label is-accent wc-callout-num", wc_upright)

        ws_staged = (out_dir / "html" / "studio-staged-default.html").read_text(encoding="utf-8")
        self.assertIn("ws-style-staged is-tilted", ws_staged)
        self.assertIn("ws-milestones", ws_staged)
        self.assertIn("ws-stage-hero", ws_staged)
        self.assertIn("ws-stage-num-hero", ws_staged)

        ws_staged_flat = (out_dir / "html" / "studio-staged-flat.html").read_text(encoding="utf-8")
        self.assertIn("ws-style-staged", ws_staged_flat)
        self.assertNotIn("ws-style-staged is-tilted", ws_staged_flat)

        # Invalid style and non-bool tilt rejected
        bad_spec = self.tmp_dir / "bad-v030.spec.json"
        bad_spec.write_text(
            json.dumps(
                {
                    "cards": [
                        {
                            "id": "bad-ws-style",
                            "layout": "workflow-steps",
                            "style": "carousel",
                            "tilt": "yes",
                            "title": "Invalid",
                            "steps": [
                                {"title": "A", "image": "assets/mod-1.png"},
                                {"title": "B", "image": "assets/mod-2.png"},
                                {"title": "C", "image": "assets/mod-3.png"},
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc_bad, payload_bad = run_cli("render", str(bad_spec), "--out-dir", str(self.tmp_dir / "out-bad"))
        self.assertEqual(rc_bad, 1)
        self.assertTrue(any("style" in e for e in payload_bad["errors"]), payload_bad["errors"])
        self.assertTrue(any("tilt" in e for e in payload_bad["errors"]), payload_bad["errors"])


if __name__ == "__main__":
    unittest.main()
