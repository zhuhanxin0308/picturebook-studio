"""使用真实字体和确定的文字测量生成完整阅读版 PDF。"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from book_model import safe_asset
from presentation import POINTS_PER_MM, TEXT_IMAGE_GAP_RATIO, effective_presentation, page_regions

PINYIN_SIZE_RATIO = 0.5
PINYIN_GAP_RATIO = 0.15
LANGUAGE_GAP_RATIO = 0.5
MISSING_GLYPH_PREVIEW_LIMIT = 20
FLOAT_TOLERANCE_PT = 0.001
CLOSING_PUNCTUATION = "，。！？：；、）》】』」”’,.!?;:%)]}"
OPENING_PUNCTUATION = "（《【『「“‘([{"


@dataclass(frozen=True)
class Run:
    """一段不可拆开的正文或一个带拼音的汉字。"""

    text: str
    reading: str = ""


@dataclass
class Block:
    """已完成测量的文字块，绘制阶段不再改变字号与换行。"""

    lines: list
    line_height: float
    ruby_height: float
    ascent: float
    ruby_ascent: float

    @property
    def height(self):
        return len(self.lines) * self.line_height


def _dependencies():
    """延迟加载 PDF 依赖，HTML 路线保持仅需标准库。"""
    try:
        from PIL import Image, ImageOps
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen.canvas import Canvas
        return Image, ImageOps, ImageReader, pdfmetrics, TTFont, Canvas
    except ImportError as error:
        raise ValueError("PDF 导出需要安装 ReportLab 与 Pillow。") from error


def _load_font(book, font_path, pdfmetrics, font_class):
    """检查真正的字形映射，不用系统回退字体掩盖缺字。"""
    if not font_path:
        raise ValueError("PDF 导出必须通过 --font 明确提供实际中文字体。")
    path = Path(font_path)
    try:
        name = "Picturebook_" + hashlib.sha256(path.read_bytes()).hexdigest()
        font = font_class(name, str(path))
    except Exception as error:
        raise ValueError(f"无法读取或加载中文字体：{path}；{error}") from error
    characters = set()
    for page in book["pages"]:
        characters.update(page["text"]["zh"])
        if "en" in book["brief"]["languages"]:
            characters.update(page["text"]["en"])
        if "pinyin" in book["brief"]["languages"]:
            for item in page["text"]["pinyin"]:
                characters.update(item["reading"])
    missing = sorted(characters - set("\r\n\t"), key=ord)
    missing = [char for char in missing if ord(char) not in font.face.charToGlyph]
    if missing:
        preview = "、".join(f"{char} (U+{ord(char):04X})" for char in missing[:MISSING_GLYPH_PREVIEW_LIMIT])
        raise ValueError(f"字体缺少正文字符，不能完整覆盖本书：{preview}")
    pdfmetrics.registerFont(font)
    return name


def _run_width(run, measure, font_size):
    return max(measure(run.text, font_size), measure(run.reading, font_size * PINYIN_SIZE_RATIO))


def _wrap(runs, max_width, measure, font_size, page_id):
    """保留英文单词并避免中文标点出现在不合适的行边。"""
    lines = []
    current = []
    used = 0.0
    for run in runs:
        if run.text == "\n":
            lines.append(current)
            current, used = [], 0.0
            continue
        width = _run_width(run, measure, font_size)
        if width > max_width + FLOAT_TOLERANCE_PT:
            raise ValueError(f"页面 {page_id} 文字溢出：单词或拼音超出文字区宽度。")
        if current and used + width > max_width + FLOAT_TOLERANCE_PT:
            # 将左括号或紧邻右标点的前字移至下一行，避免孤立标点。
            moved = []
            if run.text[:1] in CLOSING_PUNCTUATION or current[-1].text[-1:] in OPENING_PUNCTUATION:
                moved = [current.pop()]
            if not current and moved:
                raise ValueError(f"页面 {page_id} 文字溢出：文字区无法容纳连贯标点。")
            lines.append(current)
            current = moved
            used = sum(_run_width(item, measure, font_size) for item in current)
            if used + width > max_width + FLOAT_TOLERANCE_PT:
                raise ValueError(f"页面 {page_id} 文字溢出：连贯文字超出文字区宽度。")
        # 自动换行后不保留英文行首空格；显式换行与正文内容保持不变。
        if not current and run.text.isspace():
            continue
        current.append(run)
        used += width
    if current:
        lines.append(current)
    return lines


def _plain_runs(text):
    """英文按单词分组，汉字逐字换行，显式换行保留。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    tokens = re.findall(r"\n|[^\S\n]+|[A-Za-z0-9]+(?:['’_-][A-Za-z0-9]+)*|[^\sA-Za-z0-9]", normalized)
    return [Run(token) for token in tokens]


