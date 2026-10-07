"""验证故事所需的独立文字位置、无字页及其真实导出边界。"""

import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from book_factory import make_complete_book
from presentation import effective_presentation, page_regions, validate_presentation
from render_book import render_book


class ArtLayoutTests(unittest.TestCase):
    """从真实页面区域和可读回的 PDF 检验排版，而非只匹配样式字符串。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.book = make_complete_book(self.root)
        self.page = self.book["pages"][1]

    def positioned(self):
        """左右分区保留完整插画，并给短正文留出独立空间。"""
        return {"layout": "positioned", "image_box": [0.35, 0, 0.65, 1],
                "text_box": [0, 0.05, 0.3, 0.7], "font_size_pt": 20}

    def publish(self, directory, pdf=False):
        """测试仅写临时项目，不修改真实绘本。"""
        source = self.root / "book.json"
        source.write_text(json.dumps(self.book, ensure_ascii=False), encoding="utf-8")
        return render_book(source, self.root / directory, pdf=pdf,
                           font=Path("C:/Windows/Fonts/msyh.ttc") if pdf else None)

    def test_existing_layout_regions_keep_their_meaning(self):
        """旧项目的上下顺序与比例保持兼容。"""
        areas = page_regions(self.book, self.page)
        self.assertEqual(areas.image, (0, 0, 1, 0.75))
        self.assertEqual(areas.text, (0, 0.75, 1, 0.25))
        self.page["presentation"] = {"layout": "text-top"}
        areas = page_regions(self.book, self.page)
        self.assertEqual(areas.image, (0, 0.25, 1, 0.75))
        self.assertEqual(areas.text, (0, 0, 1, 0.25))

    def test_positioned_boxes_are_valid_and_not_silently_ignored(self):
        self.page["presentation"] = self.positioned()
        self.assertEqual(validate_presentation(self.positioned()), [])
        areas = page_regions(self.book, self.page)
        self.assertEqual(areas.image, (0.35, 0, 0.65, 1))
        self.assertEqual(areas.text, (0, 0.05, 0.3, 0.7))
        for layout in ("image-top", "text-top", "title-top", "image-only"):
            invalid = self.positioned()
            invalid["layout"] = layout
            with self.subTest(layout=layout):
                self.assertTrue(validate_presentation(invalid))

    def test_outside_nonfinite_boolean_and_malformed_boxes_are_rejected(self):
        bad_boxes = (None, "0,0,1,1", {}, [], [0, 0, 1], [0, 0, 1, 1, 1],
                     [-0.1, 0, 0.5, 0.5], [0, 0, 0, 0.5], [0, 0, 0.5, -0.1],
                     [0.9, 0, 0.2, 0.5], [0, 0.9, 0.5, 0.2],
                     [True, 0, 0.5, 0.5], [0, 0, float("inf"), 0.5],
                     [0, float("nan"), 0.5, 0.5], [0, 0, 10**400, 1])
        for field in ("image_box", "text_box"):
            for box in bad_boxes:
                settings = self.positioned()
                settings[field] = box
                with self.subTest(field=field, box=box):
                    self.assertTrue(validate_presentation(settings))
            settings = self.positioned()
            del settings[field]
            self.assertTrue(validate_presentation(settings))

    def test_overlap_requires_explicit_opt_in_and_edge_contact_is_allowed(self):
        settings = self.positioned()
        settings["image_box"] = [0, 0, 1, 1]
        self.assertTrue(validate_presentation(settings))
        settings["allow_overlap"] = True
        self.assertEqual(validate_presentation(settings), [])
        settings["allow_overlap"] = False
        self.assertTrue(validate_presentation(settings))
        for value in (1, "true", None):
            settings["allow_overlap"] = value
            self.assertTrue(validate_presentation(settings))
        settings = self.positioned()
        settings["text_box"] = [0, 0, 0.35, 1]
        self.assertEqual(validate_presentation(settings), [])

    def test_image_only_never_silently_hides_requested_language_text(self):
        self.page["presentation"] = {"layout": "image-only"}
        with self.assertRaisesRegex(ValueError, "无字|image-only"):
            effective_presentation(self.book, self.page)
        self.page["text"]["zh"] = ""
        areas = page_regions(self.book, self.page)
        self.assertEqual(areas.image, (0, 0, 1, 1))
        self.assertIsNone(areas.text)
        self.page["text"]["en"] = "A sentence must remain visible."
        with self.assertRaisesRegex(ValueError, "无字|image-only"):
            effective_presentation(self.book, self.page)

    def test_image_only_exports_without_an_empty_text_panel(self):
        from pypdf import PdfReader

        self.page["presentation"] = {"layout": "image-only"}
        self.page["text"]["zh"] = ""
        result = self.publish("silent", pdf=True)
        html = result["html"].read_text(encoding="utf-8")
        part = html[html.index('data-page-id="plant"'):html.index('data-page-id="back"')]
        self.assertNotIn('class="story-text"', part)
        rendered = PdfReader(result["pdf"]).pages[1]
        self.assertEqual(rendered.extract_text().strip(), "")
        matrices = [args for args, op in rendered.get_contents().operations
                    if op == b"cm" and float(args[0]) > 1]
        self.assertEqual(len(matrices), 1)
        self.assertAlmostEqual(float(matrices[0][0]), (210 - 24) * 72 / 25.4, places=4)

    def test_pdf_uses_positioned_text_width_and_actual_image_coordinates(self):
        from PIL import Image
        from pypdf import PdfReader
        from book_model import file_digest, page_fingerprint

        Image.new("RGB", (400, 200), "#dce9d7").save(self.root / self.page["image"]["path"])
        # 仅对独立测试素材重新绑定模拟审查，真实作品须逐页人工看图。
        for page in self.book["pages"]:
            fingerprint = page_fingerprint(self.book, page["id"], self.root)
            page["image"]["input_fingerprint"] = fingerprint
            page["qa"]["input_fingerprint"] = fingerprint
            page["qa"]["image_sha256"] = file_digest(self.root / page["image"]["path"])
        self.page["presentation"] = self.positioned()
        result = self.publish("positioned", pdf=True)
        rendered = PdfReader(result["pdf"]).pages[1]
        mm = 72 / 25.4
        matrices = [args for args, op in rendered.get_contents().operations
                    if op == b"cm" and float(args[0]) > 1]
        transform = matrices[0]
        self.assertAlmostEqual(float(transform[0]) / float(transform[3]), 2, places=4)
        self.assertAlmostEqual(float(transform[4]), (12 + 186 * 0.35) * mm, places=4)
        self.assertAlmostEqual(float(transform[0]), 186 * 0.65 * mm, places=4)
        positions = []
        rendered.extract_text(visitor_text=lambda t, cm, tm, fd, fs: positions.append((t, tm, fs)))
        entry = next(item for item in positions if "小兔种下种子。" in item[0])
        self.assertAlmostEqual(entry[1][4], 12 * mm, places=4)
        self.assertEqual(entry[2], 20)
        self.assertGreater(entry[1][5], 160 * mm)
        html = result["html"].read_text(encoding="utf-8")
        self.assertIn('data-layout="positioned"', html)
        self.assertIn("--image-left: 35", html)
        self.assertIn("--text-width: 30", html)
        self.assertNotIn("overflow: hidden", html)

    def test_positioned_text_overflow_cannot_publish_partial_book(self):
        self.page["presentation"] = self.positioned()
        self.page["presentation"]["text_box"] = [0, 0, 0.1, 0.1]
        self.page["text"]["zh"] *= 30
        with self.assertRaisesRegex(ValueError, "plant.*溢出"):
            self.publish("overflow", pdf=True)
        self.assertFalse((self.root / "overflow").exists())

    def test_layout_changes_reuse_art_but_do_not_bypass_semantic_changes(self):
        from book_model import page_statuses

        self.page["presentation"] = self.positioned()
        self.assertEqual(page_statuses(self.book, self.root)[1]["status"], "approved")
        self.page["action"] = "小兔把花盆抱起来"
        statuses = page_statuses(self.book, self.root)
        self.assertEqual([entry["status"] for entry in statuses], ["approved", "stale", "stale"])

    def checked_text(self, font):
        """直接运行只读 CLI，检查生图前的真实字体预检与诊断。"""
        from bookcheck import main

        source = self.root / "book.json"
        source.write_text(json.dumps(self.book), encoding="utf-8")
        before = source.read_bytes()
        output = io.StringIO()
        with patch("sys.stdout", output):
            status = main([str(source), "--stage", "ready", "--font", str(font)])
        self.assertEqual(source.read_bytes(), before)
        return status, json.loads(output.getvalue())

    def test_font_preflight_ready_is_read_only_and_does_not_need_generated_art(self):
        """图尚未制作时也能核验文字框，不必先花成本生成再发现溢出。"""
        self.book["pages"][2]["presentation"] = {"layout": "image-only"}
        self.book["pages"][2]["text"]["zh"] = ""
        for page in self.book["pages"]:
            (self.root / page["image"]["path"]).unlink()
        status, report = self.checked_text("C:/Windows/Fonts/msyh.ttc")
        self.assertEqual(status, 0)
        self.assertTrue(report["ok"] and report["text_checked"])
        self.assertEqual(report["errors"], [])

    def test_font_preflight_reports_all_overflow_pages_in_one_pass(self):
        """一次返回多页问题，避免只改第一处后反复导出。"""
        self.book["pages"][0]["presentation"] = {"font_size_pt": 300}
        self.page["presentation"] = self.positioned()
        self.page["presentation"]["text_box"] = [0, 0, 0.1, 0.1]
        self.page["text"]["zh"] *= 30
        status, report = self.checked_text("C:/Windows/Fonts/msyh.ttc")
        self.assertEqual(status, 1)
        self.assertTrue(report["text_checked"])
        self.assertEqual(len(report["errors"]), 2)
        self.assertTrue(any("cover" in message for message in report["errors"]))
        self.assertTrue(any("plant" in message for message in report["errors"]))

    def test_font_preflight_invalid_font_is_an_explicit_error(self):
        status, report = self.checked_text(self.root / "missing.ttf")
        self.assertEqual(status, 1)
        self.assertFalse(report["ok"])
        self.assertTrue(any("字体" in message for message in report["errors"]))

    def test_font_preflight_does_not_mask_an_invalid_project_with_font_errors(self):
        """先报告结构错误，不把无效区域送入字体测量。"""
        self.page["presentation"] = self.positioned()
        self.page["presentation"]["text_box"] = [-0.1, 0, 0.3, 0.7]
        status, report = self.checked_text(self.root / "missing.ttf")
        self.assertEqual(status, 1)
        self.assertNotIn("text_checked", report)
        self.assertTrue(any("text_box" in message for message in report["errors"]))
        self.assertFalse(any("字体" in message for message in report["errors"]))


if __name__ == "__main__":
    unittest.main()
