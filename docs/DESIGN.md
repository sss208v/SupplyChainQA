# DESIGN.md — 供应链智能问答系统 (Supply Chain QA)

> 版本 1.0 · 设计系统规范 · Vue3 + Element Plus

---

## 1. Visual Theme（视觉主题）

**Philosophy**: 专业即信任——让制造业用户在信息密集的环境中感到掌控感，每一个数据点都清晰可读，每一次操作都有明确反馈。

**Direction**: `enterprise-utility, data-dense, precision-first`

**Personality**: `authoritative, reliable, efficient`（权威、可靠、高效）——像专业的 ERP 系统，但有现代 SaaS 的流畅感。

**Reference**: Linear（深色侧边导航 + 浅色工作区）、Supabase（数据密集仪表盘）、Vercel（简洁 Header + 卡片布局）

**核心设计语言**：
- 侧边栏使用深蓝黑（`#1d1e2c`），与浅色主内容区形成明确分区
- 主内容区采用低饱和背景（`#f5f7fa`），卡片白色浮起
- 强调色使用科技蓝（`#409eff`），符合 Element Plus 默认主色，不做大幅修改
- 代码/技术数据区域（Token用量、工具参数、Session ID）使用等宽字体
- 语义色（成功/警告/错误）严格用于状态反馈，不装饰化使用

---

## 2. Color Palette（调色板）

### Primary — 科技蓝系

| Token | HEX | OKLCh | Usage |
|-------|-----|-------|-------|
| `--color-primary` | `#409eff` | oklch(67% 0.18 250) | CTA 按钮、链接、激活状态、焦点环 |
| `--color-primary-hover` | `#337ecc` | oklch(57% 0.17 252) | 主色悬停态 |
| `--color-primary-light` | `#ecf5ff` | oklch(96% 0.04 250) | 激活背景、选中高亮填充 |
| `--color-primary-dark` | `#2d6fba` | oklch(50% 0.16 255) | 按下态 |

### Neutral — 主界面灰阶

| Token | HEX | Usage |
|-------|-----|-------|
| `--color-bg-page` | `#f5f7fa` | 全局页面背景（main-content 区） |
| `--color-bg-card` | `#ffffff` | 卡片、对话框、Drawer 背景 |
| `--color-bg-subtle` | `#f8f9fb` | 输入区背景、代码块外层、Schema区域 |
| `--color-bg-hover` | `#f0f4f8` | 列表悬停行、菜单项悬停 |
| `--color-border` | `#e4e7ed` | 默认分隔线、卡片边框 |
| `--color-border-light` | `#f0f0f0` | 轻量分隔（chat-header border-bottom） |
| `--color-text-primary` | `#1a1f36` | 主标题、关键数据（升级自现有 `#303133`） |
| `--color-text-body` | `#303133` | 正文段落 |
| `--color-text-secondary` | `#606266` | 次要文字、描述、Label |
| `--color-text-placeholder` | `#909399` | 占位符、空状态文字 |
| `--color-text-meta` | `#b0b3b8` | Token用量、时间戳、ID等最低级文字 |

### Sidebar — 深色导航专用

| Token | HEX | Usage |
|-------|-----|-------|
| `--color-sidebar-bg` | `#1d1e2c` | 侧边栏背景 |
| `--color-sidebar-border` | `rgba(255,255,255,0.08)` | 侧边栏内分隔线 |
| `--color-sidebar-text` | `#a0a3bd` | 菜单文字（非激活） |
| `--color-sidebar-active` | `#409eff` | 激活菜单文字 |
| `--color-sidebar-hover` | `rgba(255,255,255,0.06)` | 历史记录 hover 背景 |
| `--color-sidebar-meta` | `#6c6e7e` | 历史记录时间、Session ID |

### Semantic — 语义状态色

| Token | HEX | OKLCh | Usage |
|-------|-----|-------|-------|
| `--color-success` | `#67c23a` | oklch(70% 0.20 145) | 在线状态、已索引、好评、高置信度 |
| `--color-warning` | `#e6a23c` | oklch(73% 0.16 70) | 处理中、中等置信度、需要审批 |
| `--color-danger` | `#f56c6c` | oklch(64% 0.21 22) | 删除、错误、差评、低置信度 |
| `--color-info` | `#909399` | oklch(62% 0.005 260) | 中性信息、来源标签 |

### Code & Data — 技术内容专用

