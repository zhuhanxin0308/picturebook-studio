"""测试使用的完整小故事；所有图片均为真实编码的 PNG。"""

import struct
import zlib
from pathlib import Path


def write_png(path: Path) -> None:
    """创建可解码的四像素插画，避免测试依赖外部素材。"""
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
            ">I", zlib.crc32(kind + payload) & 0xFFFFFFFF
        )

    header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\xdc\xe9\xd7" * 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(row * 2)) + chunk(b"IEND", b""))


def make_book() -> dict:
    """返回可正式制作的最小完整故事，包含道具的真实连续状态。"""
    pages = []
    definitions = [
        ("cover", "cover", "小兔的种子", "小兔展示一颗种子", []),
        ("plant", "story", "小兔种下种子。", "小兔把种子放入土中", ["cover"]),
        ("back", "backcover", "明天见。", "小兔望着埋有种子的花盆", ["plant"]),
    ]
    for page_id, kind, text, action, dependencies in definitions:
        pages.append({
            "id": page_id, "kind": kind, "scene_id": "garden",
            "character_ids": ["rabbit"], "depends_on": dependencies,
            "purpose": "展示种植故事的一个完整环节", "action": action,
            "visual_evidence": ["小兔", "花盆", "种子对应的状态"],
            "text": {"zh": text}, "continuity": {},
            "prompt": f"水彩儿童绘本，无文字。{action}。留出下方文字区域。",
        })
    pages[0]["continuity"] = {"seed": {"before": "小兔手中", "after": "小兔手中"}}
    pages[1]["continuity"] = {"seed": {"before": "小兔手中", "after": "花盆土中"}}
    pages[2]["continuity"] = {"seed": {"before": "花盆土中", "after": "花盆土中"}}
    return {
        "schema_version": 1, "book_id": "rabbit-seed", "title": "小兔的种子",
        "brief": {"age_range": "3–6 岁", "languages": ["zh"], "body_page_count": 1},
        "format": {"width_mm": 210, "height_mm": 210, "margin_mm": 12,
                   "font_size_pt": 18, "line_spacing": 1.4, "text_ratio": 0.25},
        "approvals": {"brief": "用户已确认创作要求", "story": "用户已确认完整故事",
                      "visual": "用户已确认代表页及视觉规范"},
        "visual": {"description": "温柔水彩、保留纸纹", "palette": ["#dce9d7", "#fff9ed"]},
        "characters": {"rabbit": {"description": "短耳白兔，蓝色背带裤"}},
        "scenes": {"garden": {"description": "晨光中的小花园，陶制花盆"}},
        "props": {"seed": {"description": "一颗棕色豆种", "initial_state": "小兔手中"}},
        "page_order": [page["id"] for page in pages], "pages": pages,
    }


def make_complete_book(root: Path, languages=None) -> dict:
    """为排版和验收测试绑定真实图片、输入签名及人工审查记录。"""
    from book_model import file_digest, page_fingerprint

    book = make_book()
    if languages is not None:
        book["brief"]["languages"] = list(languages)
    readings = {"小": "xiǎo", "兔": "tù", "的": "de", "种": "zhǒng", "子": "zi",
                "下": "xià", "明": "míng", "天": "tiān", "见": "jiàn"}
    for page in book["pages"]:
        if "en" in book["brief"]["languages"]:
            page["text"]["en"] = {"cover": "Rabbit's Seed", "plant": "Rabbit plants a seed.",
                                  "back": "See you tomorrow."}[page["id"]]
        if "pinyin" in book["brief"]["languages"]:
            page["text"]["pinyin"] = [
                {"char": char, "reading": ("zhòng" if page["id"] == "plant" and index == 2
                                             else readings.get(char, ""))}
                for index, char in enumerate(page["text"]["zh"])
            ]
    # 必须先补齐全书语言字段，签名计算会校验整本项目的结构。
    for page in book["pages"]:
        relative = f"images/{page['id']}.png"
        path = root / relative
        write_png(path)
        fingerprint = page_fingerprint(book, page["id"], root)
        page["image"] = {"path": relative, "input_fingerprint": fingerprint}
        page["qa"] = {"input_fingerprint": fingerprint, "image_sha256": file_digest(path),
                      "reviewer": "测试审校员", "notes": "逐页检查图文与角色状态。",
                      "checks": {name: True for name in (
                          "identity", "continuity", "text_match", "anatomy", "style", "composition")}}
    return book
