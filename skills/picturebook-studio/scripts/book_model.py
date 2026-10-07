"""绘本项目校验、内容签名及恢复状态；只读取数据和素材。"""

import hashlib
import json
import math
import struct
import unicodedata
import zlib
from pathlib import Path, PureWindowsPath

from presentation import validate_page_presentation

SCHEMA_VERSION = 1
STAGES = ("draft", "ready", "complete")
PAGE_KINDS = {"cover", "frontmatter", "story", "backmatter", "backcover"}
LANGUAGES = {"zh", "pinyin", "en"}
QA_CHECKS = ("identity", "continuity", "text_match", "anatomy", "style", "composition")
FORMAT_FIELDS = ("width_mm", "height_mm", "margin_mm", "font_size_pt", "line_spacing", "text_ratio")
COMPOSITION_FIELDS = ("width_mm", "height_mm", "margin_mm", "text_ratio")
DIGEST_BLOCK_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 128 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    # 布尔值属于 Python 整数子类，必须单独拒绝。
    return type(value) in (int, float) and (not isinstance(value, float) or math.isfinite(value))


def safe_asset(root, relative_path):
    """拒绝跨平台路径逃逸、远程地址及符号链接逃逸。"""
    if not _nonempty(relative_path) or any(ord(char) < 32 for char in relative_path):
        raise ValueError("素材路径必须是非空本地相对路径")
    normalized = relative_path.replace("\\", "/")
    windows = PureWindowsPath(relative_path)
    pieces = normalized.split("/")
    if (windows.drive or windows.root or normalized.startswith("/") or ":" in normalized
            or ".." in pieces or normalized in (".", "./")):
        raise ValueError("素材路径不得指向项目外、绝对路径或远程地址")
    try:
        base = Path(root).resolve()
        asset = (base / normalized).resolve()
        if asset == base or not asset.is_relative_to(base):
            raise ValueError("素材路径或符号链接超出项目目录")
        return asset
    except (OSError, RuntimeError, TypeError) as exc:
        raise ValueError("无法解析素材路径") from exc


def file_digest(path):
    """流式读取文件，摘要不依赖路径和修改时间。"""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(DIGEST_BLOCK_BYTES), b""):
                digest.update(block)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("素材文件无法读取") from exc
    return digest.hexdigest()


def _verify_png(data):
    """验证块结构、校验和及压缩流；像素语义仍交由看图和排版解码检查。"""
    offset, header, compressed, ended = len(PNG_SIGNATURE), None, [], False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("PNG 数据块不完整")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ValueError("PNG 数据块校验和错误")
        if header is None and kind != b"IHDR":
            raise ValueError("PNG 缺少首部")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("PNG 首部错误")
            header = struct.unpack(">IIBBBBB", payload)
            width, height, depth, color, compression, filtering, interlace = header
            if (not width or not height or width * height > MAX_IMAGE_PIXELS
                    or color not in PNG_DEPTHS or depth not in PNG_DEPTHS[color]
                    or compression != 0 or filtering != 0 or interlace not in (0, 1)):
                raise ValueError("PNG 尺寸、色彩或编码参数不受支持")
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            if length or not compressed:
                raise ValueError("PNG 缺少像素数据或结束块错误")
            ended = True
            offset = end
            break
        offset = end
    if not ended or offset != len(data):
        raise ValueError("PNG 未完整结束或包含异常附加数据")
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(b"".join(compressed), MAX_DECOMPRESSED_BYTES + 1)
    except zlib.error as exc:
        raise ValueError("PNG 压缩像素数据损坏") from exc
    if len(pixels) > MAX_DECOMPRESSED_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError("PNG 像素数据过大、不完整或编码异常")
    width, height, depth, color, _, _, interlace = header
    if not interlace:
        row_size = (width * PNG_CHANNELS[color] * depth + 7) // 8 + 1
        if len(pixels) != row_size * height or any(pixels[n] > 4 for n in range(0, len(pixels), row_size)):
            raise ValueError("PNG 像素行或滤波参数错误")


