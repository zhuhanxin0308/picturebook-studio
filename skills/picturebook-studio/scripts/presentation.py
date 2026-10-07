"""校验并解析独立排版层，供 HTML 与 PDF 使用同一套页面设置。"""

import math
import re
from dataclasses import dataclass

PAGE_LAYOUTS = {"image-top", "text-top", "title-top", "positioned", "image-only"}
TEXT_ALIGNMENTS = {"left", "center"}
PRESENTATION_FIELDS = {"layout", "text_align", "font_size_pt", "text_color", "paper_color",
                       "image_box", "text_box", "allow_overlap"}
DEFAULT_LAYOUT = "image-top"
DEFAULT_COVER_LAYOUT = "title-top"
DEFAULT_TEXT_ALIGN = "left"
DEFAULT_COVER_TEXT_ALIGN = "center"
DEFAULT_BACKCOVER_TEXT_ALIGN = "center"
DEFAULT_TEXT_COLOR = "#202020"
DEFAULT_PAPER_COLOR = "#FFFFFF"
COVER_FONT_SIZE_RATIO = 1.5
POINTS_PER_MM = 72 / 25.4
MIN_SCREEN_FONT_PX = 20
MIN_SCREEN_TITLE_PX = 28
MIN_SCREEN_MARGIN_PX = 8
SCREEN_GUTTER_PX = 12
SCREEN_PAGE_GAP_PX = 24
SCREEN_BACKGROUND_COLOR = "#ECE9E3"
TEXT_IMAGE_GAP_RATIO = 0.5
REGION_TOLERANCE = 1e-9
STACKED_ART_BREAKPOINT_PX = 640


@dataclass(frozen=True)
class PagePresentation:
    """已验证的有效值，不包含改变插画画幅或裁切的配置。"""

    layout: str
    text_align: str
    font_size_pt: float
    text_color: str
    paper_color: str
    image_box: tuple | None = None
    text_box: tuple | None = None
    allow_overlap: bool = False


@dataclass(frozen=True)
class PageRegions:
    """安全内容区中的归一化矩形，原点在左上，避免两种导出坐标分歧。"""

    image: tuple
    text: tuple | None


def _valid_box(box):
    """拒绝超界、非有限数、布尔值及无法绘制的空矩形。"""
    if not isinstance(box, list) or len(box) != 4:
        return False
    try:
        if any(type(number) not in (int, float) or not math.isfinite(number) for number in box):
            return False
        x, y, width, height = box
        return (x >= 0 and y >= 0 and width > 0 and height > 0
                and x + width <= 1 + REGION_TOLERANCE
                and y + height <= 1 + REGION_TOLERANCE)
    except OverflowError:
        return False


def _boxes_overlap(first, second):
    """边界相接不算遮挡，只把具有实际面积的交集视为叠放。"""
    return (min(first[0] + first[2], second[0] + second[2])
            - max(first[0], second[0]) > REGION_TOLERANCE
            and min(first[1] + first[3], second[1] + second[3])
            - max(first[1], second[1]) > REGION_TOLERANCE)


