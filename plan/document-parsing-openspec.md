# SmartQA 文档解析升级 — OpenSpec 需求规格

> 版本: 1.0 | 作者: Hermes | 日期: 2026-05-13

---

## 1. 现状盘点

### 1.1 已有能力

PDF 解析已在 `backend/app/api/knowledge.py:_read_pdf()` 实现三阶回退链：

```
pymupdf4llm (主) → opendataloader-pdf (备用) → pdfplumber (兜底)
```

- **pymupdf4llm**：当前主解析器，输出结构化 Markdown，保留标题层级和表格
- **opendataloader-pdf**：已在 `requirements.txt`（`opendataloader-pdf>=0.1.0`），但作为 fallback 藏在 pymupdf4llm 失败后才调用
- **pdfplumber**：最终兜底，表格→Markdown 手动转换

支持的格式：`.pdf` / `.docx` / `.txt` / `.md`

### 1.2 当前问题

| 问题 | 严重度 | 说明 |
|------|--------|------|
| opendataloader 从未被真正测试 | 🔴 高 | 作为 fallback 藏在 try/except 后面，pymupdf4llm 正常情况下不会失败，所以 opendataloader 路径从未走过 |
| API 调用方式可能不对 | 🟡 中 | 代码使用 `opendataloader_pdf.run()`，但官方 API 是 `opendataloader_pdf.convert()` |
| opendataloader 需要 Java 11+ | 🟡 中 | 环境依赖未检查，如果 pymupdf4llm 真的挂了，opendataloader 可能因为缺 Java 也挂 |
| 回退顺序不合理 | 🟡 中 | 精度更高的 opendataloader（benchmark #1, 0.907）当备胎，精度一般的 pymupdf4llm 当主力 |
| 复杂 PDF 处理差 | 🟢 低 | 当前方案对多栏布局、扫描件、混合排版支持弱——供应链场景常见这些（合同扫描件、ERP 导出的多栏报表） |

### 1.3 opendataloader-pdf 能力评估

| 维度 | pymupdf4llm（当前主） | opendataloader-pdf | 胜出 |
|------|----------------------|-------------------|------|
| 综合精度 | 中上 | **0.907**（benchmark #1） | opendataloader |
| 表格提取 | 0.85 | **0.93** | opendataloader |
| 多栏布局 | 一般 | **优秀**（确定性阅读顺序） | opendataloader |
| 扫描PDF/OCR | ❌ | ✅ Hybrid模式+AI引擎 | opendataloader |
| 页眉页脚/水印 | 需手动处理 | **自动过滤** | opendataloader |
| 输出格式 | Markdown | Markdown + JSON(含坐标) + HTML | opendataloader |
| 速度（本地模式） | 快 | 0.05s/页 | 相当 |
| 成熟度 | 高（PyMuPDF 生态） | 中（2024年新项目） | pymupdf4llm |
| 需要额外依赖 | 无 | **Java 11+** | pymupdf4llm |
| LangChain 集成 | 无 | 有官方集成 | opendataloader |
| 确定性输出 | 是 | 是（本地模式） | 相当 |

---

## 2. 需求分析

### REQ-1: opendataloader-pdf 提升为主解析器 [P0]

**描述:** 将 PDF 解析顺序从「pymupdf4llm→opendataloader→pdfplumber」改为「opendataloader→pymupdf4llm→pdfplumber」。

**为什么是 P0:**
- 用户明确指定要用 opendataloader-pdf
- opendataloader 在 benchmark 上精度更高（0.907 vs ~0.85）
- 当前 opendataloader 路径从未被测试——连 API 都可能不对
- 提升了就是真实的技术决策，面试可讲

**技术细节:**
- `opendataloader_pdf.convert()` 是官方推荐 API，不是 `.run()`
- 需要验证 `convert()` 的返回格式（直接返回 str 还是保存到文件）
- 保留 Markdown 输出格式不变，后续 pipeline 不需要改

**验收标准:**
- [ ] 修正 opendataloader API 调用（`.run()` → `.convert()` 或确认正确签名）
- [ ] 调换 `_read_pdf()` 中的解析顺序：opendataloader 优先
- [ ] 找一个真实 PDF（供应链合同/报表）验证解析结果
- [ ] pymupdf4llm 仍作为 fallback（缺 Java 时自动降级）
- [ ] pdfplumber 作为最终兜底

**面试价值:**
「文档解析我用了三阶回退方案——首选 opendataloader-pdf（benchmark #1，综合精度 0.907，自动处理多栏布局和扫描件），fallback 到 pymupdf4llm（不需要 Java），最终兜底 pdfplumber。这个链式降级设计保证了不同环境、不同文档质量都能正常工作。」

---

### REQ-2: Java 环境检测与友好报错 [P1]

**描述:** opendataloader-pdf 需要 Java 11+。启动时检测 Java 是否可用，不可用时自动降级并告知用户。

**为什么是 P1:** 避免"装了但跑不了"的尴尬——如果用户环境没 Java，opendataloader 静默失败回退到 pymupdf4llm，用户完全不知道。

**验收标准:**
- [ ] 在 `_read_pdf()` 的 opendataloader 调用前检查 `java -version`
- [ ] Java 不可用时打 warning 日志 + 自动回退到 pymupdf4llm
- [ ] 日志明确说「opendataloader 需要 Java 11+，当前环境未检测到 Java，已自动回退到 pymupdf4llm」

**面试价值:**
「opendataloader 的核心引擎是 Java 实现的，我在启动时检测 Java 环境——有就用高精度解析，没有就自动降级。」

---

### REQ-3: DOCX 解析增强 [P2]

**描述:** 当前 DOCX 解析 `_read_docx()` 只支持 python-docx 和 pymupdf fallback，没有 opendataloader 链路。补充 DOCX 的多级 fallback。

**验收标准:**
- [ ] DOCX 解析也加入 opendataloader 支持
- [ ] fallback 链: python-docx → opendataloader → pymupdf

---

### REQ-4: 端到端验证脚本 [P1]

**描述:** 写一个测试脚本，用不同类型的 PDF（纯文本、表格、多栏、扫描件）验证整个解析链。

**验收标准:**
- [ ] 脚本放在 `scripts/test_document_parsing.ps1`（PowerShell 调 Python）
- [ ] 测试至少 3 种 PDF 类型
- [ ] 输出每层的解析结果和耗时
- [ ] 验证 Markdown 输出包含表格和标题

---

## 3. 不做的事（明确排除）

- ❌ 不修改切片逻辑（`_chunk_text()` 已经是语义切片，保留不变）
- ❌ 不改变 Milvus 入库流程
- ❌ 不改变 API 接口签名
- ❌ 不引入 GPU 依赖（opendataloader 本地模式不需要 GPU）