def _verify_jpeg(data):
    """检查 JPEG 段、尺寸与图像结束标记；不声称完成熵解码。"""
    if not data.endswith(b"\xff\xd9"):
        raise ValueError("JPEG 缺少结束标记")
    offset, has_frame = 2, False
    while offset < len(data) - 2:
        if data[offset] != 0xFF:
            raise ValueError("JPEG 段标记错误")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0x00, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            raise ValueError("JPEG 段顺序错误")
        if offset + 2 > len(data):
            break
        length = struct.unpack_from(">H", data, offset)[0]
        if length < 2 or offset + length > len(data):
            raise ValueError("JPEG 数据段不完整")
        if marker in JPEG_SOF:
            if length < 8:
                raise ValueError("JPEG 帧首部错误")
            height, width = struct.unpack_from(">HH", data, offset + 3)
            if not width or not height or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("JPEG 图像尺寸错误或过大")
            has_frame = True
        if marker == 0xDA:
            if not has_frame or length < 6 or offset + length >= len(data) - 2:
                raise ValueError("JPEG 缺少帧或扫描数据")
            return
        offset += length
    raise ValueError("JPEG 缺少有效图像扫描")


def _image_path(root, relative_path):
    path = safe_asset(root, relative_path)
    try:
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("图片不存在、不是文件或超过 128 MiB")
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError("图片无法读取") from exc
    suffix = path.suffix.lower()
    if suffix == ".png" and data.startswith(PNG_SIGNATURE):
        _verify_png(data)
    elif suffix in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8"):
        _verify_jpeg(data)
    else:
        raise ValueError("图片内容与 PNG/JPEG 扩展名不匹配")
    return path


def _mapping(value, label, errors):
    if not isinstance(value, dict):
        errors.append(f"{label} 必须是对象")
        return {}
    return value


def _string_list(value, label, errors, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty) or any(not _nonempty(item) for item in value):
        errors.append(f"{label} 必须是{'可空' if allow_empty else '非空'}字符串列表")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{label} 不得重复")
    return value


def _reference(value, root, label, errors):
    if "reference" not in value:
        return
    try:
        path = safe_asset(root, value["reference"])
        if not path.is_file():
            errors.append(f"{label} 的参考素材不存在或不是文件")
    except ValueError as exc:
        errors.append(f"{label}：{exc}")


def _entities(book, root, errors):
    results = {}
    for section in ("characters", "scenes", "props"):
        result = _mapping(book.get(section), section, errors)
        valid = {}
        for identifier, entry in result.items():
            label = f"{section}.{identifier}"
            if not _nonempty(identifier):
                errors.append(f"{section} 的 ID 必须是非空字符串")
            data = _mapping(entry, label, errors)
            if not _nonempty(data.get("description")):
                errors.append(f"{label}.description 必须是非空字符串")
            if section == "props" and not _nonempty(data.get("initial_state")):
                errors.append(f"{label}.initial_state 必须是非空字符串")
            _reference(data, root, label, errors)
            if isinstance(identifier, str):
                valid[identifier] = data
        results[section] = valid
    return results


def _validate_format(book, errors):
    layout = _mapping(book.get("format"), "format", errors)
    valid = {}
    for field in FORMAT_FIELDS:
        number = layout.get(field)
        if not _number(number):
            errors.append(f"format.{field} 必须是有限数值，不能是布尔值")
        else:
            valid[field] = number
    for field in ("width_mm", "height_mm", "font_size_pt"):
        if field in valid and valid[field] <= 0:
            errors.append(f"format.{field} 必须大于 0")
    margin = valid.get("margin_mm")
    if margin is not None:
        if margin < 0:
            errors.append("format.margin_mm 不得为负数")
        if all(field in valid for field in ("width_mm", "height_mm")):
            if margin * 2 >= min(valid["width_mm"], valid["height_mm"]):
                errors.append("页边距必须小于短边的一半")
    if "line_spacing" in valid and valid["line_spacing"] < 1:
        errors.append("format.line_spacing 至少为 1")
    if "text_ratio" in valid and not 0 < valid["text_ratio"] < 1:
        errors.append("format.text_ratio 必须大于 0 且小于 1")