def _layout_page(book, page, width, text_height, font_name, pdfmetrics):
    """预先测量本页全部语言，空间不足即中止整本输出。"""
    settings = book["format"]
    font_size = effective_presentation(book, page).font_size_pt
    ascent, descent = pdfmetrics.getAscentDescent(font_name, font_size)
    base_height = max(font_size * settings["line_spacing"], ascent - descent)
    ruby_size = font_size * PINYIN_SIZE_RATIO
    ruby_ascent, ruby_descent = pdfmetrics.getAscentDescent(font_name, ruby_size)
    ruby_height = ruby_ascent - ruby_descent + font_size * PINYIN_GAP_RATIO
    measure = lambda value, size: pdfmetrics.stringWidth(value, font_name, size)
    languages = book["brief"]["languages"]
    blocks = []
    if "pinyin" in languages:
        runs = [Run(item["char"], item["reading"]) for item in page["text"]["pinyin"]]
        lines = _wrap(runs, width, measure, font_size, page["id"])
        blocks.append(Block(lines, base_height + ruby_height, ruby_height, ascent, ruby_ascent))
    else:
        lines = _wrap(_plain_runs(page["text"]["zh"]), width, measure, font_size, page["id"])
        blocks.append(Block(lines, base_height, 0.0, ascent, 0.0))
    if "en" in languages:
        lines = _wrap(_plain_runs(page["text"]["en"]), width, measure, font_size, page["id"])
        blocks.append(Block(lines, base_height, 0.0, ascent, 0.0))
    blocks = [block for block in blocks if block.lines]
    gap = font_size * LANGUAGE_GAP_RATIO
    required = sum(block.height for block in blocks) + gap * max(len(blocks) - 1, 0)
    available = text_height - font_size * TEXT_IMAGE_GAP_RATIO
    if required > available + FLOAT_TOLERANCE_PT:
        raise ValueError(
            f"页面 {page['id']} 文字溢出：需要 {required:.1f} pt，文字区可用 {available:.1f} pt。"
            "请调整已确认的分页、文字区或正文，不会自动缩小字号或裁字。"
        )
    return blocks


def _draw_blocks(canvas, blocks, x, top, font_name, font_size, pdfmetrics,
                 text_align="left", text_width=0):
    """逐行绘制已测量文本，拼音与对应汉字共享单元格中心。"""
    measure = lambda value, size: pdfmetrics.stringWidth(value, font_name, size)
    y = top
    for index, block in enumerate(blocks):
        if index:
            y -= font_size * LANGUAGE_GAP_RATIO
        for line in block.lines:
            cursor = x
            if text_align == "center":
                # 拼音按单元格宽度、普通正文按真实整行宽度，确保两者都居中。
                line_width = (sum(_run_width(run, measure, font_size) for run in line)
                              if block.ruby_height else
                              measure("".join(run.text for run in line).rstrip(), font_size))
                cursor += (text_width - line_width) / 2
            baseline = y - block.ruby_height - block.ascent
            if block.ruby_height:
                for run in line:
                    width = _run_width(run, measure, font_size)
                    canvas.setFont(font_name, font_size)
                    canvas.drawCentredString(cursor + width / 2, baseline, run.text)
                    if run.reading:
                        canvas.setFont(font_name, font_size * PINYIN_SIZE_RATIO)
                        canvas.drawCentredString(cursor + width / 2, y - block.ruby_ascent, run.reading)
                    cursor += width
            else:
                # 整行绘制保留 PDF 中的中文句子和英文单词，便于读回及辅助阅读。
                canvas.setFont(font_name, font_size)
                canvas.drawString(cursor, baseline, "".join(run.text for run in line).rstrip())
            y -= block.line_height


