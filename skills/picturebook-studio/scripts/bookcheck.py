"""只读检查绘本项目，以 JSON 报告可恢复状态和错误。"""

import argparse
import json
import sys
from pathlib import Path

from book_model import STAGES, page_statuses, validate_book


def _reject_constant(value):
    # JSON 标准不允许 NaN 和 Infinity，避免解释器宽松解析掩盖输入错误。
    raise ValueError(f"JSON 不允许非有限常量：{value}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读校验完整绘本项目")
    parser.add_argument("project", type=Path, help="UTF-8 book.json 路径")
    parser.add_argument("--stage", choices=STAGES, default="draft", help="草案、正式制作或最终交付检查")
    parser.add_argument("--status", action="store_true", help="附加逐页续作状态")
    parser.add_argument("--font", type=Path, help="可选真实字体，用于生图前检查全部文字区域")
    arguments = parser.parse_args(argv)
    report = {"ok": False, "stage": arguments.stage, "errors": []}
    try:
        book = json.loads(arguments.project.read_text(encoding="utf-8-sig"), parse_constant=_reject_constant)
        root = arguments.project.resolve().parent
        report["errors"] = validate_book(book, root, arguments.stage)
        if arguments.font and not report["errors"]:
            # 只有用户选择字体预检时加载 PDF 依赖，纯结构检查保持标准库路线。
            from render_pdf import preflight_text

            report["errors"].extend(preflight_text(book, arguments.font))
            report["text_checked"] = True
        if arguments.status and not validate_book(book, root, "draft"):
            report["pages"] = page_statuses(book, root)
        report["ok"] = not report["errors"]
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        report["errors"].append(f"无法检查项目：{exc}")
    # 转义非 ASCII 字符保证 Windows 重定向时不依赖终端编码，解析后仍为中文。
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