| Token | HEX | Usage |
|-------|-----|-------|
| `--color-code-bg` | `#1e1e1e` | 代码块背景（pre 区域） |
| `--color-code-text` | `#d4d4d4` | 代码文字 |
| `--color-code-inline-bg` | `#e8eaec` | 行内代码背景 |
| `--color-schema-output` | `#f0f9eb` | 工具输出 Schema 背景 |
| `--color-schema-text` | `#67c23a` | 工具输出 Schema 文字 |

---

## 3. Typography（排版）

### Font Stacks

```css
/* Heading — 清晰权威，Inter 在屏幕上极具可读性 */
--font-heading: 'Inter', 'PingFang SC', 'Microsoft YaHei', -apple-system, sans-serif;

/* Body — 正文使用系统字体栈，保证 CJK 渲染质量 */
--font-body: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', sans-serif;

/* Mono — 技术数据、代码、Session ID、Token用量 */
--font-mono: 'SF Mono', 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
```

> **中文字体说明**：中文字体始终 fallback 到系统字体，不加载外部中文 webfont，避免渲染延迟和截字问题。

### Scale — 排版层级

| Level | Size | Weight | Line-height | Letter-spacing | Usage |
|-------|------|--------|-------------|----------------|-------|
| Page Title | `22px / 1.375rem` | `700` | `1.3` | `-0.2px` | 页面主标题（如"工具管理"）|
| Section H | `18px / 1.125rem` | `600` | `1.4` | `-0.1px` | 卡片 Header 标题 |
| Sub H | `15px / 0.9375rem` | `600` | `1.4` | `0` | 工具名称、面板标题 |
| Body | `14px / 0.875rem` | `400` | `1.6` | `0` | 标准正文、描述、输入 |
| Small | `13px / 0.8125rem` | `400` | `1.5` | `0` | 工具描述、参数说明 |
| Caption | `12px / 0.75rem` | `400` | `1.4` | `0.1px` | 历史记录文字、类型标签 |
| Micro | `11px / 0.6875rem` | `400` | `1.4` | `0.2px` | Token用量、Session ID、时间戳 |
| Mono Data | `12px / 0.75rem` | `500` | `1.4` | `0.5px` | 物料编码、工具参数名、Metric值 |

### 中英文混排规则
- 中文正文段落行高 `1.7`（比纯英文稍宽，改善阅读节奏）
- 数字和英文单词与中文之间保留半角空格（排版层面约束，CSS 无法强制，需代码规范）
- 技术词汇（MAT-001、PO-20250101、Session ID）使用 `--font-mono` 渲染

---

## 4. Component Styles（组件样式）

本系统基于 Element Plus，以下规范为 **覆盖层规则**，不替换 Element Plus 基础样式，而是通过 CSS 变量和 scoped 选择器增强。

### Button — 按钮

```
Primary:
  background: var(--color-primary)
  color: #fff
  border-radius: 6px
  padding: 8px 16px
  font-weight: 500
  hover: background var(--color-primary-hover), transform translateY(-1px)
  
Secondary / Default:
  background: #fff
  border: 1px solid var(--color-border)
  color: var(--color-text-body)
  border-radius: 6px
  hover: border-color var(--color-primary), color var(--color-primary)

Text Button:
  background: transparent
  color: var(--color-text-secondary)
  hover: color var(--color-primary), background transparent

Danger:
  background: var(--color-danger) [或 text danger 变体]
  用于删除操作，不用于一般性警告提示

Size:
  Large: height 40px, font 15px   → 登录页专用
  Default: height 32px, font 14px → 一般操作
  Small: height 24px, font 12px   → 卡片内辅助操作
```

### Card — 卡片

```
Background: var(--color-bg-card) = #fff
Border: 1px solid var(--color-border) = #e4e7ed
Border-radius: 8px
Padding: 20px (default) / 16px (compact, 如 stat-card)
Shadow: 0 1px 4px rgba(0,0,0,0.06)     ← 默认浮起感
Shadow hover: 0 4px 12px rgba(0,0,0,0.1) ← shadow="hover" 时
Card Header:
  padding: 12px 20px
  border-bottom: 1px solid var(--color-border)
  font-weight: 600
  font-size: 14px
  display: flex; justify-content: space-between; align-items: center
```

### Input / Textarea — 输入框