def _validate_text(page, languages, label, errors):
    text = _mapping(page.get("text"), f"{label}.text", errors)
    for language in ("zh", "en"):
        if language in languages and not isinstance(text.get(language), str):
            errors.append(f"{label}.text.{language} 必须是字符串")
    if "pinyin" not in languages:
        return
    syllables = text.get("pinyin")
    if not isinstance(syllables, list):
        errors.append(f"{label}.text.pinyin 必须是逐字对象列表")
        return
    characters = []
    for syllable in syllables:
        if (not isinstance(syllable, dict) or not isinstance(syllable.get("char"), str)
                or len(syllable["char"]) != 1 or not isinstance(syllable.get("reading"), str)):
            errors.append(f"{label} 的拼音项必须包含单个 char 字符与 reading 字符串")
        else:
            characters.append(syllable["char"])
            character_name = unicodedata.name(syllable["char"], "")
            if character_name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")):
                if not _nonempty(syllable["reading"]):
                    errors.append(f"{label} 的汉字 {syllable['char']} 缺少已校对读音")
    if "".join(characters) != text.get("zh"):
        errors.append(f"{label} 的拼音逐字内容与中文不一致")


def _validate_pages(book, root, stage, languages, entities, body_count, errors):
    order = _string_list(book.get("page_order"), "page_order", errors)
    pages = book.get("pages")
    if not isinstance(pages, list) or not pages:
        errors.append("pages 必须是非空页面列表")
        return
    by_id = {}
    kinds = []
    for index, item in enumerate(pages):
        label = f"pages[{index}]"
        page = _mapping(item, label, errors)
        identifier = page.get("id")
        if not _nonempty(identifier):
            errors.append(f"{label}.id 必须是非空字符串")
        elif identifier in by_id:
            errors.append(f"页面 ID 重复：{identifier}")
        else:
            by_id[identifier] = page
        kind = page.get("kind")
        if not isinstance(kind, str) or kind not in PAGE_KINDS:
            errors.append(f"{label}.kind 不在支持的页面类型中")
        kinds.append(kind)
        for field in ("purpose", "action"):
            if not _nonempty(page.get(field)):
                errors.append(f"{label}.{field} 必须是非空字符串")
        _string_list(page.get("visual_evidence"), f"{label}.visual_evidence", errors)
        scene = page.get("scene_id")
        if not isinstance(scene, str) or scene not in entities["scenes"]:
            errors.append(f"{label}.scene_id 未引用已有场景")
        characters = _string_list(page.get("character_ids"), f"{label}.character_ids", errors, True)
        for character in characters:
            if character not in entities["characters"]:
                errors.append(f"{label} 引用了不存在的角色：{character}")
        _string_list(page.get("depends_on"), f"{label}.depends_on", errors, True)
        _mapping(page.get("continuity"), f"{label}.continuity", errors)
        _validate_text(page, languages, label, errors)
        if "presentation" in page:
            errors.extend(validate_page_presentation(page, f"{label}.presentation"))
        if (stage != "draft" or "prompt" in page) and not _nonempty(page.get("prompt")):
            errors.append(f"{label}.prompt 必须是正式完整的非空提示词")
        if "image" in page:
            image = _mapping(page["image"], f"{label}.image", errors)
            try:
                safe_asset(root, image.get("path"))
            except ValueError as exc:
                errors.append(f"{label}.image：{exc}")
    if set(order) != set(by_id) or len(order) != len(pages):
        errors.append("page_order 与 pages 必须一一对应且无缺页")
    if kinds.count("cover") != 1 or kinds.count("backcover") != 1:
        errors.append("必须各有一页封面和封底")
    if kinds.count("story") != body_count:
        errors.append("正文页数量与 brief.body_page_count 不符")
    if order and order[0] in by_id and by_id[order[0]].get("kind") != "cover":
        errors.append("封面必须是 page_order 的第一页")
    if order and order[-1] in by_id and by_id[order[-1]].get("kind") != "backcover":
        errors.append("封底必须是 page_order 的最后一页")
    positions = {identifier: index for index, identifier in enumerate(order)}
    states = {identifier: prop.get("initial_state") for identifier, prop in entities["props"].items()}
    for identifier in order:
        page = by_id.get(identifier)
        if page is None:
            continue
        dependencies = page.get("depends_on")
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if not isinstance(dependency, str) or dependency not in positions:
                    errors.append(f"页面 {identifier} 引用了不存在的前置页面")
                elif positions[dependency] >= positions[identifier]:
                    errors.append(f"页面 {identifier} 的依赖必须在此前，不能自依赖、向后引用或形成环")
        continuity = page.get("continuity")
        if not isinstance(continuity, dict):
            continue
        for prop_id, transition in continuity.items():
            label = f"页面 {identifier} 的道具 {prop_id}"
            transition = _mapping(transition, label, errors)
            if not isinstance(prop_id, str) or prop_id not in states:
                errors.append(f"{label} 不存在")
                continue
            if not all(_nonempty(transition.get(key)) for key in ("before", "after")):
                errors.append(f"{label} 的 before 与 after 必须是非空状态字符串")
                continue
            if transition["before"] != states[prop_id]:
                errors.append(f"{label} 的 before 与此前页面或初始状态不一致")
            states[prop_id] = transition["after"]