def validate_presentation(value, label="presentation"):
    """拒绝非法选项与样式注入；省略字段时由有效值解析补足。"""
    if not isinstance(value, dict):
        return [f"{label} 必须是对象"]
    errors = []
    unknown = set(value) - PRESENTATION_FIELDS
    if unknown:
        errors.append(f"{label} 包含不支持的字段：{', '.join(sorted(map(str, unknown)))}")
    for field, choices in (("layout", PAGE_LAYOUTS), ("text_align", TEXT_ALIGNMENTS)):
        if field in value and (not isinstance(value[field], str) or value[field] not in choices):
            errors.append(f"{label}.{field} 必须为 {'、'.join(sorted(choices))}")
    if "font_size_pt" in value:
        size = value["font_size_pt"]
        try:
            valid_size = type(size) in (int, float) and math.isfinite(size) and size > 0
        except OverflowError:
            # 过大的 JSON 整数无法进入字体测量，按非法字号处理而非抛出堆栈。
            valid_size = False
        if not valid_size:
            errors.append(f"{label}.font_size_pt 必须是大于 0 的有限数值，不能是布尔值")
    for field in ("text_color", "paper_color"):
        if field in value and (not isinstance(value[field], str)
                               or re.fullmatch(r"#[0-9a-fA-F]{6}", value[field]) is None):
            errors.append(f"{label}.{field} 必须是 # 开头的六位十六进制颜色")
    if value.get("layout") == "positioned":
        valid_boxes = {}
        for field in ("image_box", "text_box"):
            box = value.get(field)
            if not _valid_box(box):
                errors.append(f"{label}.{field} 必须为安全内容区内的 [左, 上, 宽, 高] 比例矩形")
            else:
                valid_boxes[field] = box
        overlap = value.get("allow_overlap", False)
        if type(overlap) is not bool:
            errors.append(f"{label}.allow_overlap 必须是布尔值")
        elif len(valid_boxes) == 2 and not overlap and _boxes_overlap(*valid_boxes.values()):
            errors.append(f"{label} 图文区域重叠；只有已设计并核验文字留白时才能明确允许叠放")
    elif any(field in value for field in ("image_box", "text_box", "allow_overlap")):
        errors.append(f"{label} 只有 positioned 布局可以配置矩形与叠放选项")
    return errors


def validate_page_presentation(page, label="presentation"):
    """无字页必须真的没有正文，不能通过布局配置把任何语言藏起来。"""
    value = page.get("presentation", {})
    errors = validate_presentation(value, label)
    if isinstance(value, dict) and value.get("layout") == "image-only":
        text = page.get("text", {})
        if isinstance(text, dict) and any(text.get(language) for language in ("zh", "en", "pinyin")):
            errors.append(f"{label} 的 image-only 无字页不能包含待显示的正文")
    return errors


def effective_presentation(book, page):
    """旧项目自动获得封面标题层级，显式单页设置优先于默认值。"""
    value = page.get("presentation", {})
    errors = validate_page_presentation(page, f"页面 {page['id']} 的 presentation")
    if errors:
        raise ValueError("；".join(errors))
    cover = page["kind"] == "cover"
    if cover:
        alignment = DEFAULT_COVER_TEXT_ALIGN
    elif page["kind"] == "backcover":
        alignment = DEFAULT_BACKCOVER_TEXT_ALIGN
    else:
        alignment = DEFAULT_TEXT_ALIGN
    return PagePresentation(
        layout=value.get("layout", DEFAULT_COVER_LAYOUT if cover else DEFAULT_LAYOUT),
        text_align=value.get("text_align", alignment),
        font_size_pt=value.get("font_size_pt", book["format"]["font_size_pt"]
                               * COVER_FONT_SIZE_RATIO if cover else book["format"]["font_size_pt"]),
        text_color=value.get("text_color", DEFAULT_TEXT_COLOR),
        paper_color=value.get("paper_color", DEFAULT_PAPER_COLOR),
        image_box=tuple(value["image_box"]) if "image_box" in value else None,
        text_box=tuple(value["text_box"]) if "text_box" in value else None,
        allow_overlap=value.get("allow_overlap", False),
    )


def page_regions(book, page):
    """一次解析全页矩形，PDF 和 HTML 共用安全边距内的同一布局。"""
    presentation = effective_presentation(book, page)
    if presentation.layout == "positioned":
        return PageRegions(presentation.image_box, presentation.text_box)
    if presentation.layout == "image-only":
        return PageRegions((0, 0, 1, 1), None)
    ratio = book["format"]["text_ratio"]
    if presentation.layout == "image-top":
        return PageRegions((0, 0, 1, 1 - ratio), (0, 1 - ratio, 1, ratio))
    return PageRegions((0, ratio, 1, 1 - ratio), (0, 0, 1, ratio))