```
Height: 36px (default) / 40px (large, 登录页)
Border: 1px solid var(--color-border)
Border-radius: 6px
Focus ring: box-shadow 0 0 0 2px rgba(64,158,255,0.2), border-color var(--color-primary)
Background: #fff
Placeholder: var(--color-text-placeholder) = #909399

Textarea (对话输入框):
  resize: none
  min-height: 52px (2行)
  border-radius: 8px
  padding: 10px 14px
  
  专用增强: 对话输入框底部操作栏紧贴 textarea 下方，
  整体作为一个组合区块处理，有浅色背景 var(--color-bg-subtle)
```

### Tag / Badge — 标签

```
Round Tag (意图标签、状态标签):
  height: 20px
  padding: 0 8px
  font-size: 11px
  border-radius: 10px
  effect="plain" 为主（描边+浅背景，不用深色填充）

数据标签 (部门权限、文档状态):
  size="small"
  effect="plain" 或 effect="light"
  
特殊规则:
  ❌ 不在同一行堆叠超过 5 个 Tag（可折叠展示）
  ✅ intent-tag 中的多个 Tag 水平排列，gap: 6px，可换行
```

### Navigation / Sidebar — 导航

```
Sidebar:
  width: 220px (展开) / 64px (折叠)
  background: var(--color-sidebar-bg) = #1d1e2c
  transition: width 0.28s cubic-bezier(0.4,0,0.2,1)  ← 更丝滑的过渡曲线

Logo区:
  height: 60px
  border-bottom: 1px solid var(--color-sidebar-border)
  icon size: 28px, 颜色: #fff
  文字: font-size 16px, font-weight 700, color #fff
  不使用 letter-spacing 拉宽

Menu Item (激活):
  background: rgba(64,158,255,0.12)  ← 替代默认的无背景态
  color: var(--color-sidebar-active) = #409eff
  左侧竖线指示: 3px 宽, border-left: 3px solid #409eff
  border-radius: 0 6px 6px 0  ← 右侧圆角

Menu Item (普通):
  color: var(--color-sidebar-text) = #a0a3bd
  hover: background rgba(255,255,255,0.05), color #d0d3e8

对话历史区域:
  max-height: 280px
  可滚动，滚动条宽 4px，颜色 rgba(255,255,255,0.15)
  
Header:
  height: 56px
  background: var(--color-bg-card) = #fff
  border-bottom: 1px solid var(--color-border)
  padding: 0 24px
  box-shadow: 0 1px 0 var(--color-border)  ← 替代现有的 box-shadow，更轻
```

### Avatar — 头像

```
User Avatar:
  size: 36px
  background: var(--color-primary) = #409eff
  border-radius: 50%

AI Avatar:
  size: 36px
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%)
  ← 比纯绿色 (#67c23a) 更有品质感，与登录页背景渐变呼应
  border-radius: 50%
```

### Chat Message — 消息气泡

```
User Message:
  background: var(--color-primary) = #409eff
  color: #fff
  border-radius: 12px 12px 4px 12px  ← 右下角切角，视觉指向性
  max-width: 70%
  padding: 10px 16px

AI Message:
  background: var(--color-bg-card) = #fff
  border: 1px solid var(--color-border)  ← 增加边框，比纯灰背景更清晰
  color: var(--color-text-body)
  border-radius: 4px 12px 12px 12px  ← 左上角切角
  max-width: 75%  ← AI消息略宽，因内容更多
  padding: 12px 16px
  box-shadow: 0 1px 3px rgba(0,0,0,0.06)

Loading Dots:
  color: var(--color-primary) 而非灰色
  animation 保持现有 bounce keyframe
```

### Stat Card — 统计卡片（知识库页）

```
布局: flex, align-items center, gap 16px
Icon区: 
  width: 48px; height: 48px
  border-radius: 12px
  background: 对应颜色的 10% opacity（如蓝色 → rgba(64,158,255,0.1)）
  icon color: 对应颜色（替代直接透出）
Value: font-size 24px, font-weight 700, color var(--color-text-primary)
Label: font-size 12px, color var(--color-text-placeholder), margin-top 2px
```

### Login Card — 登录卡片

```
外层背景: linear-gradient(135deg, #667eea 0%, #764ba2 100%)  ← 保留现有渐变
Card:
  width: 400px
  padding: 40px
  border-radius: 16px  ← 从 12px 升至 16px，更现代
  box-shadow: 0 24px 80px rgba(102,126,234,0.25)  ← 带品牌色的阴影
  
Header区:
  Logo Icon: 40px，渐变色（与背景呼应）
  标题: font-size 26px, font-weight 800, color var(--color-text-primary)
  副标题: font-size 13px, color var(--color-text-placeholder)

Demo Accounts:
  border-top: 1px solid var(--color-border)
  Tag: 悬停有 scale(1.03) 变换 + cursor pointer
```

