# 设计系统与规格参考

本文件为 [oil-showcase](../SKILL.md) 提供三套中性画板主题令牌、字体层级、文案克制原则、8 种通用版式选型与 `spec.json` 完整字段说明。

## 目录

- [三套中性画板主题令牌](#三套中性画板主题令牌)
- [产品主题色注入规则](#产品主题色注入规则)
- [标题风格与字体层级](#标题风格与字体层级)
- [文案与画面克制纪律](#文案与画面克制纪律)
- [八种版式原型与比例适配](#八种版式原型与比例适配)
- [spec.json 规格参考](#specjson-规格参考)

## 三套中性画板主题令牌

画板本身始终保持克制、低饱和度，把视觉重心让给真实产品画面。可在 `spec.json` 顶层或单卡上通过 `theme` 指定（默认 `"warm-paper"`）：

| 令牌 | `warm-paper`（暖纸编辑） | `dark-studio`（暗棚专业） | `cool-gallery`（冷灰建筑） |
| --- | --- | --- | --- |
| 默认适配产品 | 品牌官网、内容与消费产品、浅色应用 | 深色音视频剪辑器、IDE、专业桌面工作台 | 原生效率工具、仪表盘、现代 SaaS |
| `--paper-base` | `#f5f1e8` | `#0c0e11` | `#eef1f5` |
| `--paper-bg` | `#faf7f0 -> #f4efe4 -> #eae3d5` + 左上日光漫射 | `#11151b -> #0c0e11 -> #090c10` + 左上冷调主光 | `#f5f7fa -> #eef1f5 -> #e4e9f1` + 左上天光漫射 |
| 纯代码颗粒质感 | SVG `feTurbulence` `0.88`（暖棉纸 `multiply 0.036`） | SVG `feTurbulence` `0.82`（阳极暗棚 `overlay 0.038`） | SVG `feTurbulence` `0.86`（细建筑哑光 `multiply 0.032`） |
| `--ink` | `#1b1c19` | `#f5f7fa` | `#0b1220` |
| `--muted` | `#4a4d46` | `#98a2b2` | `#526075` |
| `--frame-bg` | `#ffffff` | `#17191e`（消除深色截图白边） | `#ffffff` |
| `--surface-bg` | `#fbfaf7` | `#15181f` | `#f8fafc` |
| `--hairline` | `rgba(27, 28, 25, 0.10)` | `rgba(255, 255, 255, 0.12)` | `rgba(15, 23, 42, 0.11)` |
| 方向性投影 + 光学高光边 | 左上主光斜向投影 + `inset 0 1.2px 0` 高光沿 | 深邃方向投影 + `rgba(255,255,255,0.28)` 镜面顶沿 | 建筑感方向投影 + `rgba(255,255,255,0.46)` 锐利顶沿 |
| 默认 `displayStyle` | `"serif"` | `"sans"` | `"sans"` |

卡片圆角规则：主画幅窗口与 Bento 卡片使用 `14px`–`18px`；紧凑局部切片和分层底板使用 `10px`–`14px`。所有浮起画框统一自带纯代码左上主光斜向漫射、柔和主体反光与 1px 顶部光学高光沿，全程零外网请求、零 AI 生图。

## 产品主题色注入规则

画板底色不随产品染色；单张卡片通过 `accent` 传入一个十六进制颜色（例如 `#2563eb`、`#2fbf71`、`#a62639`；留空时默认取当前主题主墨色），且**只允许**出现在极克制的结构标记上：

1. 两位数字序号（`01`、`02`、`03`、`04`）、编号角标与竖屏定位针；
2. `section-assembly` 与 `window-callout` 的 SVG 虚线连线、端点圆点及带对比光环的锚点；
3. 主画面眉标左侧小圆点（`• LIVE DEMO · ...`）、`before-after` 的过渡箭头与 `after` 细边强调、`workflow-steps` 的步骤箭头。

禁止用 `accent` 铺满整块画布背景或大面积色块。

## 标题风格与字体层级

内置 OFL 字体位于 `assets/fonts/`，渲染脚本会自动内联为离线样式。可通过顶层或单卡 `displayStyle`（`"serif"` 或 `"sans"`）覆盖主题默认标题风格：

- **衬线展示体（`displayStyle: "serif"`）**：`'Playfair Display', 'Songti SC', 'Noto Serif CJK SC', 'STSong', Georgia, serif`。适合杂志编辑感与品牌叙事；英文 `400` 字重、字距 `-0.025em`，中文 `500` 字重、行高 `1.22`。
- **现代无衬线展示体（`displayStyle: "sans"`）**：`'Inter', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif`。`600` 字重、紧致字距 `-0.032em`（中文 `-0.018em`），在 `dark-studio` 与 `cool-gallery` 下干脆利落。
- **正文无衬线与等宽体**：正文固定使用 `Inter` + 系统中文无衬线；序号、眉标、对比标签、步骤标签与页脚统一使用 `JetBrains Mono`。

## 文案与画面克制纪律

- **语言跟随用户（Language follows conversation）**：所有卡片标题、副标题、步骤与放大标注、对比标签（`before`/`after`）和图例默认跟随当前用户对话语言（中文对话输出中文，英文对话输出英文），仅在用户明确要求时切换其他语言。`before-after` 省略 `label` 时，脚本会按卡片语言自动填入 `对比前`/`对比后`（CJK）或 `BEFORE`/`AFTER`（非 CJK），也可通过 `lang: "zh" | "en"` 显式指定。
- **单一文字锚点，不分散堆字**：每张展板只在左上角保留「品牌标 + 一句短主标题」（必要时紧跟 1 行短说明），不在右上角另起装饰标签或分散多处小字；顶栏版式（`window-callout`、`before-after`、`workflow-steps`、`module-bento`）统一单列左对齐，不渲染右上角漂浮眉标。
- **禁止空洞凑数演示**：每张卡片都必须承载高信息密度的真实产品画面或肉眼立见的价值对比；禁止拿「70% 空白背景 + 几行简单代码」或单纯换浅色/深色皮肤的平淡截图做演示图。
- **主标题**：中文不超过 12 字，英文不超过 6 词；在左侧栏中可自然换行成 2 行（支持 `\n` 显式断行）。
- **副标题**：最多 1–2 短行（`19–21px`），紧跟主标题下方左对齐；标题与画面已说清时直接省略，容器自动收起不留空隙。
- **字号不低于可读下限，冗余说明宁缺毋滥**：在 `1600px` 画布上所有可见文字硬性不低于 `14px`，正文说明、图例、卡片标题与副注渲染在 `15–21px`，确保在 `900px` README 或手机信息流缩略宽度下依然一眼可读；只要标题和截图本身已经说明重点，就直接省略 `caption` 和 `footer`，避免凑数小字。
- **画面占比**：至少 70%–80% 的画布面积留给真实产品画面与克制呼吸留白。
- **视觉张力与清晰度原则（Anti-Flatness & Razor-Sharp UI）**：
  1. **纯代码光影与卡片后遮挡雕塑字**：弃用一切带边框的圆角胶囊标签（如 `[BEFORE]`、编号小方框），改用无边框大号雕塑字（`BEFORE` / `AFTER`、中文自动 `对比前` / `对比后`、`01` / `02` / `03`）置于 `z-index: 1` 卡片背后，被 `z-index: 3` 的高光顶沿自然遮挡底部 18%–25%，在不牺牲留白的前提下制造真实景深。
  2. **默认横平竖直，克制微倾边界**：
     - **默认端正 `0deg`（零旋转重采样，保持界面文字最高清）**：`before-after`、`window-callout`、`workflow-steps`（默认 `row`）、`module-bento`、`section-assembly`、`gallery-wall` 默认均保持横平竖直与规整网格秩序；如需微妙透视角度，可在卡片上显式声明 `"tilt": true`。
     - **默认分层微倾（仅限真实分层与舞台叙事）**：`layered-hero`（`hero.tilt` 默认 `true`）与 `workflow-steps` `style: "staged"`（`tilt` 默认 `true`），两者均可通过 `"tilt": false` 一键切回完全端正。
     - **多卡网格严禁散乱倾倒**：4 张以上多图网格（`gallery-wall`、`module-bento`、`section-assembly`、`workflow-steps` `row`）始终保持规整对齐与统一呼吸节奏。
  3. **尺度与光影反差**：`before-after` 须有 >50% 肉眼可见变化；横版 `side-by-side` 在端正 `0deg` 下通过左卡略退缩 + 右卡更大更亮 + 主题反光晕 + 极简无框光束箭头建立主次；横向时间轴/波形优先用 `orientation: "stacked"` 配合 `after.widthPercent` + 发光竖向光学裁切线 + 右侧无框大号 `deltaBadge`。
  4. **同排媒介语境统一**：`workflow-steps` 与 `module-bento` 同排卡片必须保持统一背景/满出血或统一留白舞台（`fit: "contain"` 会自动加 18px 均匀舞台留白与圆角投影），禁止把裸露深色浮卡与满出血彩色壁纸桌面硬拼在同一排。
- **真放大原则（Legibility First）**：连线放大框（`window-callout`）和局部对比图（`before-after`）的本质是把关键细节放大 **2×–4×**，确保读者在社媒缩略图或 900px 文档列宽下无需点开原图就能读清文字与控件；严禁把整张桌面窗口直接缩成微缩图塞进局部放大框或窄对比栏。
- **明确禁止**：装饰贴纸、爆炸角标、空洞营销徽章（如 `New!`、`AI Powered`）、用代码硬画的假产品截图。

## 八种版式原型与比例适配

### 八种版式如何选择

| 版式 `layout` | 适用叙事 | 核心结构 |
| --- | --- | --- |
| `layered-hero` | 讲清“底图 + 界面 + 透明前景”等 2–3 层叠加如何合成主视觉 | 左栏标题/斜叠层卡/底部图例，右侧倾斜或端正大主图与眉标 |
| `layered-bento` | 同时展示左下分层原理与右侧同一产品的 2–6 个真实模块 | 左栏标题/分层叠卡/底部图例，右侧 2 列 Bento 网格 |
| `section-assembly` | 拆解竖向整页或长图由哪些局部素材/模块拼装而成 | 左栏标题与编号摘要，中栏 2–5 组切片（支持单图或双图并排），右栏竖向长图 + SVG 虚线精准连线 |
| `module-bento` | 平等陈列 2–6 个高质量真实功能模块或不同状态界面 | 顶部品牌与标题，下方宽敞 3×2、2×2 或 2+3 网格，支持 `accent` 编号与标签 |
| `gallery-wall` | 展示多页完整网站、多屏 App 或多风格视觉墙 | `tilted-pages`（左栏标题 + 右侧 2–4 张规整多页，默认 `0deg`，可显式加 `tilt: true`）或 `collage`（规整画廊拼接墙，可选底部透明前景叠加） |
| `window-callout` | 解剖桌面窗口、剪辑工作台、仪表盘或复杂界面，并把 2–4 处细节真放大 | 横版默认全高左右分栏（`arrangement: "split"`）：左侧标题 + 端正高清主窗口（平面内聚焦框零漂移，可选 `tilt: true`）+ 右侧 2–4 张卡片后遮挡编号（`01`–`04`）放大面板与发光虚线标线；超宽条状界面可切 `arrangement: "stacked"`；`3:4`/`9:16` 竖版顶部主窗口配高对比定位针、下方横排放大卡 |
| `before-after` | 直观对比开启前/后、AI 处理前后、改版前后或深浅双态 | 顶部标题栏，横版端正 `0deg` 尺度对比（左卡略退缩、右卡放大提亮，可选 `tilt: true`），卡片后遮挡雕塑字（`BEFORE`/`AFTER` 或 `对比前`/`对比后`）配极简无框光束箭头；`stacked` 配合 `after.widthPercent < 100` 渲染发光竖向光学裁切线与无框大号 `deltaBadge` |
| `workflow-steps` | 展示 3–4 步连贯产品工作流（如录制→剪辑→导出、采集→整理→分享） | 默认 `style: "row"`（横版 3–4 张端正对齐卡配背后遮挡序号 `01`–`04` 与细箭头，`3:4`/`9:16` 自动竖排或 2×2）；可选 `style: "staged"`（左侧里程碑栏 + 右侧非对称 3D 层次舞台与背后大号序号，支持 `tilt: false` 切平） |

### 画幅比例与自动适配

默认 `--scale 2` 以 2 倍像素密度导出清晰成图：

| `ratio` | CSS 视口 | `@2x` 成图尺寸 | 典型场景与自动适配策略 |
| --- | --- | --- | --- |
| `16:9` | `1600×900` | `3200×1800` | X/Twitter、GitHub 封面、发布长图；标准横版构图 |
| `4:3` | `1440×1080` | `2880×2160` | Product Hunt 图集、长文插图；沿用横版并增加纵向呼吸感 |
| `3:4` | `1080×1440` | `2160×2880` | 小红书、移动端竖屏组图；自动切换为竖排、上下对比或高对比数字定位针 |
| `9:16` | `1080×1920` | `2160×3840` | 手机全屏竖版（视频号/抖音/Reels/Stories 封面与拆解图）；`workflow-steps` 3–4 步全部竖排串联、`window-callout` 2–3 项改为横卡竖排、`before-after` 宽敞上下对比 |
| `1:1` | `1200×1200` | `2400×2400` | 方图社媒卡片与缩略展示；自动切换为紧凑上文下图或 2 列平衡网格 |

也支持自定义像素视口字符串，例如 `"ratio": "1500x1000"`（宽高须在 `400`–`4000` 之间）。

## spec.json 规格参考

顶层结构支持默认 `brand`、`theme`、`displayStyle` 与必填 `cards` 数组：

```json
{
  "theme": "dark-studio",
  "displayStyle": "sans",
  "brand": {
    "name": "Studio App",
    "tag": "desktop workbench",
    "icon": "assets/logo.png"
  },
  "cards": [
    {
      "id": "01-window-callout",
      "layout": "window-callout",
      "ratio": "16:9",
      "accent": "#38bdf8",
      "title": "Everything on one timeline.",
      "subtitle": "Inspect waveforms, auto-cuts, and subtitle tracks without switching windows.",
      "window": { "image": "shots/main-window.png", "label": "MAIN WORKSPACE" },
      "callouts": [
        { "title": "Speech timeline", "caption": "Silence segments detected locally", "image": "shots/zoom-timeline.png", "targetX": 26, "targetY": 76 },
        { "title": "Inspector panel", "caption": "Real-time padding and cursor zoom", "image": "shots/zoom-inspector.png", "targetX": 82, "targetY": 44 },
        { "title": "Subtitle track", "caption": "Word-level alignment preview", "image": "shots/zoom-captions.png", "targetX": 54, "targetY": 82 }
      ]
    }
  ]
}
```

### 通用卡片字段

- `id`（必填）：唯一文件名标识，须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`（1–80 字符，以字母或数字开头，允许字母、数字、`.`、`_`、`-`）。
- `layout`（必填）：八种版式名称之一。
- `theme`（可选，继承顶层，默认 `"warm-paper"`）：`"warm-paper"`、`"dark-studio"` 或 `"cool-gallery"`。
- `displayStyle`（可选，继承顶层或按主题默认）：`"serif"` 或 `"sans"`。
- `lang`（可选，继承顶层，默认按卡片内容自动检测）：`"zh"` 或 `"en"`，用于显式控制自动默认标签（如 `对比前`/`对比后` 与 `BEFORE`/`AFTER`）和 HTML `lang` 属性。
- `ratio`（可选，默认 `"16:9"`）：`"16:9"`、`"4:3"`、`"3:4"`、`"9:16"`、`"1:1"` 或 `"WxH"`。
- `accent`（可选，默认取当前主题主墨色）：十六进制主题色，如 `"#38bdf8"`。
- `brand`（可选）：单卡覆盖顶层 `brand`；设为 `null` 隐藏品牌条。
- `title`（除全出血 `collage` 外必填）：主标题，支持 `\n` 换行。
- `subtitle`（可选）：两行内副标题。
- `eyebrow`（可选）：在 `layered-hero`、`section-assembly`、`layered-bento`（右侧实景主图正上方）及 `gallery-wall`（`tilted-pages` 标题区或有标题的 `collage` 标题区）渲染；顶栏版式（`window-callout`、`before-after`、`workflow-steps`、`module-bento`）为保持单一左对齐文字锚点，不渲染右上角漂浮眉标。
- `footer`（可选）：等宽页脚元信息，支持 `\n` 换行，八种版式均支持渲染。
- `tilt`（可选，布尔值）：精细控制卡片微倾。`before-after`、`window-callout`、`gallery-wall`、`section-assembly` 默认为 `false`（端正 `0deg`，保持界面截图文字最高清），显式设为 `true` 时开启微妙建筑倾斜；`layered-hero` 与 `workflow-steps`（`style: "staged"`）默认为 `true`，可设为 `false` 关闭倾斜。

### 通用图片槽位参数与截图比例建议

所有版式的图片对象（`hero`、`layers[]`、`items[]`/`pages[]`、`showcase`、`sections[]` 及 `sections[].images[]` 对象项、`window`、`callouts[]`、`before`/`after`、`steps[]`）均统一支持三个可选参数：

- `fit`（默认 `"cover"`）：`"cover"` 满出血铺满裁切（零内边距），或 `"contain"` 完整保留画面不裁边。在 `workflow-steps`、`module-bento`、`before-after`、`layered-bento` 与 `window-callout` 放大卡上使用 `fit: "contain"` 时，容器会自动施加 `.is-contain` 均匀 18px 舞台内边距、圆角与柔和投影，避免横向浮卡左右贴死边框而上下留出大片空白带。
- `position`（默认 `"top center"`，透明前景层默认 `"center bottom"`，切片/标注/对比默认 `"center"`）：标准 CSS `object-position`。
- `bg`（可选）：容器底色（支持 `#RGB`/`#RRGGBB`/`#RRGGBBAA`、`rgb()`/`rgba()`、`transparent`）。配合 `fit: "contain"` 时填入截图本身底色可无缝融合。

| 版式槽位 | 建议抓图比例 | 适用技巧 |
| --- | --- | --- |
| `layered-hero` 主图与层卡 | `16:10`（约 `1440×900`） | 深色窗口在 `dark-studio` 下默认暗底无缝贴合 |
| `layered-bento` 右侧 4 格 / 6 格 | 4 格约 `4:3`，6 格约 `16:10` | 按功能区裁切，不硬塞整屏微缩图 |
| `section-assembly` 切片与右栏长图 | 切片约 `16:7`–`16:9`，右栏约 `9:16`–`3:5` | 右栏用 `--full-page` 抓长图配 `position: "top center"` |
| `module-bento` 模块卡 | 约 `16:10`（`4:3`–`16:9` 皆宜） | 窄面板可用 `position: "top left"` 或 `contain` + `bg` |
| `gallery-wall` 整页 / 拼接墙 | `tilted-pages` 约 `2:5`；`collage` 约 `16:10` | 可按单图分别指定 `bg` 与 `position` |
| `window-callout` 主窗口与放大卡 | 主窗口自动贴合截图原生比例（默认 `contain` 无空边）；默认左右 `split` 右侧放大卡：3 格约 `2.8:1`–`3:1` 宽条、4 格约 `3.5:1`–`4:1` 宽条；`3:4`/`9:16` 横排放大卡约 `2:1`（`3:4` 4 格约 `16:10`） | 放大卡必须放 2×–4× 局部裁切细节，`(targetX, targetY)` 按主窗口**实际渲染图片内容区**计算 |
| `before-after` 双对比画框 | 横版各约 `4:3`–`16:10`；竖版各约 `16:10` | 两张图尽量同尺寸、同视口截取，确保对比公平直观 |
| `workflow-steps` 步骤卡画面 | `16:9` 下 3 步约 `1:1`、4 步约 `2:3`–`3:4` 竖构图（`~0.68:1`）；`3:4` 三步横卡约 `4:3` | 每步只展示一个关键界面状态，4 步横版优先使用竖向或方正局部裁切 |

### 各版式专用字段

1. **`layered-hero`**：`layers`（2–3 项，含 `image`、`label`、可选 `transparent` 及槽位参数）、`showIndexBadges`（默认 `false`）、`hero`（含 `image`、可选 `tilt` 及槽位参数）。
2. **`layered-bento`**：`layers`（2–3 项）、`showIndexBadges`（默认 `true`）、`items`（2–6 项，含 `image`、可选 `label` 及槽位参数）。
3. **`section-assembly`**：`sections`（2–5 项，含 `caption`、`summary`、`targetY`、`image` 或 `images` 路径/对象数组、`ratios` 及段级槽位参数）、`showcase`（含 `image`、可选 `tilt` 默认 `false` 及槽位参数）。
4. **`module-bento`**：`items`（2–6 项，含 `image`、可选 `index`、`label`/`caption` 及槽位参数）、`columns`（可选 `2` 或 `3`）。
5. **`gallery-wall`**：`variant`（`"tilted-pages"` 或 `"collage"`）、`tilt`（可选，默认 `false`）、`items`（也接受别名 `pages`）、`foreground`（可选，仅 `collage` 底部透明前景层）。
6. **`window-callout`**：
   - `arrangement`（可选，默认 `"split"`）：横版可选 `"split"`（左标题+高清主窗口 + 右侧全高放大卡堆叠与发光虚线，推荐）或 `"stacked"`（上主窗口 + 下排卡片 + 纵向虚线，仅用于超宽条状界面）。
   - `tilt`（可选，默认 `false`）：默认端正 `0deg` 保持文字锐利；设为 `true` 时为主窗口启用微妙 3D 透视角度，聚焦框随窗口平面精确投影，标线零漂移。
   - `window`（必填）：`{ "image": "...", "label": "可选窗口标签", "fit": "contain", "position": "center", "bg": "..." }`（默认 `fit: "contain"` 并自动收缩外框贴合截图原生比例，零空边）。
   - `callouts`（必填，2–4 项）：每项含 `image`、`title`、`targetX`（`0`–`100`，相对主图实际渲染宽度）、`targetY`（`0`–`100`，相对主图实际渲染高度），可选 `targetW` / `targetH`（`1`–`100`，在主图平面内绘制发光聚焦框）、`index`（默认 `"01"`..，渲染为卡片后遮挡大号雕塑字）、`tag`（等宽副标签）、`caption`（简短副说明）及 `fit`/`position`/`bg`。
7. **`before-after`**：
   - `orientation`（可选）：`"side-by-side"`（横版/方图默认）或 `"stacked"`（竖版默认；横版 `16:9`/`4:3` 显式设为 `"stacked"` 时上下全宽对比，适合横向时间轴、波形与宽幅长条）。
   - `tilt`（可选，默认 `false`）：默认端正 `0deg` 并保持左右尺度与光影反差；设为 `true` 时开启微妙建筑倾斜。
   - `deltaBadge`（可选）：无框大号变化量文案（如 `"-7.1 s · 8 cuts removed"` 或 `"-7.1 s · 删去 8 处停顿"`，含 `·` 时自动拆分为主数值 + 等宽副注），在 `widthPercent < 100` 时落于右侧渐变回收区与发光光学裁切线旁。
   - `before`（必填）：`{ "label": "对比前 / BEFORE", "meta": "可选右侧等宽短注", "image": "...", "caption": "可选 1 行说明", "fit": "cover", "position": "center", "bg": "..." }`（省略 `label` 时中文卡片默认 `"对比前"`，英文卡片默认 `"BEFORE"`，渲染为卡片后遮挡雕塑字）。
   - `after`（必填）：同上结构（省略 `label` 时中文卡片默认 `"对比后"`，英文卡片默认 `"AFTER"`），自带更强主体投影、主题反光晕与光学高光顶沿；在 `stacked` 模式下还支持可选 `widthPercent`（`35`–`100`，默认 `100`），小于 `100` 时左对齐按该比例渲染 `after` 画框，并在右侧绘制发光竖向光学裁切线与柔光回收区。
   - `compareNote`（可选）：右下角等宽对比注释。
8. **`workflow-steps`**：
   - `style`（可选，默认 `"row"`）：`"row"`（规整横排/竖排步骤卡配卡片后遮挡序号 `01`–`04`）或 `"staged"`（横版左侧里程碑栏 + 右侧非对称 3D 层次舞台与背后遮挡大号序号，默认 `tilt: true`，支持 `tilt: false` 切平）。
   - `steps`（必填，3–4 项）：每项含 `image`、`title`，可选 `index`（默认 `"01"`..）、`caption`（1–2 行说明）及 `fit`/`position`/`bg`。
