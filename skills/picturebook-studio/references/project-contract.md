# 项目数据与工具约定

这是完整绘本制作时使用的工作数据约定。项目以 UTF-8 `book.json` 保存，所有插画及参考图路径均相对该文件所在目录；拒绝绝对路径、目录外路径、符号链接逃逸和远程 URL。字体由排版命令显式选择，不属于素材路径。

## 数据结构

- `schema_version`：整数 `1`。
- `book_id`、`title`：非空字符串。
- `brief`：`age_range` 为非空字符串；`languages` 为包含 `zh` 的不重复列表，其他可选 `pinyin`、`en`；`body_page_count` 为正整数。
- `format`：`width_mm`、`height_mm`、`margin_mm`、`font_size_pt`、`line_spacing`、`text_ratio` 均为有限数值。尺寸、字号为正；边距非负且小于短边的一半；行距至少为 1；文字区比例大于 0 且小于 1。
- `approvals`：`brief`、`story`、`visual` 对应实际已获得的用户确认或授权依据，值为非空字符串。缺失时允许保存草案，但不能进入正式制作与最终交付。已获得明确授权时记录该授权，不重复索要批准。
- `visual`：`description` 非空；`palette` 为非空颜色字符串列表；可选 `reference` 为风格参考图路径。
- `characters`：角色 ID 到对象的映射；每个对象包含非空 `description`，可选 `reference`。描述固定身份、比例、服装及故事允许的变化。
- `scenes`：场景 ID 到对象的映射；每个对象包含非空 `description`，可选 `reference`。
- `props`：道具 ID 到对象的映射；每个对象包含非空 `description`、`initial_state`，可选 `reference`。状态应记录地点、持有人、内容物及完好情况等对故事有意义的信息。
- `page_order`：用户确定的全部页面 ID，顺序即导出顺序；包含封面、正文及本书需要的附加页。
- `pages`：页面对象列表；ID 与 `page_order` 必须一一对应。正文页数量必须等于 `body_page_count`，必须存在封面和封底。

每个页面必须包含：

- `id`：唯一非空字符串；`kind`：`cover`、`frontmatter`、`story`、`backmatter`、`backcover` 之一。
- `scene_id`：已有场景 ID；`character_ids`：已有角色 ID 的不重复列表，可以为空。
- `depends_on`：本页视觉内容依赖的先前页面 ID，不重复且不得引用自己或后续页。
- `purpose`、`action`：非空字符串；`visual_evidence`：非空字符串列表，指出图文必须对应的可见事物。
- `text`：`zh` 为字符串；选择英文时 `en` 为字符串。选择拼音时 `pinyin` 为逐字对象列表，每项包含 `char` 与 `reading`；`char` 按顺序拼接必须与 `zh` 完全相同，标点可用空 `reading`。不得自动把多音字按孤立字典读音视为已校对。
- `continuity`：道具 ID 到 `{before, after}` 的映射，均为非空状态字符串。未参与本页的道具可省略。每个 `before` 必须等于该道具在此前页面留下的状态，首次使用必须等于 `initial_state`。
- `prompt`：正式制作前必须存在的非空完整生图提示词。

每页可选 `presentation` 对象，仅控制独立文字区与完整插图区的排列、颜色和字号：

- `layout`：`image-top`（插图在上）、`text-top`（文字在上）、`title-top`（上方标题区垂直居中）、`positioned`（显式图文矩形）、`image-only`（实际无字页）。封面默认 `title-top`，其他页面默认 `image-top`。
- `text_align`：`left` 或 `center`。封面、封底默认居中，正文及其他附加页默认左对齐。
- `font_size_pt`：大于 0、排版工具可表示的有限数值，不能是布尔值。默认使用 `format.font_size_pt`；封面默认使用其 1.5 倍以建立标题层级。
- `text_color`、`paper_color`：只能是 `#` 开头的六位十六进制颜色，如 `#315A6F`、`#FFFAEF`。默认字色 `#202020`，纸色 `#FFFFFF`。

- `image_box`、`text_box`：仅 `positioned` 使用，两个都必须提供，为安全内容区内的 `[左, 上, 宽, 高]` 归一化矩形；原点在左上，比例介于0与1，宽高为正且不得越界，不能是布尔值或非有限数。
- `allow_overlap`：仅 `positioned` 使用的布尔值，默认 `false`。图文矩形有面积重叠时须明确为 `true`；只应用于图画计划已有明确文字留白的页面，实际可读性须看图验收。边界相接不算重叠。

对象可只包含需要覆盖的字段；未提供的字段沿用默认值。未知字段、未知布局和非法颜色一律报告错误，不静默忽略。旧项目无需增加此对象即可导出。三个旧布局仍保留相同尺寸的插图区和文字区；`positioned` 改变完整插画的位置与大小但仍等比保留，不提供裁切或拉伸。`image-only` 使用全部安全内容区，必须让 `text.zh` 及任何其他正文语言真正为空，不得通过布局隐藏文字。

