"""覆盖项目数据、连续性、素材安全、版本绑定与只读命令行。"""

import base64
import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from book_factory import make_book, make_complete_book, write_png
from book_model import file_digest, page_fingerprint, page_statuses, safe_asset, validate_book

# 由 Pillow 编码的真实二乘二 JPEG；保留固定夹具，使标准库测试无需安装图像依赖。
JPEG_SAMPLE = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwg"
    "JC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAIDASIAAhEBAxEB/8QAH"
    "wAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1"
    "FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNk"
    "ZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1"
    "NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAt"
    "REAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGR"
    "omJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmao"
    "qOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIR"
    "AxEAPwD2OiiisTM//9k="
)


class BookModelTests(unittest.TestCase):
    """每例使用独立项目，避免素材或签名在测试间串扰。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.book = make_book()

    def tearDown(self):
        self.temp.cleanup()

    def errors(self, stage="draft"):
        return validate_book(self.book, self.root, stage)

    def test_valid_project_and_complete_images(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.errors("ready"), [])
        self.book = make_complete_book(self.root, ["zh", "pinyin", "en"])
        self.assertEqual(self.errors("complete"), [])
        self.assertEqual([p["status"] for p in page_statuses(self.book, self.root)], ["approved"] * 3)

    def test_malformed_top_level_has_errors_instead_of_traceback(self):
        for value in (None, [], "invalid", 1, True, {}):
            with self.subTest(value=value):
                self.assertTrue(validate_book(value, self.root))
        self.assertTrue(validate_book(self.book, self.root, "unknown"))

    def test_malformed_sections_and_page_fields(self):
        fields = ("brief", "format", "visual", "characters", "scenes", "props", "page_order", "pages")
        for field in fields:
            with self.subTest(field=field):
                book = make_book()
                book[field] = None
                self.assertTrue(validate_book(book, self.root))
        for field in ("id", "kind", "scene_id", "character_ids", "depends_on", "purpose", "action",
                      "visual_evidence", "text", "continuity"):
            with self.subTest(page_field=field):
                book = make_book()
                book["pages"][0][field] = None
                self.assertTrue(validate_book(book, self.root))

    def test_number_constraints_reject_bool_and_nonfinite(self):
        for key in self.book["format"]:
            for bad in (True, math.inf, -math.inf, math.nan, "12", None):
                with self.subTest(key=key, value=bad):
                    book = make_book()
                    book["format"][key] = bad
                    self.assertTrue(validate_book(book, self.root))
        for field, value in (("width_mm", 0), ("height_mm", -1), ("margin_mm", -1),
                             ("margin_mm", 105), ("font_size_pt", 0), ("line_spacing", 0.9),
                             ("text_ratio", 0), ("text_ratio", 1)):
            with self.subTest(field=field, value=value):
                book = make_book()
                book["format"][field] = value
                self.assertTrue(validate_book(book, self.root))
        for version in (True, 1.0, "1", 2):
            self.book["schema_version"] = version
            self.assertTrue(self.errors())

    def test_languages_pinyin_and_required_text(self):
        for langs in ([], ["en"], ["zh", "zh"], ["zh", "fr"], None, ["zh", {}]):
            book = make_book()
            book["brief"]["languages"] = langs
            self.assertTrue(validate_book(book, self.root))
        self.book["brief"]["languages"] = ["zh", "en", "pinyin"]
        self.assertTrue(self.errors())
        self.book = make_complete_book(self.root, ["zh", "en", "pinyin"])
        self.book["pages"][0]["text"]["pinyin"][0]["char"] = "坏"
        self.assertTrue(self.errors())
        self.book["pages"][0]["text"]["pinyin"] = [{"char": "小", "reading": 8}]
        self.assertTrue(self.errors())

    def test_page_count_order_and_cover_constraints(self):
        for mutation in (lambda b: b["page_order"].append("lost"),
                         lambda b: b["page_order"].append("cover"),
                         lambda b: b["pages"].append(copy.deepcopy(b["pages"][0])),
                         lambda b: b["pages"][0].update(kind="frontmatter"),
                         lambda b: b["pages"][-1].update(kind="backmatter"),
                         lambda b: b["brief"].update(body_page_count=2),
                         lambda b: b["brief"].update(body_page_count=True),
                         lambda b: b["pages"].append("bad")):
            book = make_book()
            mutation(book)
            self.assertTrue(validate_book(book, self.root))

    def test_references_and_dependency_order(self):
        for field, bad in (("scene_id", "unknown"), ("character_ids", ["unknown"]),
                           ("character_ids", ["rabbit", "rabbit"]), ("depends_on", ["unknown"]),
                           ("depends_on", ["cover"]), ("depends_on", ["plant"]),
                           ("depends_on", ["back", "back"])):
            book = make_book()
            book["pages"][0][field] = bad
            self.assertTrue(validate_book(book, self.root))
        self.book["pages"][0]["depends_on"] = ["back"]
        self.assertTrue(self.errors())
        with self.assertRaises(ValueError):
            page_fingerprint(self.book, "back", self.root)

    def test_continuity_follows_export_order_and_initial_state(self):
        self.book["pages"][1]["continuity"]["seed"]["before"] = "路边"
        self.assertTrue(self.errors())
        self.book = make_book()
        self.book["props"]["seed"]["initial_state"] = "口袋中"
        self.assertTrue(self.errors())
        self.book = make_book()
        self.book["pages"][1]["continuity"] = {"unknown": {"before": "a", "after": "b"}}
        self.assertTrue(self.errors())
        self.book["pages"][1]["continuity"] = {"seed": {"before": "小兔手中", "after": ""}}
        self.assertTrue(self.errors())

    def test_asset_paths_reject_escape_and_urls(self):
        for bad in ("", ".", "../outside.png", "images/../../outside.png", r"..\outside.png",
                    "/etc/passwd", r"C:\outside.png", "https://x/a.png", "file:///x",
                    "a\x00.png", 5, None, "//server/a", r"images\..\a.png"):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    safe_asset(self.root, bad)
        self.assertEqual(safe_asset(self.root, "images/a.png"), self.root / "images" / "a.png")

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / "escape"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("当前系统未授予创建符号链接权限")
            with self.assertRaises(ValueError):
                safe_asset(self.root, "escape/a.png")

    def test_approvals_and_prompts_only_required_for_production(self):
        self.book.pop("approvals")
        self.book["pages"][0].pop("prompt")
        self.assertEqual(self.errors(), [])
        self.assertTrue(self.errors("ready"))

    def test_text_and_typography_do_not_invalidate_illustrations(self):
        before = page_fingerprint(self.book, "back", self.root)
        self.book["title"] = "新标题"
        self.book["pages"][0]["text"] = {"zh": "修改正文", "en": "New text", "pinyin": []}
        self.book["format"]["font_size_pt"] = 24
        self.book["format"]["line_spacing"] = 1.8
        self.assertEqual(before, page_fingerprint(self.book, "back", self.root))

    def test_invalid_page_presentation_is_rejected_at_every_stage(self):
        """排版配置不能注入样式，也不能悄悄接受拼错的选项。"""
        invalid = [None, [], "title-top", {"layout": "overlay"}, {"layout": []},
                   {"text_align": "right"}, {"font_size_pt": True},
                   {"font_size_pt": 0}, {"font_size_pt": -1},
                   {"font_size_pt": math.inf}, {"font_size_pt": math.nan},
                   {"font_size_pt": 10 ** 400},
                   {"text_color": "red"}, {"text_color": "#fff"},
                   {"paper_color": "#ffffff;display:none"}, {"paper_color": 7},
                   {"font_size": 24}]
        for value in invalid:
            for stage in ("draft", "ready", "complete"):
                with self.subTest(value=value, stage=stage):
                    book = make_complete_book(self.root)
                    book["pages"][0]["presentation"] = value
                    self.assertTrue(validate_book(book, self.root, stage))

    def test_presentation_changes_reuse_all_approved_illustrations(self):
        """独立文字区的位置和颜色不改变插画素材或依赖签名。"""
        self.book = make_complete_book(self.root)
        before = [page_fingerprint(self.book, p, self.root) for p in self.book["page_order"]]
        for index, page in enumerate(self.book["pages"]):
            page["presentation"] = {
                "layout": ("title-top", "text-top", "image-top")[index],
                "text_align": "center", "font_size_pt": 25,
                "text_color": "#315A6F", "paper_color": "#FFFAEF",
            }
        self.assertEqual(before, [page_fingerprint(self.book, p, self.root)
                                  for p in self.book["page_order"]])
        self.assertEqual(self.errors("complete"), [])
        self.assertEqual([p["status"] for p in page_statuses(self.book, self.root)], ["approved"] * 3)

    def test_visual_changes_and_dependencies_invalidate_downstream(self):
        before = [page_fingerprint(self.book, p, self.root) for p in self.book["page_order"]]
        self.book["pages"][1]["action"] = "小兔给花盆浇水"
        after = [page_fingerprint(self.book, p, self.root) for p in self.book["page_order"]]
        self.assertEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])
        self.assertNotEqual(before[2], after[2])
        for section, key, value in (("visual", "description", "铅笔风格"),
                                    ("format", "text_ratio", 0.3)):
            book = make_book()
            book[section][key] = value
            self.assertNotEqual(before[0], page_fingerprint(book, "cover", self.root))

    def test_adopted_upstream_image_change_invalidates_visual_dependents(self):
        self.book = make_complete_book(self.root)
        cover = self.book["pages"][0]
        original = page_fingerprint(self.book, "cover", self.root)
        jpeg = self.root / "images/repainted-cover.jpg"
        jpeg.write_bytes(base64.b64decode(JPEG_SAMPLE))
        cover["image"]["path"] = "images/repainted-cover.jpg"
        cover["qa"]["image_sha256"] = file_digest(jpeg)
        self.assertEqual(original, page_fingerprint(self.book, "cover", self.root))
        self.assertEqual([page["status"] for page in page_statuses(self.book, self.root)],
                         ["approved", "stale", "stale"])

    def test_references_use_content_not_modification_time(self):
        image_path = self.root / "reference.png"
        write_png(image_path)
        self.book["characters"]["rabbit"]["reference"] = "reference.png"
        before = page_fingerprint(self.book, "cover", self.root)
        image_path.touch()
        self.assertEqual(before, page_fingerprint(self.book, "cover", self.root))
        image_path.write_bytes(image_path.read_bytes() + b"different content")
        self.assertNotEqual(before, page_fingerprint(self.book, "cover", self.root))
        image_path.unlink()
        self.assertTrue(self.errors())
        with self.assertRaises(ValueError):
            page_fingerprint(self.book, "cover", self.root)

    def test_digest_and_unknown_page(self):
        sample = self.root / "sample.txt"
        sample.write_bytes(b"abc")
        self.assertEqual(file_digest(sample), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        with self.assertRaises(ValueError):
            page_fingerprint(self.book, "unknown", self.root)

    def test_missing_stale_review_approved_resume(self):
        self.assertEqual([p["status"] for p in page_statuses(self.book, self.root)], ["missing"] * 3)
        self.book = make_complete_book(self.root)
        self.book["pages"][0]["image"]["input_fingerprint"] = "old"
        self.book["pages"][1].pop("qa")
        statuses = page_statuses(self.book, self.root)
        self.assertEqual([p["status"] for p in statuses], ["stale", "review", "approved"])
        self.assertTrue(self.errors("complete"))

    def test_qa_is_bound_to_current_image_and_literal_boolean(self):
        for mutation in (lambda p: p["qa"].update(image_sha256="old"),
                         lambda p: p["qa"].update(input_fingerprint="old"),
                         lambda p: p["qa"].update(reviewer=""),
                         lambda p: p["qa"].update(notes=""),
                         lambda p: p["qa"]["checks"].update(identity=1),
                         lambda p: p["qa"]["checks"].update(anatomy=False),
                         lambda p: p["qa"].update(checks=None),
                         lambda p: p.update(qa="bad")):
            self.book = make_complete_book(self.root)
            mutation(self.book["pages"][0])
            self.assertEqual(page_statuses(self.book, self.root)[0]["status"], "review")
            self.assertTrue(self.errors("complete"))

    def test_real_image_required_not_just_file_presence(self):
        for data in (b"fake png", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff\xd9"):
            self.book = make_complete_book(self.root)
            path = self.root / self.book["pages"][0]["image"]["path"]
            path.write_bytes(data)
            self.assertTrue(self.errors("complete"))
            self.assertEqual(page_statuses(self.book, self.root)[0]["status"], "missing")
        self.book = make_complete_book(self.root)
        path = self.root / self.book["pages"][0]["image"]["path"]
        path.unlink()
        self.assertTrue(self.errors("complete"))

    def test_corrupt_image_and_unsafe_reference_have_errors(self):
        self.book = make_complete_book(self.root)
        path = self.root / self.book["pages"][0]["image"]["path"]
        data = bytearray(path.read_bytes())
        data[30] ^= 1
        path.write_bytes(data)
        self.assertTrue(self.errors("complete"))
        self.book["visual"]["reference"] = "../outside.png"
        self.assertTrue(self.errors())
        self.book = make_book()
        self.book["characters"]["rabbit"] = []
        self.assertTrue(self.errors())

    def test_chinese_pinyin_requires_reading_but_punctuation_may_be_empty(self):
        self.book = make_complete_book(self.root, ["zh", "pinyin"])
        self.assertEqual(self.errors(), [])
        self.book["pages"][0]["text"]["pinyin"][0]["reading"] = ""
        self.assertTrue(self.errors())

    def test_real_jpeg_and_corrupt_segments(self):
        self.book = make_complete_book(self.root)
        page = self.book["pages"][0]
        jpeg = self.root / "images/cover.jpg"
        data = base64.b64decode(JPEG_SAMPLE)
        jpeg.write_bytes(data)
        page["image"]["path"] = "images/cover.jpg"
        page["qa"]["image_sha256"] = file_digest(jpeg)
        # 测试替换已采用封面后，显式重新绑定后续页面，确保整书处于当前版本。
        for dependent in self.book["pages"][1:]:
            fingerprint = page_fingerprint(self.book, dependent["id"], self.root)
            dependent["image"]["input_fingerprint"] = fingerprint
            dependent["qa"]["input_fingerprint"] = fingerprint
        self.assertEqual(self.errors("complete"), [])
        for damaged in (data[:-2], data[:2] + b"bad" + data[5:], data[:4] + b"\xff\xff" + data[6:]):
            with self.subTest(damaged_length=len(damaged)):
                jpeg.write_bytes(damaged)
                self.assertTrue(self.errors("complete"))
                self.assertEqual(page_statuses(self.book, self.root)[0]["status"], "missing")

    def test_malformed_nested_fields_fail_cleanly(self):
        mutations = (
            lambda b: b.update(book_id="", title=[]),
            lambda b: b["brief"].update(age_range=None),
            lambda b: b["visual"].update(description=None, palette=[{}]),
            lambda b: b["props"]["seed"].update(description="", initial_state=None),
            lambda b: b["characters"].update({"": {"description": "兔"}}),
            lambda b: b["pages"][0].update(image={"path": 8}),
            lambda b: b["pages"][0].update(continuity={"seed": []}),
            lambda b: b["pages"][0].update(depends_on=[{}]),
            lambda b: b["pages"][0].update(character_ids=[{}]),
        )
        for mutation in mutations:
            book = make_book()
            mutation(book)
            self.assertTrue(validate_book(book, self.root))

    def test_cli_json_errors_and_status(self):
        cli = Path(__file__).resolve().parents[1] / "scripts" / "bookcheck.py"
        project = self.root / "book.json"
        project.write_text(json.dumps(self.book, ensure_ascii=False), encoding="utf-8")
        run = subprocess.run([sys.executable, str(cli), str(project), "--status"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(json.loads(run.stdout)["pages"]), 3)
        for payload in ("not json", "[]", '{"schema_version":NaN}'):
            project.write_text(payload, encoding="utf-8")
            run = subprocess.run([sys.executable, str(cli), str(project)], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse(run.stderr)
            self.assertTrue(json.loads(run.stdout)["errors"])
        project.unlink()
        run = subprocess.run([sys.executable, str(cli), str(project)], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn("Traceback", run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
