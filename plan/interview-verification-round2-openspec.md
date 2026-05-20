# SmartQA 面试可用性二轮修复 - OpenSpec

> 版本: 1.0
> 日期: 2026-05-20
> 目标: 解决“测试无法现场自证”与“文档背书强于实际可复现结果”的剩余问题

---

## 1. 背景

上一轮修复后，项目的文档编码、默认账号、前端快捷登录、`docker-compose` 健康检查已经基本收口，但仍存在影响面试可信度的关键问题：

1. 在 Windows 环境中直接运行 `pytest -q` 仍会因为 `backend/pytest.ini` 解码失败而中断。
2. 即使绕过 `pytest.ini`，当前环境中测试收集仍会因依赖缺失而失败。
3. README 与面试指南仍使用了“84 passed, 4 skipped”这类强结论，但当前仓库尚未提供一条可稳定复现该结果的标准路径。

本轮目标不是继续美化文档，而是让“测试说明”与“仓库真实可验证状态”一致。

---

## 2. 已确认事实

以下内容已经确认，可作为本轮修复的前提，不需要重复返工：

- 默认账号已统一为：
  - `admin / admin123`
  - `purchase / purchase123`
  - `warehouse / warehouse123`
- 前端登录页快捷账号已与后端种子数据一致
- `docs/interview-showcase.html` 的演示账号口径已基本一致
- `docker-compose.yml` 的 backend healthcheck 已切换到真实存在的 `/health`
- `backend/requirements.txt` 已声明：
  - `neo4j>=5.0.0`
  - `langchain-core>=0.3.0`
  - `pytest>=8.0.0`
  - `pytest-asyncio>=0.23.0`

---

## 3. 问题定义

### P0: `pytest.ini` 在 Windows 下不可直接读取

当前复检中，运行：

```powershell
cd backend
pytest -q
```

实际结果为 `UnicodeDecodeError: 'gbk' codec can't decode ...`。

这说明：

- 现有 `backend/pytest.ini` 仍不适合作为 Windows 默认编码环境下的稳健入口
- 项目的“标准测试命令”仍无法直接工作
- 任何“测试已通过”的文档背书都缺少现场可复现性

### P0: 测试背书强于当前可复现事实

当前文档中的“84 passed, 4 skipped”属于强结论，但复检时：

- `pytest -q` 无法启动
- `pytest -q -c NUL backend\tests` 仍在收集阶段暴露依赖问题

因此，本轮必须做到二选一：

1. 真正修通一条可复现的测试路径，然后保留真实结果
2. 如果无法完全修通，就把文档改成保守且真实的表述

不允许继续保留“默认就能跑出 84 passed, 4 skipped”的印象。

---

## 4. 修复要求

### REQ-1: 修复 `pytest.ini` 的 Windows 兼容性

目标：

- 让 `backend` 目录下直接执行 `pytest -q` 不再因配置文件解码失败而中断

建议做法：

- 检查 `backend/pytest.ini` 是否包含会触发 Windows/GBK 读取问题的非 ASCII 内容
- 优先将注释、marker 描述、warning 说明改成 ASCII/英文，避免编码歧义
- 保持 pytest 配置语义不变，仅修复可读性和兼容性问题

验收标准：

- [ ] 在 Windows 环境下，`cd backend && pytest -q` 能进入正常收集或执行阶段
- [ ] 不再出现 `UnicodeDecodeError`

### REQ-2: 给出一条真实可复现的测试运行路径

目标：

- 让仓库拥有一条开发者按文档即可复现的测试命令

要求：

- 检查 `backend/requirements.txt` 与测试实际依赖是否一致
- 如果还缺运行测试所需的包，补齐说明或补齐依赖声明
- 明确区分：
  - 单元测试
  - 需要真实服务的集成测试
  - 需要 LLM/API key 的测试

验收标准：

- [ ] 至少有一条“当前环境可执行”的测试命令写入文档
- [ ] 文档明确哪些测试默认可跑，哪些测试需要额外服务
- [ ] 结果表述与实际运行输出一致

### REQ-3: 下调或更新测试背书文案

需要检查并修订：

- `README.md`
- `docs/INTERVIEW_GUIDE.md`
- 如有相关位置，也包括 `docs/interview-showcase.html`

修订原则：

- 如果你成功跑出稳定结果，就写真实结果
- 如果不能稳定复现，就改成事实型表述，例如：
  - “仓库包含若干单元测试与集成测试”
  - “默认环境可运行 X 类测试”
  - “Neo4j / LangGraph 集成测试需额外服务或依赖”

禁止事项：

- 不要保留“84 passed, 4 skipped”这类未经本轮复跑证实的定值结论
- 不要使用模糊但有误导性的说法，例如“测试已全部完成”

验收标准：

- [ ] 所有测试状态文案都能被当前仓库命令验证
- [ ] 文档不再暗示“开箱即得的全通过结果”，除非本轮确实验证成功

### REQ-4: 输出残余风险

如果本轮后仍存在以下情况，必须明确写在交付摘要里：

- 某些测试仍依赖外部服务
- 某些测试仍依赖本地安装额外包
- 某些功能只适合讲解，不适合现场点开演示

---

## 5. 建议修改文件

- `backend/pytest.ini`
- `backend/requirements.txt`（仅在确有必要时）
- `README.md`
- `docs/INTERVIEW_GUIDE.md`
- `docs/interview-showcase.html`（如果其中仍出现测试强背书）

---

## 6. 验证步骤

编码 Agent 完成后，请至少执行并记录以下验证：

```powershell
cd backend
pytest -q
```

如需区分默认可跑测试与集成测试，也请补充一条更细的验证命令，并说明它验证的范围。

如果因为当前环境未安装依赖而无法得到最终结果，也必须把失败原因写清楚，不要只写“需用户自行处理”。

---

## 7. 交付要求

最终回复必须包含：

1. 修改了哪些文件
2. `pytest.ini` 做了什么兼容性修复
3. 文档里的测试状态改成了什么说法
4. 实际运行了哪些命令
5. 跑出来的真实结果是什么
6. 还剩哪些风险没有解决

---

## 8. 给编码 Agent 的一句话指令

请按 `plan/interview-verification-round2-openspec.md` 执行，优先修复 `backend/pytest.ini` 的 Windows 兼容性，让 `pytest -q` 不再因解码问题中断；然后基于实际复跑结果修订 README、面试指南和展示手册中的测试说明，凡是不能由当前仓库命令稳定复现的“通过数/跳过数”结论，一律改为保守且真实的表述，并在最后输出实际验证命令、真实结果和剩余风险。