### Tool Node Card — 工具节点卡片

```
保留现有 Dify 风格，细化如下:
Border: 1px solid var(--color-border)
Border-radius: 10px
padding: 20px
transition: border-color 0.2s, box-shadow 0.2s

Hover:
  border-color: var(--color-primary)
  box-shadow: 0 4px 16px rgba(64,158,255,0.12)

Active (selected):
  border-color: var(--color-primary)
  background: var(--color-primary-light) = #ecf5ff

Node Icon:
  width: 40px; height: 40px; border-radius: 10px
  不直接用纯色，改用对应颜色的渐变:
    query_inventory: linear-gradient(135deg, #409eff, #5bc5ff)
    query_order: linear-gradient(135deg, #67c23a, #88d85e)
    create_ticket: linear-gradient(135deg, #e6a23c, #f0c060)
    get_datetime: linear-gradient(135deg, #909399, #b0b3b8)
    get_knowledge: linear-gradient(135deg, #f56c6c, #ff8c8c)
```

---

## 5. Layout（布局）

### 整体布局结构

```
┌─────────────────────────────────────────────────┐
│  Sidebar (220px)  │  Header (56px)               │
│  [深色 #1d1e2c]   ├──────────────────────────────┤
│                   │                              │
│  Logo             │  Main Content Area           │
│  Nav Menu         │  background: #f5f7fa         │
│  History          │                              │
│  [折叠按钮]        │  padding: 20px               │
│                   │  max-width: 内容宽度自适应     │
└─────────────────────────────────────────────────┘
```

### Grid — 栅格

```
Container max-width:
  Knowledge / Evaluate: max-width 1000px, margin 0 auto
  Tools: max-width 1200px, margin 0 auto
  Chat: 无 max-width，全占 main content 区域

Columns: 灵活 — Knowledge 统计用 3列, Tools 用 auto-fill minmax(340px, 1fr)
Gutter: 16px (标准卡片间距), 20px (宽松区域)
```

### Spacing Scale — 间距体系

| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | `4px` | icon与文字间距、Tag内边距 |
| `--space-2` | `8px` | 按钮间距、inline gap |
| `--space-3` | `12px` | 卡片内 Header padding（垂直） |
| `--space-4` | `16px` | 标准内边距、卡片间距 |
| `--space-5` | `20px` | 消息列表 padding、Content padding |
| `--space-6` | `24px` | 区块间距（stats-row 下方、login header） |
| `--space-8` | `32px` | 大节间距 |
| `--space-10` | `40px` | Login card padding |

### Chat Layout — 对话页专用

```
.chat-container:
  height: calc(100vh - 96px)  ← 从 120px 减至 96px，给消息区更多空间
  border-radius: 10px
  overflow: hidden

.message-list:
  padding: 20px 24px  ← 水平 padding 略增
  scroll-behavior: smooth

.input-area:
  padding: 12px 20px 16px  ← 顶部减少，视觉更紧凑
  background: var(--color-bg-subtle)
  border-top: 1px solid var(--color-border)

.input-wrapper:
  max-width: 860px  ← 从 800px 略增

欢迎屏 quick-actions:
  flex-direction: column (mobile) / row (desktop)
  max 3个按钮，水平排列，每个按钮 max-width 300px
```

---

## 6. Depth & Elevation（深度与层级）

| Level | CSS | Usage |
|-------|-----|-------|
| Flat | `none` | Sidebar 内元素、输入框 |
| Surface | `0 1px 3px rgba(0,0,0,0.06)` | 消息气泡 AI侧、默认卡片 |
| Raised | `0 1px 4px rgba(0,0,0,0.08)` | Header bar |
| Card Hover | `0 4px 12px rgba(0,0,0,0.10)` | 卡片 hover 态、工具节点 hover |
| Floating | `0 8px 24px rgba(0,0,0,0.12)` | Drawer、Dropdown、Popconfirm |
| Modal | `0 20px 60px rgba(0,0,0,0.18)` | 登录卡片、对话框 |
| Brand Modal | `0 24px 80px rgba(102,126,234,0.25)` | 登录卡片（带品牌色） |

### Z-index Scale