def validate_book(book, root, stage="draft"):
    """返回中文错误列表；先校验结构，再进入依赖签名和验收检查。"""
    errors = []
    if stage not in STAGES:
        return ["校验阶段必须是 draft、ready 或 complete"]
    if not isinstance(book, dict):
        return ["项目顶层必须是 JSON 对象"]
    if type(book.get("schema_version")) is not int or book["schema_version"] != SCHEMA_VERSION:
        errors.append("schema_version 必须是整数 1")
    for field in ("book_id", "title"):
        if not _nonempty(book.get(field)):
            errors.append(f"{field} 必须是非空字符串")
    brief = _mapping(book.get("brief"), "brief", errors)
    if not _nonempty(brief.get("age_range")):
        errors.append("brief.age_range 必须是非空字符串")
    languages = _string_list(brief.get("languages"), "brief.languages", errors)
    if "zh" not in languages or any(language not in LANGUAGES for language in languages):
        errors.append("语言必须包含 zh，其他语言只支持 pinyin、en")
    body_count = brief.get("body_page_count")
    if type(body_count) is not int or body_count <= 0:
        errors.append("brief.body_page_count 必须是正整数")
    _validate_format(book, errors)
    approvals = book.get("approvals", {})
    approvals = _mapping(approvals, "approvals", errors)
    for key in ("brief", "story", "visual"):
        if (stage != "draft" or key in approvals) and not _nonempty(approvals.get(key)):
            errors.append(f"approvals.{key} 必须记录实际已获得的用户确认或授权依据")
    visual = _mapping(book.get("visual"), "visual", errors)
    if not _nonempty(visual.get("description")):
        errors.append("visual.description 必须是非空字符串")
    _string_list(visual.get("palette"), "visual.palette", errors)
    _reference(visual, root, "visual", errors)
    entities = _entities(book, root, errors)
    _validate_pages(book, root, stage, languages, entities, body_count, errors)
    if stage == "complete" and not errors:
        try:
            for status in _page_statuses(book, root):
                if status["status"] != "approved":
                    descriptions = {"missing": "图片缺失或无效", "stale": "插画输入签名已过期",
                                    "review": "验收缺失、未通过或与当前图片不匹配"}
                    errors.append(f"页面 {status['id']}：{descriptions[status['status']]}")
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def _with_reference_digest(value, root):
    result = {key: item for key, item in value.items() if key != "reference"}
    if "reference" in value:
        result["reference_sha256"] = file_digest(safe_asset(root, value["reference"]))
    return result


