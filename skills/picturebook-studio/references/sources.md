# 方法来源与整合边界

核验日期：2026-10-06。下列项目用于研究创作方法；本包独立编写技能说明、数据约定、校验及排版代码，没有复制上游图片、图库、源码或整份技能文件，不需要联网调用上游才能运行。

| 来源 | 采用的方法 | 本技能的适配 |
| --- | --- | --- |
| [kangqiang1996-bot/picture-book-generator](https://github.com/kangqiang1996-bot/picture-book-generator) | 故事到分页、角色场景道具连续性、逐页图文对应、确定性文字排版 | 完整交付状态、版本签名、全部页面验收；避免把样页当成成书 |
| [yang0/handraw-style](https://github.com/yang0/handraw-style) | 显式描述画风、配色与参考素材用途，角色及场景资料复用 | 一本书一套确认设定；分开风格参考与身份参考；不依赖原图库和模型编号表 |
| [jiayuewangjavy/watercolor-diary](https://github.com/jiayuewangjavy/watercolor-diary) | 纸面、透明色层、颜料边缘、手工轮廓与留白表现 | 用于故事画面视觉规范；不沿用去背景、静物拆解和英文签语的原生任务 |
| [kart-io/picture-skills](https://github.com/kart-io/picture-skills) | 适龄语言、中英拼音层和可辨识角色特征 | 自行实现语言数据约定；不复用缺失引用路径或不完整的原参数引擎 |

上游来源保留原作者署名：yang0（手绘风格项目）、JavyWang / jiayuewangjavy（水彩项目）、kangqiang1996-bot（绘本项目）、kart-io（绘本魔法师）。

许可核验记录：绘本生成器和水彩项目有 MIT LICENSE；handraw-style 的 LICENSE 在 MIT 文本上附加了展示作者与原仓库链接的要求；picture-skills 的 README 标注 MIT，但核验时未找到独立 LICENSE。此记录不替代各项目的实际许可证，也不为复制其资产提供额外授权。

若后续明确要求引入上游代码、图库或模板，应重新核验该版本和具体资产的许可，保留对应许可与署名，并将依赖变化纳入回归检查。当前版本保持独立实现。