def preflight_text(book, font_path):
    """只测量已通过结构检查的项目，生图前一次报告全部文字框溢出。"""
    _, _, _, pdfmetrics, font_class, _ = _dependencies()
    font_name = _load_font(book, font_path, pdfmetrics, font_class)
    settings = book["format"]
    width = (settings["width_mm"] - settings["margin_mm"] * 2) * POINTS_PER_MM
    height = (settings["height_mm"] - settings["margin_mm"] * 2) * POINTS_PER_MM
    errors = []
    by_id = {page["id"]: page for page in book["pages"]}
    for page_id in book["page_order"]:
        page = by_id[page_id]
        region = page_regions(book, page).text
        if region is None:
            continue
        try:
            _layout_page(book, page, width * region[2], height * region[3], font_name, pdfmetrics)
        except ValueError as error:
            errors.append(str(error))
    return errors


def write_pdf(book, root, destination, font_path):
    """先完成字体与全部页面测量，再创建 PDF 文件并等比绘制插画。"""
    Image, ImageOps, ImageReader, pdfmetrics, font_class, canvas_class = _dependencies()
    font_name = _load_font(book, font_path, pdfmetrics, font_class)
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor

    settings = book["format"]
    width = settings["width_mm"] * mm
    height = settings["height_mm"] * mm
    margin = settings["margin_mm"] * mm
    content_width = width - margin * 2
    content_height = height - margin * 2
    by_id = {page["id"]: page for page in book["pages"]}
    pages = [by_id[page_id] for page_id in book["page_order"]]
    presentations = [effective_presentation(book, page) for page in pages]
    regions = [page_regions(book, page) for page in pages]
    layouts = [_layout_page(book, page, content_width * area.text[2],
                            content_height * area.text[3], font_name, pdfmetrics)
               if area.text else [] for page, area in zip(pages, regions)]
    canvas = canvas_class(str(destination), pagesize=(width, height), pageCompression=1)
    canvas.setTitle(book["title"])
    # 工具只能记录为制作软件，用户未提供作者时不代为署名。
    canvas.setAuthor("")
    canvas.setCreator("绘本创作工作室")
    for page, blocks, presentation, area in zip(pages, layouts, presentations, regions):
        # 纸色覆盖整页，文字与插画保持独立区域，不遮挡插画或修改源图片。
        canvas.setFillColor(HexColor(presentation.paper_color))
        canvas.rect(0, 0, width, height, fill=1, stroke=0)
        # 归一化原点在左上，ReportLab 原点在左下；按整块区域换算，保持图像等比。
        image_x = margin + content_width * area.image[0]
        image_y = height - margin - content_height * (area.image[1] + area.image[3])
        image_width = content_width * area.image[2]
        image_height = content_height * area.image[3]
        try:
            with Image.open(safe_asset(root, page["image"]["path"])) as source:
                # 手机照片可能带旋转元数据，先应用方向再计算完整图片比例。
                oriented = ImageOps.exif_transpose(source)
                canvas.drawImage(ImageReader(oriented), image_x, image_y,
                                 width=image_width, height=image_height,
                                 preserveAspectRatio=True, anchor="c", mask="auto")
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
            raise ValueError(f"页面 {page['id']} 图片无法安全解码或绘制：{error}") from error
        if area.text:
            font_size = presentation.font_size_pt
            text_x = margin + content_width * area.text[0]
            text_width = content_width * area.text[2]
            text_height = content_height * area.text[3]
            top = height - margin - content_height * area.text[1]
            if presentation.layout in ("image-top", "positioned"):
                top -= font_size * TEXT_IMAGE_GAP_RATIO
            elif presentation.layout == "title-top":
                required = (sum(block.height for block in blocks)
                            + font_size * LANGUAGE_GAP_RATIO * max(len(blocks) - 1, 0))
                top -= (text_height - font_size * TEXT_IMAGE_GAP_RATIO - required) / 2
            canvas.setFillColor(HexColor(presentation.text_color))
            _draw_blocks(canvas, blocks, text_x, top, font_name, font_size, pdfmetrics,
                         presentation.text_align, text_width)
        canvas.showPage()
    canvas.save()
