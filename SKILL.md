---
name: oil-showcase
description: 用克制的画廊编辑风将任意真实产品截图（网站、深色桌面工作台、原生工具、移动 App）、分层素材与界面模块编排为高分辨率展示卡片与结构拆解图（支持暖纸/暗棚/冷灰三套主题、8 种通用版式、16:9/4:3/3:4/9:16/1:1 画幅，默认 2x 导出 PNG 与离线总览页）。当用户需要制作产品发布配图、社媒展示卡、竖屏封面图、Bento 模块总览、横屏窗口标注解剖、前后对比卡、三四步流程卡或多屏画廊墙时使用。不用于多页演示幻灯片（交给 oil-ppt）、长图文写作或发布（交给 oil-post）、漫画墨线概念解释图（交给 oil-visual），也不用于直接设计或编写线上产品界面（交给 oil-ui-pro）。
license: MIT
compatibility: 核心脚本仅使用 Python 3.10+ 标准库，调用本机 Chrome、Chromium 或 Microsoft Edge 无头模式渲染，无需安装外部依赖，内置 OFL 字体可离线工作；已在 macOS 实测通过，Windows 与 Linux 已做跨平台路径兼容，待实机验证。
metadata:
  version: "0.2.0"
---

# oil-showcase

把真实产品画面编排成克制、耐看的高分辨率展示卡。画板提供三套中性主题（`warm-paper`、`dark-studio`、`cool-gallery`），只把产品主题色克制地注入序号、虚线连线、对比箭头和极小标记点，适配网站、深色桌面工作台、原生工具与移动 App。

下文 `<python>` 指 Python 3.10+ 解释器：macOS 与 Linux 通常为 `python3`，Windows 通常为 `py -3`。

## 第一步：静默检查环境

先运行只读检查，确认 Python 版本、本机浏览器和内置字体可用：

```text
<python> scripts/render_cards.py doctor
```

如果未检测到浏览器，停止并告知用户安装 Chrome/Chromium/Edge，或通过环境变量 `CHROME_PATH` 指定可执行文件路径。

## 第二步：收集真实产品素材

展示卡必须使用真实产品截图、真实页面裁切或透明前景层，禁止伪造假界面、装饰贴纸或空洞营销徽章。

- **禁止空洞凑数演示**：每张卡片都必须承载高信息密度的真实产品画面或肉眼立见的价值对比；禁止拿「70% 空白背景 + 几行简单代码」或单纯换浅色/深色皮肤的平淡截图做演示图。
- **真放大原则**：连线放大框（`window-callout`）和局部对比图（`before-after`）必须放 **2×–4× 局部放大裁切**，让读者在缩略图或 900px 文档宽度下不点开原图也能直接读清界面文字；严禁把整张窗口缩小塞进放大卡或窄对比栏。
- 用户已有现成图片时，核对文件路径、长宽比与清晰度，直接选用。
- 需要从本地 HTML 或 `localhost` 网页截取无浏览器边框的高清画面时，调用内置抓图工具：

```text
<python> scripts/render_cards.py capture <url-or-html> --out <png-path> --viewport 1440x900 --scale 2
```

截取局部模块加 `--selector "<css>"`，截取整页长图加 `--full-page`。

## 第三步：选定主题、版式并编写规格

读取 [设计系统与规格参考](references/design-system.md)，按叙事目标确定以下四项并写入 UTF-8 JSON（如 `cards.spec.json`）：

| 版式 `layout` | 适用叙事 |
| --- | --- |
| `layered-hero` | 2–3 层素材叠加 + 大主图 |
| `layered-bento` | 左侧分层叠卡 + 右侧 2 列模块 Bento |
| `section-assembly` | 中栏切片用虚线指向右侧竖向整页 |
| `module-bento` | 顶部标题 + 3×2 / 2×2 / 2+3 业务模块网格 |
| `gallery-wall` | 倾斜整页展陈（`tilted-pages`）或画廊拼接墙（`collage`） |
| `window-callout` | 左侧大主窗口 + 右侧 2–4 张放大细节卡与水平标线（可选上下 `stacked`） |
| `before-after` | 开启前/后、改版前/后或深浅双态并排/上下对比 |
| `workflow-steps` | 3–4 步端到端产品操作流程串联 |

- 主题 `theme` 可选 `warm-paper`（默认衬线）、`dark-studio`（深色哑光无衬线，适配深色专业工具）或 `cool-gallery`（冷灰无衬线，适配原生与效率工具），均可用 `displayStyle` 单独覆盖标题字体风格。
- 选定画幅比例（`16:9`、`4:3`、`3:4`、`9:16`、`1:1` 或自定义 `WxH`）和产品主题色 `accent`，深色或异形截图善用 `fit`、`position`、`bg`。
- **单一文字锚点，不分散堆字**：每张展板只在左上角保留「品牌标 + 一句短主标题」（必要时紧跟 1 行短说明），不在右上角另起装饰标签或分散多处小字；次级说明字号不低于可读下限（`15–21px`），标题与画面已说清时直接省略 `subtitle`、`caption` 和 `footer`。
- 若需求明显超出 8 种内置版式，可遵循参考文档令牌编写自定义 HTML，再用 `shoot` 导出。

## 第四步：渲染与自动校验

用规格文件批量生成高分辨率 PNG、独立 HTML 和离线预览页：

```text
<python> scripts/render_cards.py render <spec.json> --out-dir <out-dir> --scale 2
```

- 局部微调单卡加 `--only <card-id>`，脚本会保留同目录已有其他卡片的完整 `index.html`。
- 自定义 HTML 单卡导出使用 `<python> scripts/render_cards.py shoot <card.html> --out <card.png> --ratio 16:9 --scale 2`。

脚本会自动检查图片路径、字体就绪、图片解码和文本溢出；出现报错或 `warnings` 时，优先修短文案或调整槽位参数后重出。

## 第五步：视觉抽查与交付

如果当前宿主支持读图，逐张检查 `<out-dir>/png/*.png`：确认标题留白充足、层次清楚、截图核心内容未被意外裁切、标线与锚点清晰可读。有问题就调整规格并用 `--only` 重渲染。

交付时告知 PNG 路径、离线总览页 `<out-dir>/index.html` 路径以及是否存在未解决的警告，不覆盖用户未授权的原图。

## 交付后的下一步

主流程完成且用户未指定后续动作时，按当前状态从下表选最多 3 条推荐给用户：

| 候选动作 | 得到的结果 | 额外代价 |
| --- | --- | --- |
| 切换画板主题（`dark-studio` / `cool-gallery` / `warm-paper`） | 同套素材与版式生成另一氛围版本对比 | 新增导出目录，重渲染几秒 |
| 适配竖版 `3:4` / `9:16` 或方图 `1:1` | 同套素材自动重排为移动端竖屏、短视频封面或方图卡片 | 新增文件，重新渲染几秒 |
| 精修单张构图或标线坐标 | 用 `--only` 局部微调单卡并保持总览页完整 | 只重渲染单张，耗时极短 |
