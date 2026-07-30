<template>
  <div class="dashboard">
    <h2 class="page-title">系统概览</h2>

    <!-- 服务状态 -->
    <el-row :gutter="16" class="status-row">
      <el-col :span="6" v-for="svc in services" :key="svc.name">
        <el-card shadow="hover" :class="['status-card', { connected: svc.connected }]">
          <div class="status-header">
            <span class="status-name">{{ svc.label }}</span>
            <el-tag :type="svc.connected ? 'success' : 'danger'" size="small">
              {{ svc.connected ? '运行中' : '离线' }}
            </el-tag>
          </div>
          <div class="status-detail">{{ svc.detail }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="知识库文档数" :value="stats.docCount" suffix="篇" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="向量切片数" :value="stats.chunkCount" suffix="个" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="Agent 类型" :value="4" suffix="个" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="单元测试" :value="728" suffix="通过" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 技术栈 & 快速导航 -->
    <el-row :gutter="16" class="bottom-row">
      <el-col :span="12">
        <el-card shadow="hover" class="tech-card">
          <template #header>
            <span>技术栈</span>
          </template>
          <div class="tech-grid">
            <div class="tech-item" v-for="tech in techStack" :key="tech.name">
              <span class="tech-name">{{ tech.name }}</span>
              <el-tag size="small" type="info">{{ tech.role }}</el-tag>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="hover" class="quick-card">
          <template #header>
            <span>快速导航</span>
          </template>
          <div class="quick-links">
            <el-button type="primary" @click="$router.push('/chat')">
              <el-icon><ChatDotRound /></el-icon> 智能对话
            </el-button>
            <el-button type="success" @click="$router.push('/knowledge')">
              <el-icon><Folder /></el-icon> 知识库管理
            </el-button>
            <el-button type="warning" @click="$router.push('/tools')">
              <el-icon><SetUp /></el-icon> 工具管理
            </el-button>
            <el-button type="info" @click="$router.push('/evaluate')">
              <el-icon><DataAnalysis /></el-icon> RAG 评估
            </el-button>
          </div>
          <div class="quick-features">
            <h4>核心能力</h4>
            <ul>
              <li>三级意图路由（规则 → 语义 → LLM）</li>
              <li>混合检索 + RRF 融合 + BGE-Reranker</li>
              <li>Self-RAG 检索过滤 + 生成反思</li>
              <li>两层缓存（MD5 精确 + Embedding 语义）</li>
              <li>Text-to-SQL 结构化数据查询</li>
              <li>Langfuse 全链路可观测</li>
              <li>RBAC 行级权限控制</li>
              <li>SSE 流式 + 操作审批闭环</li>
            </ul>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ChatDotRound, Folder, SetUp, DataAnalysis } from '@element-plus/icons-vue'
import request from '@/api/request'

const services = ref([
  { name: 'milvus', label: 'Milvus', connected: false, detail: '向量数据库' },
  { name: 'redis', label: 'Redis', connected: false, detail: '缓存 & 会话' },
  { name: 'postgres', label: 'PostgreSQL', connected: false, detail: '元数据存储' },
  { name: 'neo4j', label: 'Neo4j', connected: false, detail: '知识图谱' },
])

const stats = reactive({
  docCount: 0,
  chunkCount: 0,
})

const techStack = [
  { name: 'FastAPI', role: '后端框架' },
  { name: 'LangGraph', role: 'Agent 编排' },
  { name: 'LangChain', role: 'LLM 调用' },
  { name: 'Milvus', role: '向量检索' },
  { name: 'Redis', role: '缓存/会话' },
  { name: 'PostgreSQL', role: '元数据' },
  { name: 'Neo4j', role: '知识图谱' },
  { name: 'Vue3', role: '前端' },
  { name: 'llama.cpp', role: '本地 LLM' },
  { name: 'BGE', role: 'Embedding' },
]

async function fetchHealth() {
  try {
    const res = await request.get('/health')
    if (res.services) {
      for (const svc of services.value) {
        svc.connected = res.services[svc.name]?.connected ?? false
      }
    }
    stats.docCount = res.knowledge_docs_count ?? 0
    stats.chunkCount = res.knowledge_chunks_count ?? 0
  } catch (e) {
    console.warn('Health check failed:', e)
  }
}

onMounted(() => {
  fetchHealth()
})
</script>

<style scoped>
.dashboard {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--space-6);
}

.page-title {
  font-family: var(--font-heading);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  margin-bottom: var(--space-6);
  animation: fadeInUp 0.4s ease both;
}

.status-row {
  margin-bottom: var(--space-5);
  animation: fadeInUp 0.4s ease 0.1s both;
}

.stats-row {
  margin-bottom: var(--space-5);
  animation: fadeInUp 0.4s ease 0.2s both;
}

.bottom-row {
  margin-bottom: var(--space-5);
  animation: fadeInUp 0.4s ease 0.3s both;
}

.status-card {
  text-align: left;
  padding-left: var(--space-4);
  border-left: 3px solid var(--color-text-meta);
}

.status-card.connected {
  border-left-color: var(--color-success);
}

.status-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-2);
}

.status-name {
  font-weight: 600;
  font-size: 16px;
  color: var(--color-text-primary);
}

.status-detail {
  color: var(--color-text-placeholder);
  font-size: 13px;
}

.stat-card {
  text-align: center;
}

.tech-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.tech-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  transition: background var(--transition-base);
}

.tech-item:hover {
  background: var(--color-bg-hover);
}

.tech-name {
  font-weight: 500;
  color: var(--color-text-body);
}

.quick-links {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-5);
}

.quick-links .el-button {
  height: 44px;
  font-weight: 500;
  transition: all var(--transition-base);
}

.quick-links .el-button:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-raised);
}

.quick-features h4 {
  margin: 0 0 var(--space-2) 0;
  color: var(--color-text-secondary);
  font-family: var(--font-heading);
  font-weight: 600;
}

.quick-features ul {
  margin: 0;
  padding-left: 20px;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.8;
}

/* ===== Mobile Responsive ===== */
@media (max-width: 767px) {
  .dashboard {
    padding: 12px;
  }

  .page-title {
    font-size: 18px;
    margin-bottom: var(--space-4);
  }

  .status-row .el-col,
  .stats-row .el-col {
    flex: 0 0 50%;
    max-width: 50%;
    margin-bottom: 8px;
  }

  .bottom-row .el-col {
    flex: 0 0 100%;
    max-width: 100%;
    margin-bottom: 12px;
  }

  .status-card {
    padding: 10px;
  }

  .status-name {
    font-size: 13px;
  }

  .status-detail {
    font-size: 11px;
  }

  .stat-card {
    padding: 10px;
  }

  .quick-links {
    flex-direction: column;
  }

  .quick-links .el-button {
    width: 100%;
    justify-content: flex-start;
  }

  .tech-grid {
    grid-template-columns: 1fr;
  }

  .quick-features ul {
    font-size: 13px;
    line-height: 1.6;
  }
}
</style>
