# SmartQA 面试可用性修复 - OpenSpec

> 版本: 1.0
> 日期: 2026-05-20
> 目标: 让项目从“功能很多”提升到“现场能稳演示、问题能自证、文档不露怯”

---

## 1. 背景

本轮复核基于 2026-05-20 的本地仓库与实际命令验证，结论如下：

- 前端可正常构建：`cd frontend && npm run build`
- 后端默认 shell 环境下直接运行 `pytest` 会因未进入项目 `venv` 而缺依赖
- 使用项目自带虚拟环境运行：
  - `backend\\venv\\Scripts\\python.exe -m pytest tests -q -k "not integration"` 通过
  - `backend\\venv\\Scripts\\python.exe -m pytest tests -q` 失败 2 项
- 失败点集中在最能打动面试官的能力：Domain Agent 与 Orchestrator 的真实 LLM 联调

因此当前项目已经“能讲、能展示一部分”，但还不算“面试现场稳妥可用”。

---

## 2. 当前基线

### 已确认通过

- 前端生产构建通过
- 后端非集成测试通过：`81 passed, 1 skipped, 6 deselected`
- 仓库内已有评估产物、面试文档、演示页面、启动脚本
- 项目结构完整，具备较强展示价值

### 已确认风险

- 全量测试在真实 `venv` 下结果为：`2 failed, 82 passed, 4 skipped`
- 失败原因为外部 LLM 连接错误，导致 Agent/Orchestrator 关键链路不可复现
- 默认管理员账号与密码硬编码在启动逻辑和文档中
- 注册接口注释与实际角色分配不一致，RBAC 叙事会被追问
- 前端构建存在超大 chunk 警告，性能叙事偏弱

---

## 3. 问题定义

### P0: Agent / Orchestrator 缺少“面试安全模式”

当前 `test_cross_domain.py` 中的真实链路测试依赖 LLM 可连通。一旦 API key、网络、供应商服务异常，`DomainAgent.run()` 会直接返回 `"Agent 执行出错: Connection error."`，`Orchestrator.run()` 则返回 `execution=None`。

这意味着：

- 最有亮点的多 Agent 能力无法稳定自证
- 现场 demo 极易因外部依赖翻车
- 测试虽存在，但不能作为“随时可复现”的背书

### P0: 测试入口不够自解释

仓库当前真实可复现路径依赖 `backend/venv`，但项目主文档仍容易让人默认用系统 Python 直接运行测试。

这意味着：

- 面试官或编码 Agent 很容易得到和文档不一致的结果
- “81 passed” 结论虽然成立，但复现路径不够清晰

### P1: RBAC 叙事存在一致性缺口

注册接口注释写“默认角色为 employee”，但代码实际赋予 `purchase` 角色。面试时一旦被问“普通用户默认权限是什么”，会出现代码与讲解不一致的问题。

### P1: Demo 账号策略过于硬编码

当前默认账号在启动时自动写入数据库，且文档直接公开固定密码。对于 demo 虽然方便，但在面试语境下容易被质疑安全意识与环境隔离策略。

---

## 4. 修复目标

### REQ-1: 给多 Agent 主链路增加可复现策略 [P0]

目标：

- 让 Domain Agent / Orchestrator 在“无外网、无 API key、服务商波动”场景下也有稳定演示路径

可选实现方向，优先推荐 A：

#### 方案 A（推荐）：增加 Demo Fallback / Mock Mode

- 增加显式配置，如 `DEMO_MODE=true` 或 `LLM_FALLBACK_MODE=mock`
- 在 LLM 不可用时：
  - DomainAgent 返回可解释的降级结果，而非裸 `"Connection error"`
  - Orchestrator 返回确定性的本地计划模板，保证 `execution` 结构完整
- 在 SSE/前端中明确标识“当前为离线演示模式”

优点：

- 最适合面试现场
- 不依赖外网
- 能保住系统架构讲解节奏

缺点：

- 需要补充降级说明，避免被误解为真实生产链路

#### 方案 B：严格跳过真实 LLM 集成测试

- 检测 API key、网络连通性或显式 `RUN_LIVE_LLM_TESTS=true`
- 条件不满足时自动 skip 相关测试，而不是失败

优点：

- 实现简单
- 测试结果更真实