生成后加入：

- `image`：`path` 为真实 PNG/JPEG 文件；`input_fingerprint` 为生成时的页面输入签名。
- `qa`：`input_fingerprint` 与当前签名相同；`image_sha256` 为实际图片摘要；`reviewer`、`notes` 为非空字符串；`checks` 包含 `identity`、`continuity`、`text_match`、`anatomy`、`style`、`composition`，全部通过时均为 JSON 布尔值 `true`。无人像时在备注中说明肢体项不适用的理由，仍记录审查结果。机器校验仅核验记录和版本绑定，不能代替看图。

## 页面签名与恢复

`scripts/book_model.py` 暴露以下接口，供检查与排版共同使用：

- `validate_book(book, root, stage="draft") -> list[str]`：返回中文错误列表。阶段为 `draft`、`ready`、`complete`。草案检查结构、范围、引用、页数、道具连续性；正式阶段额外检查确认记录和提示词；完成阶段额外检查真实图片、输入签名和验收绑定。
- `page_fingerprint(book, page_id, root) -> str`：计算 SHA-256。纳入本页动作、视觉证据、场景、人物、道具状态、提示词、全书视觉规范、构图相关版式及前置页面签名；参考图文件内容和前置页面已采用图片的内容摘要也参与计算，前置页尚无图时记录为空。纯正文、拼音、英文、书名字句、字体字号、`presentation` 与本页自身生成结果不进入插画签名，因此不改变画面含义的文字或独立排版修订可复用插画。`format` 的尺寸、边距与文字区比例仍参与签名，因为它们改变插图区大小。正文改动若改变画面含义，必须同步修改动作、视觉证据和提示词。
- `file_digest(path) -> str`：文件 SHA-256。
- `safe_asset(root, relative_path) -> pathlib.Path`：解析并限制素材在项目内；非法路径抛出 `ValueError`。
- `page_statuses(book, root) -> list[dict]`：按 `page_order` 返回 `id`、`status`、`fingerprint`。状态为 `missing`、`stale`、`review`、`approved`。真实图片缺失为 missing；输入不匹配为 stale；验收缺失、失败或与图片摘要不符为 review。

校验 CLI：`python scripts/bookcheck.py book.json --stage ready`；查看续作清单：`python scripts/bookcheck.py book.json --stage draft --status`。只读输出 JSON，不自动改写项目数据。

可加 `--font 实际字体路径`，在结构通过后调用 `scripts/render_pdf.py` 的 `preflight_text(book, font_path)`，按真实字形、字号与全部文字矩形一次返回各页溢出。此时不要求插画已经生成，也不创建或修改项目与导出文件。报告中的 `text_checked: true` 表示已完成字体测量；缺字体、缺字或结构错误仍明确失败。未提供该参数时保持标准库检查路线。

检查器对 PNG 检查块、校验和与压缩像素结构，对 JPEG 检查段、尺寸、扫描及结束标记；JPEG 完整解码在 PDF 路线由 Pillow 执行。无论哪种格式，都需实际打开图片进行视觉验收，不能把文件检查成功等同于画面正确。

## 排版约定

`scripts/render_book.py book.json --output 目标目录` 在 `complete` 校验通过后导出离线自包含 `book.html`。它保留完整插图比例，正文放在预留的独立文字区；排版元素不包含外部网络资源或可执行用户文本。

追加 `--pdf --font 字体路径` 导出阅读版 `book.pdf`。PDF 使用 ReportLab，图片检查使用 Pillow；HTML 路线使用 Python 标准库。字体必须实际覆盖正文字符；文字容纳不下时停止并指出页面，不能缩成不可读小字或静默裁切。拼音通过已校对逐字数据排版。

排版到全新目录，已有产物不覆盖。输出阅读版 PDF 不等于通过印刷厂的生产校验；印刷出血、装订和色彩规格需按用户提供的要求单独处理。导出后仍必须渲染并检查每页，记录文本溢出、图像拉伸、错页、字体与安全边距问题。

HTML 与 PDF 共用 `scripts/presentation.py` 的有效布局解析与页面矩形。HTML 在窄屏保持完整图片和可读文字，允许页面自然增高；`positioned` 在640像素及以下展开为图后正文，不保留会挤压文字的叠放位置，打印恢复项目物理尺寸和设计位置。PDF 固定页尺寸，按本页实际文字矩形、字号和全部语言测量，溢出时停止并指出页面。技术通过只能证明结构、字体与边界，不证明翻页节奏、封面美感或图文叙事达到高质量；这些必须依据实际页面另行审校。
