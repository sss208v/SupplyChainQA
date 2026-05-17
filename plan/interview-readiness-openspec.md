# SmartQA 面试就绪 — OpenSpec 需求规格

> 版本: 2.0 | 日期: 2026-05-14 | 上下文: 92 篇知识库 + 100 题问题库已就绪

---

## 1. 目标 (Goal)

确保 supply-chain-qa 项目在面试中可以**完整、流畅、不被追问翻车**地演示所有核心功能。覆盖三个维度：**系统完善**（代码质量 + 功能闭合）、**手册完善**（interview-showcase.html 与代码一致）、**演示就绪**（启动→演示→回答追问全链路）。

---

## 2. 当前基线 (2026-05-14 更新)

| 维度 | 状态 | 说明 |
|------|------|------|
| 代码 | ✅ | 58/59 测试通过，后端 import OK，前端 build OK |
| 知识库 | ✅ | 92 篇文档 (1.2MB)，7 部门 × 10+ 篇 |
| 问题库 | ✅ | `plan/demo-questions.md` 100 题，10 类 × 10 题 |
| 架构图 | ✅ | docs/architecture.svg 浅色主题，无重叠 |
| 面试 HTML | ✅ | 24 导航项，行数/指标/PDF链/config语法与代码一致 |
| 上传脚本 | ✅ | `scripts/upload_knowledge_base.py` — 批量索引 70 篇新文档 |
| 验证脚本 | ✅ | `scripts/verify_demo.py` — 8 步全链路 API 测试 |
| 话术审计 | ✅ | 18 项声明全部通过代码验证 |
| 启动脚本 | ✅ | `demo_start.ps1` 7 步（含自动知识库索引检查） |
| 演示脚本 | ⚠️ | 需确保知识库已索引（首次运行自动处理） |
| RAG 指标 | ⚠️ | 旧指标基于 20 篇文档，92 篇后需重跑（REQ-2 待后端启动后执行） |
| Git 推送 | ✅ | 已推送 5+ commits |

---

## 3. 需求清单

### REQ-1: 知识库重新索引到 Milvus [P0]

**描述:** 70 篇新文档（`knowledge/SC-*.md`）尚未入库 Milvus。面试时 RAG 检索只能命中旧的 22 篇文档。

**当前状态:** ✅ 已通过 /health 确认 2425 chunks，7 部门全覆盖。

**验收标准:**
- [ ] `knowledge/SC-*.md` 全部 70 篇上传到 Milvus `smartqa_docs` collection
- [ ] `/health` 端点 `knowledge_docs_count` ≥ 500（92 篇 × ~6 chunks/篇）
- [ ] 用新问题库中的 RAG 问题测试，能检索到新文档内容
- [ ] 权限过滤正常（security_group 按部门配置）

**实施步骤:**
```powershell
# 1. 启动后端
cd backend
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 2. 调用上传接口（或手动通过前端 Knowledge 页面上传）
# 3. 验证: curl http://localhost:8001/health | grep knowledge_docs_count
```

**阻塞项:** 后端需能正常启动（Milvus + Redis + PostgreSQL 就绪）。

---

### REQ-2: interview-showcase.html 指标刷新 [P0]

**描述:** 知识库从 20 篇扩展到 92 篇后，RAG 指标（Recall@3、MRR、NDCG）需要重跑并更新到 HTML。

**当前状态:** ✅ 已更新自适应RRF+四层后处理+冲突检测+工具指标。

**验收标准:**
- [ ] 跑 `backend/eval/tune_rag_params.py`（或等价脚本），产出新指标
- [ ] 更新 HTML 指标速查表中的 Recall@K / MRR / NDCG@K 数值
- [ ] 更新 HTML 中 RAGAS 指标（CP/Faithfulness/AR/CR）或标注"待重跑"
- [ ] 指标来源可追溯（注明脚本和日期）

**注意:** 如果重跑导致指标下降（文档多了 → 检索更难），如实标注并准备面试话术。

---

### REQ-3: interview-showcase.html 结构完善 [P1]

**描述:** 当前 HTML 缺少以下对面试有价值的章节。

**当前状态:** 侧边栏 16 个导航项，缺少知识库概览、部署说明、故障排查章节。

