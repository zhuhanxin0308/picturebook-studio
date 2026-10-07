"""验证成书导出、字体覆盖、版面边界与不覆盖既有成果的约束。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from book_factory import make_complete_book
from render_book import render_book


class RenderBookTests(unittest.TestCase):
    """使用临时工作区里的完整三页绘本检查实际导出行为。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "book.json"
        self.output = self.root / "published"
        self.book = make_complete_book(self.root)

    def save(self):
        """测试数据属于独立临时目录，保持项目源文件不变。"""
        self.source.write_text(json.dumps(self.book, ensure_ascii=False), encoding="utf-8")

    def bind_test_reviews(self):
        """测试替换图像后重新绑定全书模拟审查，覆盖前置图像依赖。"""
        from book_model import file_digest, page_fingerprint

        by_id = {page["id"]: page for page in self.book["pages"]}
        for page_id in self.book["page_order"]:
            page = by_id[page_id]
            fingerprint = page_fingerprint(self.book, page_id, self.root)
            page["image"]["input_fingerprint"] = fingerprint
            page["qa"]["input_fingerprint"] = fingerprint
            page["qa"]["image_sha256"] = file_digest(self.root / page["image"]["path"])

    def font(self):
        """允许持续集成明确提供字体，本机验证使用实际中文字体。"""
        candidates = [os.environ.get("PICTUREBOOK_TEST_FONT", ""),
                      "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                      "/usr/share/fonts/truetype/arphic/uming.ttc"]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        self.skipTest("测试环境未提供真实中文字体，不能声称 PDF 已验证")

    def test_html_is_self_contained_and_preserves_order(self):
        self.save()
        result = render_book(self.source, self.output)
        html = result["html"].read_text(encoding="utf-8")
        self.assertEqual(result["html"], self.output / "book.html")
        self.assertEqual(html.count('class="page"'), 3)
        self.assertEqual(html.count("data:image/png;base64,"), 3)
        self.assertIn("object-fit: contain", html)
        self.assertLess(html.index('data-page-id="cover"'), html.index('data-page-id="plant"'))
        self.assertLess(html.index('data-page-id="plant"'), html.index('data-page-id="back"'))
        self.assertNotIn("<script", html)
        self.assertNotIn('src="http', html)
        self.assertNotIn("overflow: hidden", html)

    def test_html_escapes_text_title_and_ruby(self):
        self.book = make_complete_book(self.root, ["zh", "pinyin", "en"])
        self.book["title"] = '<script>alert("书名")</script>'
        page = self.book["pages"][1]
        page["text"]["zh"] = "<兔>"
        page["text"]["en"] = '<img src=x onerror="alert(1)">'
        page["text"]["pinyin"] = [
            {"char": "<", "reading": ""},
            {"char": "兔", "reading": "tù</rt><script>"},
            {"char": ">", "reading": ""},
        ]
        self.save()
        html = render_book(self.source, self.output)["html"].read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<ruby>", html)
        self.assertIn("tù&lt;/rt&gt;&lt;script&gt;", html)
        self.assertNotIn('<img src=x', html)

    def test_cover_defaults_create_title_hierarchy_and_responsive_screen(self):
        """旧项目也应产生独立封面标题层级，并保留无脚本手机阅读。"""
        from html.parser import HTMLParser

        class Sections(HTMLParser):
            """只读取真实页面结构，避免把样式文本当成页面。"""
            def __init__(self):
                super().__init__()
                self.pages = {}
                self.current = None

            def handle_starttag(self, tag, attrs):
                data = dict(attrs)
                if tag == "section":
                    self.current = data["data-page-id"]
                    self.pages[self.current] = {"attrs": data, "blocks": []}
                elif tag == "div" and self.current:
                    self.pages[self.current]["blocks"].append(data.get("class"))

        self.save()
        html = render_book(self.source, self.output)["html"].read_text(encoding="utf-8")
        parser = Sections()
        parser.feed(html)
        self.assertEqual(parser.pages["cover"]["blocks"], ["story-text", "illustration"])
        self.assertEqual(parser.pages["plant"]["blocks"], ["illustration", "story-text"])
        self.assertIn("--font-size: 27.0pt", parser.pages["cover"]["attrs"]["style"])
        self.assertIn("--font-size: 18pt", parser.pages["plant"]["attrs"]["style"])
        self.assertIn("text-align: center", parser.pages["cover"]["attrs"]["style"])
        self.assertNotIn("width: max-content", html)
        self.assertIn("max-width: 100%", html)
        self.assertIn("@media print", html)
        self.assertNotIn("<script", html)

    def test_per_page_layout_colors_and_alignment_are_shared_with_pdf(self):
        """实导出检查上方文字、纸色、字色和居中坐标，所有文字仍可读回。"""
        from pypdf import PdfReader

        page = self.book["pages"][1]
        page["presentation"] = {"layout": "text-top", "text_align": "center",
                                "font_size_pt": 26, "text_color": "#315A6F",
                                "paper_color": "#FFFAEF"}
        self.save()
        result = render_book(self.source, self.output, pdf=True, font=self.font())
        html = result["html"].read_text(encoding="utf-8")
        part = html[html.index('data-page-id="plant"'):html.index('data-page-id="back"')]
        self.assertLess(part.index('class="story-text"'), part.index('class="illustration"'))
        self.assertIn("#315A6F", part)
        self.assertIn("#FFFAEF", part)
        rendered = PdfReader(result["pdf"]).pages[1]
        texts = []
        rendered.extract_text(visitor_text=lambda t, cm, tm, fd, fs: texts.append((t, tm, fs)))
        entry = next(item for item in texts if "小兔种下种子。" in item[0])
        self.assertEqual(entry[2], 26)
        self.assertGreater(entry[1][4], 12 * 72 / 25.4)
        self.assertGreater(entry[1][5], 0.75 * float(rendered.mediabox.height))
        colors = [args for args, operation in rendered.get_contents().operations if operation == b"rg"]
        self.assertTrue(any(abs(float(c[0]) - 49 / 255) < 0.001 for c in colors))
        self.assertTrue(any(abs(float(c[0]) - 1) < 0.001 and abs(float(c[1]) - 250 / 255) < 0.001
                            for c in colors))

    def test_larger_page_font_overflow_cannot_publish_partial_book(self):
        """单页字号参与实际测量，不能只改变可见样式却绕过溢出检查。"""
        self.book["pages"][1]["presentation"] = {"font_size_pt": 300}
        self.save()
        with self.assertRaisesRegex(ValueError, "plant.*溢出"):
            render_book(self.source, self.output, pdf=True, font=self.font())
        self.assertFalse(self.output.exists())

    def test_direct_html_export_also_rejects_invalid_presentation(self):
        """绕过整书入口调用底层导出时，非法样式同样不能写入成品。"""
        from render_html import write_html

        self.book["pages"][0]["presentation"] = {"paper_color": "#FFFFFF;display:none"}
        destination = self.root / "direct.html"
        with self.assertRaisesRegex(ValueError, "presentation"):
            write_html(self.book, self.root, destination)
        self.assertFalse(destination.exists())

    def test_centered_ruby_and_english_use_page_font_and_stay_extractable(self):
        """对三语实排版，覆盖逐字拼音与整句英文两种居中路径。"""
        from pypdf import PdfReader

        self.book = make_complete_book(self.root, ["zh", "pinyin", "en"])
        self.book["pages"][1]["presentation"] = {"layout": "text-top", "text_align": "center",
                                                 "font_size_pt": 20}
        self.save()
        reader = PdfReader(render_book(self.source, self.output, pdf=True, font=self.font())["pdf"])
        text = reader.pages[1].extract_text()
        self.assertIn("xiǎo", text)
        self.assertIn("zhòng", text)
        self.assertIn("Rabbit plants a seed.", text)

    def test_all_layouts_preserve_image_ratio_and_keep_text_in_separate_region(self):
        """核验真实 PDF 坐标：切换上下顺序只移动整图，不裁切或与文字重叠。"""
        from PIL import Image
        from pypdf import PdfReader

        page = self.book["pages"][1]
        Image.new("RGB", (600, 200), "#dce9d7").save(self.root / page["image"]["path"])
        self.bind_test_reviews()
        for layout in ("image-top", "text-top", "title-top"):
            with self.subTest(layout=layout):
                page["presentation"] = {"layout": layout, "font_size_pt": 20}
                self.save()
                destination = self.root / layout
                rendered = PdfReader(render_book(self.source, destination, pdf=True,
                                                 font=self.font())["pdf"]).pages[1]
                matrices = [args for args, op in rendered.get_contents().operations
                            if op == b"cm" and float(args[0]) > 1]
                self.assertEqual(len(matrices), 1)
                transform = matrices[0]
                self.assertAlmostEqual(float(transform[0]) / float(transform[3]), 3, places=5)
                text_positions = []
                rendered.extract_text(visitor_text=lambda t, cm, tm, fd, fs:
                                      text_positions.append((t, tm, fs)))
                position = next(item for item in text_positions if "小兔种下种子。" in item[0])
                if layout == "image-top":
                    self.assertLess(position[1][5] + position[2], float(transform[5]))
                else:
                    self.assertGreater(position[1][5], float(transform[5]) + float(transform[3]))

    def test_browser_mobile_desktop_and_print_keep_complete_language_layers(self):
        """在真实浏览器读取离线文件，核验屏幕不横滚、文字自然延展与打印尺寸。"""
        node_path = os.environ.get("PICTUREBOOK_TEST_NODE")
        if not node_path or not Path(node_path).is_file():
            self.skipTest("未提供浏览器测试运行时；需在交付验收单独完成真实窄屏检查")
        self.book = make_complete_book(self.root, ["zh", "pinyin", "en"])
        page = self.book["pages"][1]
        page["text"]["zh"] *= 12
        page["text"]["pinyin"] *= 12
        page["text"]["en"] *= 12
        self.save()
        path = render_book(self.source, self.output)["html"]
        javascript = r"""
        const {chromium} = require('playwright');
        const {pathToFileURL} = require('url');
        (async () => {
          // 独立无界面浏览器只读取测试文件，不启动服务或访问用户浏览器资料。
          const browser = await chromium.launch({headless: true, executablePath: process.env.PICTUREBOOK_TEST_BROWSER});
          const results = [];
          const page = await browser.newPage({viewport: {width: 320, height: 1100}});
          await page.goto(pathToFileURL(process.argv[1]).href, {waitUntil: 'load'});
          for (const width of [320, 390, 1100]) {
            await page.setViewportSize({width, height: 1100});
            const data = await page.evaluate(() => ({
              width: document.documentElement.clientWidth,
              scrollWidth: document.documentElement.scrollWidth,
              pages: Array.from(document.querySelectorAll('.page')).map(element => {
                const r = element.getBoundingClientRect();
                const text = element.querySelector('.story-text').getBoundingClientRect();
                const image = element.querySelector('img');
                const ir = image.getBoundingClientRect();
                return {loaded: image.complete && image.naturalWidth > 0,
                  textInside: text.bottom <= r.bottom + 1 && text.left >= r.left && text.right <= r.right + 1,
                  imageInside: ir.left >= r.left && ir.right <= r.right + 1 && ir.bottom <= r.bottom + 1,
                  fontSize: parseFloat(getComputedStyle(element.querySelector('.story-text')).fontSize),
                  fit: getComputedStyle(image).objectFit};
              })
            }));
            results.push(data);
          }
          await page.setViewportSize({width: 320, height: 1100});
          await page.emulateMedia({media: 'print'});
          const printed = await page.locator('.page').first().evaluate(e => ({
            width: e.getBoundingClientRect().width,
            font: parseFloat(getComputedStyle(e.querySelector('.story-text')).fontSize)
          }));
          await browser.close();
          console.log(JSON.stringify({results, printed}));
        })().catch(error => {console.error(error); process.exit(1);});
        """
        process = subprocess.run([node_path, "-e", javascript, str(path)], capture_output=True,
                                 text=True, encoding="utf-8", timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        for viewport in result["results"]:
            self.assertLessEqual(viewport["scrollWidth"], viewport["width"] + 1)
            self.assertEqual(len(viewport["pages"]), 3)
            for rendered in viewport["pages"]:
                self.assertTrue(rendered["loaded"] and rendered["textInside"] and rendered["imageInside"])
                self.assertGreaterEqual(rendered["fontSize"], 20)
                self.assertEqual(rendered["fit"], "contain")
        self.assertAlmostEqual(result["printed"]["width"], 210 * 96 / 25.4, delta=0.1)
        self.assertAlmostEqual(result["printed"]["font"], 27 * 96 / 72, places=2)

    def test_existing_directory_and_file_are_never_overwritten(self):
        self.save()
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("已确认的成书", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已存在"):
            render_book(self.source, self.output)
        self.assertEqual(marker.read_text(encoding="utf-8"), "已确认的成书")
        existing_file = self.root / "existing-file"
        existing_file.write_text("原文件", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已存在"):
            render_book(self.source, existing_file)

    def test_incomplete_review_prevents_every_export(self):
        self.book["pages"][0]["qa"]["checks"]["identity"] = False
        self.save()
        with self.assertRaises(ValueError):
            render_book(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_escaped_asset_path_cannot_read_outside_project(self):
        self.book["pages"][1]["image"]["path"] = "../private.png"
        self.save()
        with self.assertRaises(ValueError):
            render_book(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_json_and_missing_input_are_reported(self):
        self.source.write_text("{错误", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "项目"):
            render_book(self.source, self.output)
        with self.assertRaisesRegex(ValueError, "项目"):
            render_book(self.root / "missing.json", self.output)
        self.assertFalse(self.output.exists())

    def test_nonobject_project_and_missing_font_are_rejected(self):
        self.source.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "最外层"):
            render_book(self.source, self.output)
        self.save()
        with self.assertRaisesRegex(ValueError, "字体"):
            render_book(self.source, self.output, pdf=True, font=self.root / "missing.ttf")
        self.assertFalse(self.output.exists())

    def test_pdf_can_be_read_back_with_chinese_and_all_pages(self):
        from pypdf import PdfReader

        self.book = make_complete_book(self.root, ["zh", "pinyin", "en"])
        self.save()
        result = render_book(self.source, self.output, pdf=True, font=self.font())
        reader = PdfReader(result["pdf"])
        self.assertEqual(len(reader.pages), 3)
        self.assertEqual(reader.metadata.author, "")
        self.assertEqual(reader.metadata.creator, "绘本创作工作室")
        text = "".join(page.extract_text() for page in reader.pages)
        self.assertIn("Rabbit plants a seed.", text)
        self.assertIn("zhòng", text)
        self.assertIn("兔", text)
        self.assertIn("种", text)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 210 * 72 / 25.4, places=3)

    def test_pdf_without_font_and_invalid_font_leave_no_output(self):
        self.save()
        with self.assertRaisesRegex(ValueError, "字体"):
            render_book(self.source, self.output, pdf=True)
        self.assertFalse(self.output.exists())
        broken_font = self.root / "broken.ttf"
        broken_font.write_bytes(b"this is not a font")
        with self.assertRaisesRegex(ValueError, "字体"):
            render_book(self.source, self.output, pdf=True, font=broken_font)
        self.assertFalse(self.output.exists())

    def test_font_missing_chinese_glyphs_is_rejected(self):
        import reportlab

        self.save()
        latin_font = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        with self.assertRaisesRegex(ValueError, "缺少|覆盖"):
            render_book(self.source, self.output, pdf=True, font=latin_font)
        self.assertFalse(self.output.exists())

    def test_pdf_overflow_reports_page_and_removes_partial_artifacts(self):
        self.book["pages"][1]["text"]["zh"] = "小兔种下种子。" * 300
        self.save()
        with self.assertRaisesRegex(ValueError, "plant.*溢出|溢出.*plant"):
            render_book(self.source, self.output, pdf=True, font=self.font())
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".picturebook-*")), [])

    def test_publication_failure_removes_only_new_output(self):
        self.save()
        with patch("render_book.os.replace", side_effect=OSError("模拟发布失败")):
            with self.assertRaises(OSError):
                render_book(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.assertTrue(self.source.is_file())

    def test_asset_change_during_export_cannot_publish_unreviewed_image(self):
        from render_html import write_html

        self.save()

        def change_after_html(book, root, destination):
            write_html(book, root, destination)
            (root / book["pages"][0]["image"]["path"]).write_bytes(b"changed")

        with patch("render_book.write_html", side_effect=change_after_html):
            with self.assertRaises(ValueError):
                render_book(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_pdf_plain_chinese_multiline_and_english_read_back(self):
        from pypdf import PdfReader

        self.book = make_complete_book(self.root, ["zh", "en"])
        self.book["pages"][1]["text"]["zh"] = "小兔种下种子。\n明天见。"
        self.save()
        result = render_book(self.source, self.output, pdf=True, font=self.font())
        text = PdfReader(result["pdf"]).pages[1].extract_text()
        self.assertIn("小兔种下种子。", text)
        self.assertIn("明天见。", text)
        self.assertIn("Rabbit plants a seed.", text)

    def test_pdf_rejects_word_wider_than_text_region(self):
        self.book = make_complete_book(self.root, ["zh", "en"])
        self.book["pages"][1]["text"]["en"] = "W" * 300
        self.save()
        with self.assertRaisesRegex(ValueError, "plant.*溢出"):
            render_book(self.source, self.output, pdf=True, font=self.font())
        self.assertFalse(self.output.exists())

    def test_pdf_preserves_wide_image_ratio(self):
        from PIL import Image
        from pypdf import PdfReader

        page = self.book["pages"][0]
        image_path = self.root / page["image"]["path"]
        Image.new("RGB", (600, 200), "#dce9d7").save(image_path)
        self.bind_test_reviews()
        self.save()
        result = render_book(self.source, self.output, pdf=True, font=self.font())
        operations = PdfReader(result["pdf"]).pages[0].get_contents().operations
        transforms = [arguments for arguments, operation in operations
                      if operation == b"cm" and float(arguments[0]) > 1]
        self.assertEqual(len(transforms), 1)
        self.assertAlmostEqual(float(transforms[0][0]) / float(transforms[0][3]), 3, places=5)

    def test_html_cli_runs_without_third_party_site_packages(self):
        self.save()
        process = subprocess.run(
            [sys.executable, "-S", str(SCRIPTS / "render_book.py"), str(self.source),
             "--output", str(self.output)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue((self.output / "book.html").is_file())

    def test_pdf_cli_reports_undecodable_jpeg_without_traceback(self):
        from book_model import validate_book

        # 结构有帧和扫描段，但缺少实际解码必需的量化表与有效像素流。
        invalid_jpeg = bytes.fromhex(
            "FFD8 FFC0 0011 08 0002 0002 03 01 11 00 02 11 00 03 11 00 "
            "FFDA 000C 03 01 00 02 11 03 11 00 3F 00 00 FFD9"
        )
        page = self.book["pages"][0]
        page["image"]["path"] = "images/broken.jpg"
        image_path = self.root / page["image"]["path"]
        image_path.write_bytes(invalid_jpeg)
        self.bind_test_reviews()
        self.assertEqual(validate_book(self.book, self.root, stage="complete"), [])
        self.save()
        process = subprocess.run(
            [sys.executable, str(SCRIPTS / "render_book.py"), str(self.source),
             "--output", str(self.output), "--pdf", "--font", str(self.font())],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(process.returncode, 0)
        error = json.loads(process.stderr)["error"]
        self.assertIn("cover", error)
        self.assertIn("图片", error)
        self.assertNotIn("Traceback", process.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_emits_paths_and_returns_nonzero_for_invalid_input(self):
        self.save()
        command = [sys.executable, str(SCRIPTS / "render_book.py"), str(self.source),
                   "--output", str(self.output)]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(Path(json.loads(process.stdout)["html"]), self.output / "book.html")
        repeated = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("已存在", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