缺点：

- 只能解决“测试背书”问题，不能解决“现场 demo 翻车”问题

#### 方案 C：本地 Ollama 兜底

- 当 DeepSeek/外部 LLM 不可用时自动切换本地 `ollama`

优点：

- 仍保留真实 Agent 推理链路

缺点：

- 对机器环境要求更高
- 面试当天依然有模型冷启动风险

验收标准：

- [ ] 在无外部 LLM 可用时，Agent/Orchestrator 不再返回裸连接错误
- [ ] 至少有一条“面试稳定路径”可跑通
- [ ] 集成测试对外部依赖的要求被明确表达为 skip 或 fallback，而不是不可解释失败

### REQ-2: 明确唯一的测试绿通命令 [P0]

目标：

- 让任何人按文档执行都能得到一致结果

要求：

- README 与面试文档统一使用项目 `venv` 命令
- 区分三类验证：
  - 基础单测
  - 依赖 Docker/数据库的集成测试
  - 依赖真实 LLM 的 live 测试

建议标准命令：

```powershell
cd backend
venv\Scripts\python.exe -m pytest tests -q -k "not integration"
```

如保留 live 测试，再补一条：

```powershell
cd backend
venv\Scripts\python.exe -m pytest tests -q
```

并明确说明附加前提。

验收标准：

- [ ] README 中有一条当前环境可直接复现的绿通命令
- [ ] 面试文档中的测试结论与真实命令输出一致
- [ ] 不再混淆“可本地直接跑”和“需外部条件”的测试

### REQ-3: 修复 RBAC 注释与默认角色不一致 [P1]

目标：

- 保证权限叙事、接口注释、前端体验、面试话术一致

二选一：

- 保留当前实现：把注释改成“默认采购角色，仅供 demo”
- 或修正实现：新增真正的低权限默认角色

验收标准：

- [ ] `register` 接口注释与代码一致
- [ ] README / 面试讲稿中对默认角色的描述一致

### REQ-4: 收敛默认账号策略 [P1]

目标：

- 降低“硬编码管理员密码”带来的面试风险

建议：

- 将默认账号种子逻辑收敛到显式 `DEMO_SEED_USERS=true`
- 在文档中说明这些账号仅用于演示环境
- 避免在一般启动流程中默认写入固定高权限账号

验收标准：

- [ ] Demo 账号创建逻辑可控开关化
- [ ] 文档明确声明为演示用途
- [ ] 非 demo 场景不会默认落管理员账号

### REQ-5: 收敛前端打包警告 [P2]

目标：

- 让前端性能叙事更完整

建议：

- 为大页面做懒加载
- 拆分 Element Plus 或重资源模块
- 至少把 >500kB 的 warning 降到更可解释的范围

验收标准：

- [ ] `npm run build` 仍通过
- [ ] 主包体积下降，或对 warning 有清晰说明

---

## 5. 非需求

本轮不要求：

- 重做整体架构
- 替换技术栈
- 上 CI/CD
- 做完整生产级安全改造
- 追求所有外部依赖场景下 100% 自动化通过

---

## 6. 建议修改文件

- `backend/app/agents/domain_agent.py`
- `backend/app/agents/orchestrator.py`
- `backend/tests/test_cross_domain.py`
- `backend/app/api/auth.py`
- `backend/app/main.py`
- `backend/app/config.py`
- `README.md`
- `docs/INTERVIEW_GUIDE.md`
- 如需同步展示口径：`docs/interview-showcase.html`

---

## 7. 建议执行顺序

1. 先做 REQ-1：修复 Agent/Orchestrator 的面试安全路径
2. 再做 REQ-2：统一测试绿通命令与文档
3. 再做 REQ-3/REQ-4：修复权限与 demo 账号叙事
4. 最后做 REQ-5：优化前端打包体积

---

## 8. 给编码 Agent 的一句话指令

请按 `plan/interview-readiness-round3-openspec.md` 执行，优先把多 Agent 与 Orchestrator 的外部 LLM 依赖改造成“可稳定演示或可明确跳过”的路径，并统一 README / 面试文档中的测试命令为项目 `venv` 可直接复现的版本；同时修复注册默认角色说明与 demo 账号种子策略，最后视时间处理前端大包 warning。