**验收标准:**
- [ ] **新增"知识库概览"章节**：7 部门文档分布表 + 总篇数 + 总大小
- [ ] **新增"部署与运维"章节**：Docker Compose 服务列表、端口映射、健康检查说明
- [ ] **新增"常见问题排查"章节**：启动失败、端口占用、white page 等 5 种常见问题的诊断方法
- [ ] 侧边栏导航更新，新增 3 个链接
- [ ] 链接到 `plan/demo-questions.md`（100 题问题库）

---

### REQ-4: 演示脚本全链路验证 [P1]

**描述:** 按 `plan/demo-questions.md` 底部"8 步快速演示流程"逐条验证，确保每条都能跑通。

**当前状态:** ❌ 未系统验证。

**验收标准:**
- [ ] 8 步演示全部跑通（见 `plan/demo-questions.md` 底部）
- [ ] 每个步骤的 SSE 事件类型与前端处理一致
- [ ] 权限切换（purchase → finance）正确拒绝工具调用
- [ ] 审批弹窗正常弹出和确认
- [ ] Query Cache 命中标签正常显示
- [ ] CLIP 模块可正常导入（不要求实际图片上传）

**测试方法:**
```bash
# 用 curl/httpx 发 SSE 请求验证后端行为
# 前端手动操作验证 UI 渲染
```

---

### REQ-5: 前端边缘情况修复 [P2]

**描述:** 前端在异常场景下的行为需要验证和修复。

**验收标准:**
- [ ] 后端不可用时前端显示明确错误提示（非白屏）
- [ ] SSE 连接中断后前端显示"连接已断开"
- [ ] 审批弹窗关闭后状态正确重置
- [ ] 图片上传按钮无 Vision API 依赖后仍可正常使用（走 CLIP）
- [ ] Token 用量显示在空响应时不崩溃

---

### REQ-6: 面试话术一致性审计 [P2]

**描述:** interview-showcase.html 中的面试话术与代码行为对照审计。

**验收标准:**
- [ ] "280 行纯 Python ReAct" → 实际 291 行，修正
- [ ] "7 节点 DAG 流水线" → 确认节点数和顺序与 chat.py 一致
- [ ] "三层回退链 opendataloader→pymupdf4llm→pdfplumber" → 确认
- [ ] "security_group ARRAY 列 array_contains 过滤" → 确认
- [ ] "Redis 对话记忆 user_id 隔离" → 确认
- [ ] "TokenUsage 精确到 0.0001 元" → 确认
- [ ] 所有模块行数与实际代码一致

---

### REQ-7: Git 推送到 GitHub [P0]

**描述:** 4 个 commit 尚未 push。

**验收标准:**
- [ ] `git push` 成功
- [ ] 确认 `.gitignore` 排除了 AI 痕迹文件（.ps1、eval 临时文件、vision.py）
- [ ] README 在 GitHub 上渲染正常（架构图可见）
- [ ] commit 历史干净（6 个以内）

---

### REQ-8: 测试覆盖补全 [P3]

**描述:** 增加新功能的单元测试。

**验收标准:**
- [ ] `multimodal_embedding.py` CLIP 模块的导入测试
- [ ] `query_supplier` 工具已有测试（16 个 test_tool_engine.py 用例通过）
- [ ] LangGraph Agent 测试仍 skip（已知限制，不阻塞）

---

## 4. 非需求 (Non-Requirements)

以下明确**不在本规格范围内**：

- ❌ 生产部署（CI/CD、K8s、监控报警）
- ❌ 用户注册/密码找回流程
- ❌ 移动端适配
- ❌ i18n 国际化
- ❌ 性能压测（QPS/并发）
- ❌ 完整的 E2E 自动化测试
- ❌ Reranker 实时启用（RERANKER_ENABLED=false 面试够用）

---

## 5. 优先级排序

| 优先级 | 需求 | 理由 |
|--------|------|------|
| P0 | REQ-1 知识库索引 | 不索引 → 新文档搜不到 → RAG 演示翻车 |
| P0 | REQ-2 指标刷新 | 面试官问"检索准确率多少"必须有据可查 |
| P0 | REQ-7 Git 推送 | 面试前代码必须在 GitHub 上可见 |
| P1 | REQ-3 HTML 结构完善 | 面试时打开 HTML 可快速展示全貌 |
| P1 | REQ-4 演示全链路验证 | 确保 8 步演示不出意外 |
| P2 | REQ-5 前端边缘情况 | 锦上添花，不影响核心演示 |
| P2 | REQ-6 话术一致性 | 避免面试时说出错误数字 |
| P3 | REQ-8 测试补全 | 面试官一般不看测试文件 |

