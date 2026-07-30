# Supply Chain QA 部署指南

提供 3 种部署方案，按便捷程度排序：

| 方案 | 适合场景 | 耗时 | 费用 |
|---|---|---|---|
| A. ngrok 隧道 | 临时演示、面试 | 2 分钟 | 免费 |
| B. Docker Compose (VPS) | 长期运行 | 15 分钟 | ~50元/月 |
| C. 云平台托管 | 免运维 | 30 分钟 | 按量付费 |

---

## 方案 A：ngrok 隧道（最快，适合面试演示）

从本地电脑直接暴露到公网。

### 步骤

```powershell
# 1. 安装 ngrok（一次性）
cd scripts
.\install_ngrok.ps1

# 2. 启动本地服务
.\demo_start.ps1

# 3. 创建公网隧道
ngrok http http://localhost:3000
```

手机访问 ngrok 输出的公网 URL（如 `https://xxxx.ngrok-free.app`）即可。

> **注意**：免费版 ngrok 有带宽限制，且 URL 每次重启会变化。如需固定域名，可以用 ngrok 免费静态域名（注册后获取）。

---

## 方案 B：Docker Compose 部署到 VPS

推荐配置：**4 核 8GB 内存**（阿里云 ECS / 腾讯云 CVM / 华为云 ECS）

### 1. 购买服务器

- 阿里云：[ECS 计算型 c7](https://ecs.console.aliyun.com)（2C4G 起步，约 ¥68/月）
- 腾讯云：[轻量应用服务器](https://cloud.tencent.com)（4C8G，约 ¥90/月）
- 系统选择：**Ubuntu 22.04 LTS**

### 2. 连接到服务器

```bash
ssh root@你的服务器IP
```

### 3. 克隆项目并部署

```bash
# 安装 git
apt-get update && apt-get install -y git

# 克隆项目
git clone https://github.com/你的用户名/supply-chain-qa.git
cd supply-chain-qa

# 配置 API Key
cp deploy/.env.production .env
vim .env  # 填入 DEEPSEEK_API_KEY=sk-xxx

# 一键部署
chmod +x deploy/deploy.sh
./deploy/deploy.sh
```

### 4. 开放端口

在云服务商控制台的「安全组」中开放：
- **80**（HTTP）
- **443**（HTTPS，可选）

### 5. 访问

浏览器打开 `http://你的服务器IP`

### 6. （可选）配置域名 + HTTPS

```bash
# 安装 certbot
apt-get install -y certbot python3-certbot-nginx

# 先配置 DNS 将域名指向服务器 IP，然后：
certbot --nginx -d your-domain.com

# 自动续期
certbot renew --dry-run
```

---

## 方案 C：分离部署（免运维）

将前端和后端分开部署到云平台。

### 前端 → Vercel（免费）

1. 将项目推送到 GitHub
2. 打开 [vercel.com](https://vercel.com)，Import 你的仓库
3. 设置：
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
   - **Environment Variable**: `VITE_API_URL` = 后端 API 地址
4. 部署

### 后端 → Railway（按量付费）

1. 打开 [railway.app](https://railway.app)，New Project → Deploy from GitHub
2. 选择你的仓库
3. 设置：
   - **Root Directory**: `backend`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. 添加 Environment Variables：
   - `DEEPSEEK_API_KEY` = sk-xxx
   - `POSTGRES_HOST` / `REDIS_HOST` / `MILVUS_HOST` = Railway 提供的服务地址
5. Railway 会自动检测 `requirements.txt` 并安装

> 问题：Railway 不提供 Milvus，需要额外使用 [Zilliz Cloud](https://cloud.zilliz.com)（免费额度 1GB）

---

## 配置 HTTPS

### 使用 Caddy（推荐，最简单）

在服务器上安装 Caddy 替代 Nginx：

```bash
# 安装 Caddy
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

# 创建 Caddyfile
cat > /etc/caddy/Caddyfile << 'EOF'
your-domain.com {
    reverse_proxy /api/* backend:8001
    reverse_proxy /* frontend:80
}
EOF

systemctl restart caddy
```

Caddy 自动申请和续期 Let's Encrypt 证书，零配置。

---

## 环境变量说明

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | **是** | - | DeepSeek API 密钥 |
| `DB_PASSWORD` | 建议改 | scqa123 | PostgreSQL 密码 |
| `NEO4J_PASSWORD` | 建议改 | scqa123 | Neo4j 密码 |
| `LLM_PROVIDER` | 否 | deepseek | LLM 提供商 |
| `RERANKER_ENABLED` | 否 | false | 是否启用重排序模型（需 2GB+ 内存） |
| `RERANKER_DEVICE` | 否 | cpu | Reranker 推理设备：`cpu`（默认）或 `cuda`（GPU，快 5-10 倍） |
| `CORS_ORIGINS` | 否 | * | 允许的跨域来源 |

---

## Langfuse 可观测性（本地自托管）

项目内置 Langfuse 本地部署，`docker compose up -d` 后自动启动。

### 访问地址

- **Langfuse UI**: http://localhost:3100
- 首次访问需注册管理员账号（本地实例，数据仅存在于 Docker volume）

### 获取 API Key 并配置后端

1. 登录 Langfuse UI → Settings → API Keys → Create API Key
2. 将获取的 Public Key 和 Secret Key 填入 `backend/.env`：
   ```env
   LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
   LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx
   LANGFUSE_HOST=http://localhost:3100
   ```
3. 重启后端服务，日志中出现 `[Langfuse] 客户端初始化成功` 即表示连接成功

### 查看 Trace

每次聊天请求会在 SSE 流中返回 `langfuse_url`，点击可直接跳转到对应 Trace 页面，查看：
- 意图路由决策
- RAG 检索结果
- 工具调用详情
- LLM Token 消耗和耗时

---

## 常见问题

**Q: 内存不够怎么办？**
A: 关闭 Reranker（`RERANKER_ENABLED=false`），最低 4GB 可运行。

**Q: Milvus 启动失败？**
A: 确保服务器有足够内存（Milvus standalone 需要 2GB+），检查 `docker compose logs milvus`。

**Q: Embedding 模型下载慢？**
A: 首次启动会从 HuggingFace 下载 `BAAI/bge-small-zh-v1.5`（约 100MB），可提前设置镜像：
```bash
export HF_ENDPOINT=https://hf-mirror.com
```

**Q: 如何更新部署？**
```bash
git pull
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml up -d
```