| Layer | Value | Component |
|-------|-------|-----------|
| Base | `0` | 普通内容 |
| Sticky | `100` | Header |
| Sidebar | `200` | 侧边栏折叠按钮 |
| Dropdown | `1000` | Element Plus Dropdown |
| Overlay | `2000` | Drawer backdrop |
| Drawer | `2001` | Drawer panel |
| Modal | `3000` | Dialog |
| Toast | `9000` | ElMessage 通知 |

---

## 7. Cautions（注意事项）

### Never Do — 禁止模式

- ❌ **禁止在非技术内容使用等宽字体** — 只在 Token用量、物料编码、Session ID、代码块中使用 `--font-mono`；普通文字统一使用 `--font-body`
- ❌ **禁止在同一行放超过 5 个 Tag** — 意图标签区超过 4 个时，使用折叠 `el-tooltip` 展示更多
- ❌ **禁止在 Sidebar 内使用浅色背景色块** — Sidebar 内所有交互状态只通过 opacity/透明度处理
- ❌ **禁止给文字型按钮 (text button) 加边框** — Text 模式只改变 color，不加任何 border 或 background
- ❌ **禁止滥用 `box-shadow`** — 同一个元素不叠加超过 1 层阴影，避免过度"浮夸感"
- ❌ **禁止在卡片 Header 用超过 600 的字重** — Header 标题 `font-weight: 600`，内容区标题 `500`
- ❌ **禁止用纯色 `danger` 按钮做常规删除** — 删除统一用 `text type="danger"` 配合 `el-popconfirm`
- ❌ **禁止在消息气泡内显示原始 JSON** — Tool call 的 input/observation 如超 100 字符需截断 + 展开

### Prefer — 推荐替代

- ✅ 用 `effect="plain"` 的 Tag 表达状态（比 `effect="dark"` 更轻，减少视觉噪声）
- ✅ 渐变色只用于品牌元素（登录背景、AI头像、Tool Node图标），不用于数据展示区
- ✅ 空状态用 `el-empty` + 描述文字，不用空白 div
- ✅ 数据缺失时显示 `—`，不显示 `null` 或 `0`
- ✅ 加载状态统一用 `v-loading` 指令，不自制 spinner 覆盖层

---

## 8. Responsive Behavior（响应式行为）

### Breakpoints

| Name | Width | Behavior |
|------|-------|----------|
| Mobile | `< 768px` | 侧边栏默认折叠 (64px), 消息气泡 max-width 90% |
| Tablet | `768px - 1280px` | 侧边栏 220px，Knowledge/Evaluate 2列布局 |
| Desktop | `> 1280px` | 完整布局，Tools Grid auto-fill |
| Wide | `> 1600px` | Content max-width 生效，左右留白 |

### Adaptation Rules

- **侧边栏**：移动端默认折叠至 64px，折叠按钮始终可见；Tablet 及以上默认展开
- **消息气泡**：Mobile 下 `max-width: 88%`，Desktop 保持 `70%` (user) / `75%` (AI)
- **对话输入框**：Mobile 下按钮区改为图标模式（去掉"发送"文字），减少空间占用
- **工具网格**：`grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))` — 自然适配
- **知识库统计卡片**：Mobile 下从 3列变为单列堆叠
- **Header 用户信息**：Mobile 下隐藏 department Tag，只显示用户名

---

## 9. Agent Prompt Guide（AI 生成指南）

本节供 AI 辅助前端开发时参考，确保生成代码符合本设计系统。

### Key Instructions

1. **使用 CSS 变量而非硬编码颜色** — 所有颜色引用 `var(--color-*)` 变量，便于全局切换
2. **Element Plus 组件优先** — 不重复造轮子，通过 `:deep()` 修改 EP 组件内部样式
3. **scoped 样式** — 所有 `.vue` 文件样式块使用 `<style scoped>`，组件样式不污染全局
4. **中文字体不加载外部资源** — font-family 只使用系统字体栈，不引入 CDN 字体
5. **侧边栏深色系不混入浅色元素** — Sidebar 内所有颜色从 `--color-sidebar-*` 取值
6. **语义色严格语义化** — success/warning/danger 只表示"状态"，不做装饰用途
7. **动画克制** — transition 时长 `0.2s - 0.3s`，ease 曲线；不使用复杂 keyframe（除 loading-dots）
8. **Token 用量 / Session ID 使用 `--font-mono`** — 这类元数据在视觉上应有别于正文

### Quick CSS Snippet — 核心变量