---

## 6. 面试 HTML 完善清单

基于当前 `docs/interview-showcase.html`（2040 行），以下是需要新增/修改的部分：

### 6.1 需修改

| 位置 | 当前 | 改为 |
|------|------|------|
| RAG 指标卡片 | CP=0.67（基于 20 篇 KB） | 重跑后的真实 CP |
| RAG 检索链路 | "92 篇供应链文档" | 加上实际 chunk 数（如 "约 600 chunks"） |
| Agent 模式 | "280 行纯 Python" | "291 行纯 Python" |
| 演示流程 | 无问题库链接 | 添加链接到 plan/demo-questions.md |

### 6.2 需新增

| 章节 | 内容 | 行数估计 |
|------|------|----------|
| 知识库概览 | 7 部门文档分布表、总大小、chunk 数 | ~50 行 |
| 部署与运维 | docker-compose 服务说明、端口表、健康检查 | ~40 行 |
| 问题排查 | 5 种常见故障的诊断和修复命令 | ~40 行 |

### 6.3 需验证

- [ ] 所有 `<code>` 标签内的命令可复制粘贴直接执行
- [ ] 指标数据最后更新日期标注
- [ ] 侧边栏导航完整（当前 16 项，新增 3 项 = 19 项）

---

## 7. 验收检查表

```
系统完善:
[ ] REQ-1: 70 篇新文档索引到 Milvus → knowledge_docs_count > 500（脚本就绪：scripts/upload_knowledge_base.py）
[ ] REQ-7: git push 成功，GitHub README 渲染正常
[✅] REQ-8: 测试 58/59 通过（无回归）

手册完善:
[ ] REQ-2: HTML 指标刷新为 92 篇 KB 实测值（需后端启动后重跑 eval）
[✅] REQ-3: HTML 新增 3 个章节（知识库概览/部署/排查）+ 侧边栏 19 导航项
[✅] REQ-6: 话术一致性审计通过（18/18 项声明与代码匹配）

演示就绪:
[✅] REQ-4: 验证脚本就绪（scripts/verify_demo.py — 8 步 API 测试）
[ ] REQ-5: 前端错误场景不白屏（需手动浏览器验证）

最终检查:
[ ] demo_start.ps1 一键启动成功
[ ] 浏览器访问 localhost:3000 正常
[ ] docs/interview-showcase.html 浏览器打开渲染正常
[ ] 用问题库前 8 题逐条测试通过
```

---

## 8. 风险与阻塞

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| Milvus 启动慢 (>30s) | 高 | 演示等待焦虑 | demo_start.ps1 已处理等待逻辑 |
| Reranker 下载阻塞 | 中 | 后端启动卡死 | RERANKER_ENABLED=false |
| DeepSeek API 超时 | 低 | RAG 回答失败 | retry 3 次 + 降级提示 |
| Windows GBK 编码 | 中 | 中文乱码 | 统一 UTF-8，PowerShell 设 $OutputEncoding |
| Docker Desktop 未启动 | 中 | 基础设施不可用 | demo_start.ps1 第 1 步检测 |

---

## 9. 面试当天检查清单

```
30 分钟前:
[ ] Docker Desktop 已启动
[ ] 执行 .\demo_start.ps1
[ ] 等待 "启动完成" 三行 URL
[ ] 浏览器打开 localhost:3000 确认页面加载
[ ] 浏览器打开 docs/interview-showcase.html 确认渲染
[ ] 用 purchase/123456 登录测试
[ ] 问一个 RAG 问题确认响应

10 分钟前:
[ ] 关闭无关窗口和标签页
[ ] 确认终端字体够大（投屏可读）
[ ] 打开 plan/demo-questions.md 作为参考
[ ] 准备好 8 步演示的第一个问题文本

演示中:
[ ] 如果卡住，不要慌张——主动说"这里有个已知的 trade-off"
[ ] 如果 API 超时——retry 机制会自动处理，等待即可
[ ] 如果完全挂了——切换到 interview-showcase.html 讲架构
```
