# Supply Chain QA — 前端

Vue 3 + Vite + Element Plus + Pinia 单页应用（JavaScript，非 TypeScript），是供应链 QA 系统的操作界面。

## 运行前提

- Node 18+
- 后端 API 已在 `http://localhost:8001` 运行（`cd backend; venv\Scripts\python.exe -m uvicorn app.main:app --port 8001`）
- 知识库/工具等页面的数据依赖 Docker 基础设施（Milvus / Redis / PostgreSQL / Neo4j），见根目录 `docker-compose.yml`

## 常用命令（Windows PowerShell，frontend 目录下执行）

```powershell
npm install        # 安装依赖
npm run dev        # 开发服务器（Vite）
npm run test:unit  # 单元测试（Vitest）
npm run test:e2e   # 端到端测试（Playwright，需前后端均在运行）
npm run build      # 生产构建（输出 dist/）
```

## 目录结构

```
src/
├── views/      # 6 个页面：Login / Dashboard / Chat / Knowledge / Tools / Evaluate
├── stores/     # Pinia store：auth / chat / knowledge / tools / evaluate
├── api/        # HTTP 封装：request.js 统一拦截（JWT 注入、错误处理），其余按业务域拆分
├── router/     # 页面路由（index.js）
├── components/ # 复用组件
└── styles/     # 全局样式
```

## 约定

- UI 组件一律使用 Element Plus，不引入其他 UI 库
- 状态管理一律使用 Pinia store，不用 Vuex 或 composables 替代
- 所有 HTTP 请求经 `src/api/request.js` 统一封装；聊天接口为 SSE 流式（`src/api/chat.js`）
- 登录获得 JWT 后随请求携带；知识库列表等接口按角色行级过滤，未登录/Token 过期会返回 401（页面显示为空时先检查后端是否在运行、是否需要重新登录）

更多架构约束与踩坑记录见根目录 [AGENTS.md](../AGENTS.md)。