```css
:root {
  /* === Brand === */
  --color-primary:        #409eff;
  --color-primary-hover:  #337ecc;
  --color-primary-light:  #ecf5ff;
  --color-primary-dark:   #2d6fba;

  /* === Neutral === */
  --color-bg-page:        #f5f7fa;
  --color-bg-card:        #ffffff;
  --color-bg-subtle:      #f8f9fb;
  --color-bg-hover:       #f0f4f8;
  --color-border:         #e4e7ed;
  --color-border-light:   #f0f0f0;

  /* === Text === */
  --color-text-primary:   #1a1f36;
  --color-text-body:      #303133;
  --color-text-secondary: #606266;
  --color-text-placeholder:#909399;
  --color-text-meta:      #b0b3b8;

  /* === Sidebar === */
  --color-sidebar-bg:     #1d1e2c;
  --color-sidebar-text:   #a0a3bd;
  --color-sidebar-active: #409eff;
  --color-sidebar-meta:   #6c6e7e;

  /* === Semantic === */
  --color-success:        #67c23a;
  --color-warning:        #e6a23c;
  --color-danger:         #f56c6c;
  --color-info:           #909399;

  /* === Code === */
  --color-code-bg:        #1e1e1e;
  --color-code-text:      #d4d4d4;
  --color-code-inline-bg: #e8eaec;

  /* === Typography === */
  --font-heading: 'Inter', 'PingFang SC', 'Microsoft YaHei', -apple-system, sans-serif;
  --font-body:    -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', sans-serif;
  --font-mono:    'SF Mono', 'JetBrains Mono', 'Consolas', 'Courier New', monospace;

  /* === Spacing === */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;

  /* === Elevation === */
  --shadow-surface:   0 1px 3px rgba(0,0,0,0.06);
  --shadow-raised:    0 1px 4px rgba(0,0,0,0.08);
  --shadow-card-hover:0 4px 12px rgba(0,0,0,0.10);
  --shadow-floating:  0 8px 24px rgba(0,0,0,0.12);
  --shadow-modal:     0 20px 60px rgba(0,0,0,0.18);

  /* === Border Radius === */
  --radius-sm:  4px;
  --radius-md:  6px;
  --radius-lg:  8px;
  --radius-xl:  12px;
  --radius-2xl: 16px;
  --radius-full:9999px;
}
```

### Component Quick Reference — 常用组件速查

```css
/* ── AI 消息气泡升级（替换原 .ai-content） ── */
.ai-content {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  color: var(--color-text-body);
  border-radius: 4px var(--radius-xl) var(--radius-xl) var(--radius-xl);
  box-shadow: var(--shadow-surface);
}

/* ── AI 头像渐变（替换原纯绿色） ── */
.ai-avatar {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

/* ── 统计卡片图标区升级 ── */
.stat-icon-wrap {
  width: 48px; height: 48px;
  border-radius: var(--radius-xl);
  display: flex; align-items: center; justify-content: center;
}
.stat-icon-wrap--blue   { background: rgba(64,158,255,0.10); }
.stat-icon-wrap--green  { background: rgba(103,194,58,0.10); }
.stat-icon-wrap--yellow { background: rgba(230,162,60,0.10); }

/* ── Sidebar 激活菜单项左侧指示线 ── */
.el-menu-item.is-active {
  background: rgba(64,158,255,0.12) !important;
  border-left: 3px solid var(--color-primary);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
}

/* ── Token 用量 meta 文字 ── */
.token-usage {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-meta);
  letter-spacing: 0.2px;
}
```

### Page-by-Page Notes — 各页面特别说明

| 页面 | 关键设计点 |
|------|-----------|
| **Login** | 背景渐变保留；卡片 border-radius 升至 16px；AI头像/icon 与渐变背景呼应 |
| **Chat** | 欢迎屏 icon 换为渐变色圆形背景；quick-action 按钮 hover 有微上移；输入区有明确边界感 |
| **ChatMessage** | AI 消息加白底 + 边框，替代灰色背景；意图 Tag 区域水平排列，超 4 个折叠 |
| **Knowledge** | 统计卡片 icon 区加有色底块；上传拖拽区有 dashed 边框 + primary 色 hover |
| **Tools** | 工具节点 icon 用渐变色；激活态有 primary-light 背景；测试 Drawer 有明确分区 |
| **Evaluate** | 数据指标用 `--font-mono` 渲染；进度条 label 位置居中；满意度进度条加渐变色 |
