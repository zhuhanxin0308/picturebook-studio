"""仅用标准库生成无脚本、图片内嵌的离线绘本阅读文件。"""

import base64
from html import escape

from book_model import safe_asset
from presentation import (
    MIN_SCREEN_FONT_PX, MIN_SCREEN_MARGIN_PX, MIN_SCREEN_TITLE_PX, POINTS_PER_MM,
    SCREEN_BACKGROUND_COLOR, SCREEN_GUTTER_PX, SCREEN_PAGE_GAP_PX,
    STACKED_ART_BREAKPOINT_PX, TEXT_IMAGE_GAP_RATIO, effective_presentation, page_regions,
)


def _paragraph(text):
    """转义后保留段落换行，正文不能变成 HTML 标签。"""
    return escape(text, quote=True).replace("\n", "<br>")


def _ruby(items):
    """使用已校对的逐字拼音，不在排版阶段猜测多音字。"""
    result = []
    for item in items:
        char = _paragraph(item["char"])
        reading = escape(item["reading"], quote=True)
        result.append(f"<ruby>{char}<rt>{reading}</rt></ruby>" if reading else char)
    return "".join(result)


def write_html(book, root, destination):
    """保留原图比例；长正文允许页面延展，绝不隐藏或截断文字。"""
    layout = book["format"]
    width = layout["width_mm"]
    height = layout["height_mm"]
    margin = layout["margin_mm"]
    text_height = (height - margin * 2) * layout["text_ratio"]
    image_height = (height - margin * 2) - text_height
    content_width = width - margin * 2
    languages = book["brief"]["languages"]
    by_id = {page["id"]: page for page in book["pages"]}
    sections = []
    for page_id in book["page_order"]:
        page = by_id[page_id]
        presentation = effective_presentation(book, page)
        regions = page_regions(book, page)
        content = safe_asset(root, page["image"]["path"]).read_bytes()
        media_type = "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
        encoded = base64.b64encode(content).decode("ascii")
        text = page["text"]
        chinese = _ruby(text["pinyin"]) if "pinyin" in languages else _paragraph(text["zh"])
        english = f'<p class="english" lang="en">{_paragraph(text["en"])}</p>' if "en" in languages else ""
        illustration = (
            f'<div class="illustration"><img src="data:{media_type};base64,{encoded}" '
            f'alt="{escape(page["action"], quote=True)}"></div>'
        )
        story_text = (f'<div class="story-text"><p lang="zh-CN">{chinese}</p>{english}</div>'
                      if regions.text else "")
        minimum_font = MIN_SCREEN_TITLE_PX if page["kind"] == "cover" else MIN_SCREEN_FONT_PX
        screen_font_vw = presentation.font_size_pt / (width * POINTS_PER_MM) * 100
        style = (f"--font-size: {presentation.font_size_pt}pt; --screen-font: {screen_font_vw}vw; "
                 f"--container-font: {screen_font_vw}cqw; "
                 f"--minimum-font: {minimum_font}px; text-align: {presentation.text_align}; "
                 f"color: {presentation.text_color}; background: {presentation.paper_color};")
        art_page = presentation.layout in ("positioned", "image-only")
        if art_page:
            # 使用校验后的数值生成样式，用户不能注入任意 CSS 或越出页面。
            for prefix, region in (("image", regions.image), ("text", regions.text)):
                if region:
                    for field, number in zip(("left", "top", "width", "height"), region):
                        style += f" --{prefix}-{field}: {number * 100:.12g}%;"
            blocks = f'<div class="page-content">{illustration}{story_text}</div>'
        else:
            blocks = (illustration + story_text if presentation.layout == "image-top"
                      else story_text + illustration)
        classes = "page art-page" if art_page else "page"
        sections.append(
            f'<section class="{classes}" data-page-id="{escape(page_id, quote=True)}" '
            f'data-layout="{presentation.layout}" data-kind="{escape(page["kind"], quote=True)}" style="{style}" '
            f'aria-label="{escape(page_id, quote=True)}">{blocks}</section>'
        )
    title = escape(book["title"], quote=True)
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{title}</title><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 0 {SCREEN_GUTTER_PX}px; background: {SCREEN_BACKGROUND_COLOR}; font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }}
main {{ width: {width}mm; max-width: 100%; margin: auto; container-type: inline-size; }}
.page {{ width: 100%; min-height: min({height}mm, {height / width * 100}vw); padding: min({margin}mm, max({MIN_SCREEN_MARGIN_PX}px, {margin / width * 100}vw)); margin: {SCREEN_PAGE_GAP_PX}px auto; break-after: page; }}
.illustration {{ width: 100%; aspect-ratio: {content_width} / {image_height}; display: flex; align-items: center; justify-content: center; }}
.illustration img {{ display: block; width: 100%; height: 100%; object-fit: contain; min-width: 0; }}
.story-text {{ min-height: min({text_height}mm, {text_height / width * 100}vw); font-size: clamp(var(--minimum-font), var(--screen-font), var(--font-size)); line-height: {layout["line_spacing"]}; overflow-wrap: anywhere; display: flex; flex-direction: column; }}
[data-layout="image-top"] .story-text {{ padding-top: {TEXT_IMAGE_GAP_RATIO}em; }}
[data-layout="text-top"] .story-text, [data-layout="title-top"] .story-text {{ padding-bottom: {TEXT_IMAGE_GAP_RATIO}em; }}
[data-layout="title-top"] .story-text {{ justify-content: center; }}
p {{ margin: 0; white-space: pre-wrap; }}
p + p {{ margin-top: 0.5em; }}
ruby {{ ruby-align: center; }}
rt {{ font-size: 0.5em; line-height: 1.2; }}
@supports (font-size: 1cqw) {{
  /* 字号随实际书页宽度缩放，避免视口边距使定位文字比预留区域更大。 */
  .story-text {{ font-size: clamp(var(--minimum-font), var(--container-font), var(--font-size)); }}
}}
.page-content {{ position: relative; width: 100%; aspect-ratio: {content_width} / {height - margin * 2}; }}
.art-page .illustration {{ position: absolute; left: var(--image-left); top: var(--image-top); width: var(--image-width); height: var(--image-height); aspect-ratio: auto; }}
.art-page .story-text {{ position: absolute; left: var(--text-left); top: var(--text-top); width: var(--text-width); height: var(--text-height); min-height: 0; padding-top: {TEXT_IMAGE_GAP_RATIO}em; }}
@media screen and (max-width: {STACKED_ART_BREAKPOINT_PX}px) {{
  /* 窄屏把艺术布局展开为正常阅读顺序，保留字号并显示全部文字。 */
  .art-page .page-content {{ aspect-ratio: auto; }}
  .art-page .illustration {{ position: static; width: 100%; height: auto; }}
  .art-page .illustration img {{ height: auto; }}
  .art-page .story-text {{ position: static; width: 100%; height: auto; }}
  .art-page[data-kind="cover"] .page-content {{ display: flex; flex-direction: column; }}
  .art-page[data-kind="cover"] .story-text {{ order: -1; padding-top: 0; padding-bottom: {TEXT_IMAGE_GAP_RATIO}em; }}
}}
@media print {{
  @page {{ size: {width}mm {height}mm; margin: 0; }}
  body {{ padding: 0; background: white; }}
  main {{ width: {width}mm; max-width: none; }}
  .page {{ width: {width}mm; min-height: {height}mm; padding: {margin}mm; margin: 0; }}
  .illustration {{ height: {image_height}mm; aspect-ratio: auto; }}
  .story-text {{ min-height: {text_height}mm; font-size: var(--font-size); }}
  .art-page .page-content {{ height: {height - margin * 2}mm; aspect-ratio: auto; }}
  .art-page .story-text {{ min-height: 0; }}
}}
</style></head><body><main aria-label="{title}">{"".join(sections)}</main></body></html>'''
    destination.write_text(document, encoding="utf-8")