def _adopted_image_digest(page, root):
    # 上游使用相同提示词重绘也可能改变构图，依赖页必须绑定实际已采用的图像。
    image = page.get("image")
    if not isinstance(image, dict):
        return None
    path = safe_asset(root, image.get("path"))
    try:
        return file_digest(path) if path.is_file() else None
    except OSError as exc:
        raise ValueError("前置页面的图片无法读取") from exc


def _fingerprints(book, root):
    # 依赖只允许指向前页，因此顺序计算既避免递归溢出，也自然传播失效。
    pages = {page["id"]: page for page in book["pages"]}
    visual = _with_reference_digest(book["visual"], root)
    entities = {
        section: {identifier: _with_reference_digest(item, root) for identifier, item in book[section].items()}
        for section in ("characters", "scenes", "props")
    }
    fingerprints = {}
    for identifier in book["page_order"]:
        page = pages[identifier]
        payload = {
            "signature_version": SCHEMA_VERSION, "page_id": identifier, "kind": page["kind"],
            "action": page["action"], "visual_evidence": page["visual_evidence"],
            "scene": entities["scenes"][page["scene_id"]],
            "characters": {key: entities["characters"][key] for key in page["character_ids"]},
            "props": {key: entities["props"][key] for key in page["continuity"]},
            "continuity": page["continuity"], "prompt": page.get("prompt", ""), "visual": visual,
            "composition": {key: book["format"][key] for key in COMPOSITION_FIELDS},
            "dependencies": {
                key: {"input_fingerprint": fingerprints[key],
                      "image_sha256": _adopted_image_digest(pages[key], root)}
                for key in page["depends_on"]
            },
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("插画输入必须是可编码的有限 JSON 数据") from exc
        fingerprints[identifier] = hashlib.sha256(encoded).hexdigest()
    return fingerprints


def page_fingerprint(book, page_id, root):
    """纯文字和排版字号不改变插画签名；视觉内容须在动作等字段表达。"""
    errors = validate_book(book, root)
    if errors:
        raise ValueError("项目数据无法计算签名：" + "；".join(errors))
    if not isinstance(page_id, str) or page_id not in book["page_order"]:
        raise ValueError("待计算签名的页面不存在")
    return _fingerprints(book, root)[page_id]


def _qa_matches(qa, fingerprint, digest):
    if not isinstance(qa, dict):
        return False
    checks = qa.get("checks")
    return (qa.get("input_fingerprint") == fingerprint and qa.get("image_sha256") == digest
            and _nonempty(qa.get("reviewer")) and _nonempty(qa.get("notes"))
            and isinstance(checks, dict) and all(checks.get(key) is True for key in QA_CHECKS))


def _page_statuses(book, root):
    fingerprints = _fingerprints(book, root)
    pages = {page["id"]: page for page in book["pages"]}
    result = []
    for identifier in book["page_order"]:
        page, fingerprint = pages[identifier], fingerprints[identifier]
        image = page.get("image")
        status = "missing"
        if isinstance(image, dict):
            try:
                path = _image_path(root, image.get("path"))
            except ValueError:
                path = None
            if path is not None:
                if image.get("input_fingerprint") != fingerprint:
                    status = "stale"
                elif _qa_matches(page.get("qa"), fingerprint, file_digest(path)):
                    status = "approved"
                else:
                    status = "review"
        result.append({"id": identifier, "status": status, "fingerprint": fingerprint})
    return result


def page_statuses(book, root):
    """只读生成续作清单，不覆盖用户已有图片及验收记录。"""
    errors = validate_book(book, root)
    if errors:
        raise ValueError("项目结构错误，无法恢复：" + "；".join(errors))
    return _page_statuses(book, root)
