"""在完整验收通过后，将绘本导出到全新目录，失败时不留下半本成书。"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from book_model import validate_book
from render_html import write_html


def render_book(book_path, output_dir, pdf=False, font=None):
    """先验证、后排版、最后发布；已有目录与文件一律不覆盖。"""
    source = Path(book_path).resolve()
    output = Path(output_dir).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"输出目标已存在，必须选择全新目录：{output}")
    try:
        book = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取项目数据：{source}；{error}") from error
    if not isinstance(book, dict):
        raise ValueError("项目数据的最外层必须是对象。")
    errors = validate_book(book, source.parent, stage="complete")
    if errors:
        raise ValueError("项目尚未通过完整验收：\n" + "\n".join(errors))
    output.parent.mkdir(parents=True, exist_ok=True)
    published = False
    with tempfile.TemporaryDirectory(prefix=".picturebook-", dir=output.parent) as staging:
        temporary = Path(staging)
        write_html(book, source.parent, temporary / "book.html")
        filenames = ["book.html"]
        if pdf:
            from render_pdf import write_pdf

            write_pdf(book, source.parent, temporary / "book.pdf", font)
            filenames.append("book.pdf")
        errors = validate_book(book, source.parent, stage="complete")
        if errors:
            raise ValueError("排版过程中素材发生变化，必须重新验收：\n" + "\n".join(errors))
        try:
            # 排版全部成功后独占创建目录，避免并发调用覆盖先前的成果。
            try:
                output.mkdir()
            except FileExistsError as error:
                raise ValueError(f"输出目标已存在，必须选择全新目录：{output}") from error
            published = True
            for filename in filenames:
                os.replace(temporary / filename, output / filename)
        except Exception:
            # 仅删除本次独占创建的输出目录；不触碰源项目或已有成果。
            if published:
                shutil.rmtree(output)
            raise
    return {Path(filename).suffix.lstrip("."): output / filename for filename in filenames}


def main(argv=None):
    """命令行只输出成果路径或明确错误，便于编排层判断成功与失败。"""
    parser = argparse.ArgumentParser(description="导出已完成验收的完整儿童绘本。")
    parser.add_argument("book", type=Path, help="项目 book.json 路径")
    parser.add_argument("--output", required=True, type=Path, help="尚不存在的输出目录")
    parser.add_argument("--pdf", action="store_true", help="同时导出阅读版 PDF")
    parser.add_argument("--font", type=Path, help="覆盖全书字符的真实中文 TTF/TTC 字体")
    args = parser.parse_args(argv)
    try:
        paths = render_book(args.book, args.output, pdf=args.pdf, font=args.font)
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({key: str(path) for key, path in paths.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # 明确使用 UTF-8，Windows 管道同样能读回中文诊断信息。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
