<template>
  <div class="knowledge-page">
      <!-- 统计卡片 -->
      <el-row :gutter="16" class="stats-row">
        <el-col :span="8">
          <el-card shadow="hover">
            <div class="stat-card">
              <div class="icon-wrap icon-wrap--primary">
                <el-icon :size="28" color="#2563eb"><Document /></el-icon>
              </div>
              <div>
                <div class="stat-value">{{ knowledgeStore.stats.total_chunks }}</div>
                <div class="stat-label">知识切片数</div>
              </div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="hover">
            <div class="stat-card">
              <div class="icon-wrap icon-wrap--success">
                <el-icon :size="28" color="#10b981"><Cpu /></el-icon>
              </div>
              <div>
                <div class="stat-value">{{ knowledgeStore.stats.embedding_model }}</div>
                <div class="stat-label">嵌入模型</div>
              </div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="hover">
            <div class="stat-card">
              <div class="icon-wrap icon-wrap--warning">
                <el-icon :size="28" color="#f59e0b"><DataAnalysis /></el-icon>
              </div>
              <div>
                <div class="stat-value">{{ knowledgeStore.stats.embedding_dimension }}</div>
                <div class="stat-label">向量维度</div>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 上传区域 -->
      <el-card class="upload-card">
        <template #header>
          <div class="card-header">
            <span><el-icon><Upload /></el-icon> 上传文档</span>
          </div>
        </template>

        <!-- 权限组选择 -->
        <div class="permission-section">
          <div class="permission-label">可见部门（勾选可访问此文档的部门）：</div>
          <el-checkbox-group v-model="selectedGroups" class="permission-group">
            <el-checkbox label="admin" border>管理员</el-checkbox>
            <el-checkbox label="purchase" border>采购部</el-checkbox>
            <el-checkbox label="warehouse" border>仓库部</el-checkbox>
            <el-checkbox label="quality" border>质量部</el-checkbox>
            <el-checkbox label="production" border>生产部</el-checkbox>
            <el-checkbox label="finance" border>财务部</el-checkbox>
            <el-checkbox label="logistics" border>物流部</el-checkbox>
          </el-checkbox-group>
        </div>

        <el-upload
          ref="uploadRef"
          :auto-upload="false"
          :limit="5"
          :on-change="handleFileChange"
          accept=".pdf,.txt,.md,.markdown,.docx,.doc"
          drag
          multiple
        >
          <el-icon :size="48"><UploadFilled /></el-icon>
          <div class="el-upload__text">
            拖拽文件到此处，或<em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              支持 PDF、TXT、Markdown、DOCX 格式，单文件不超过 50MB
            </div>
          </template>
        </el-upload>

        <div class="upload-actions">
          <el-button
            type="primary"
            :loading="knowledgeStore.uploading"
            @click="handleUpload"
          >
            <el-icon><Upload /></el-icon>
            开始上传并索引
          </el-button>
          <el-button
            type="success"
            :loading="ingesting"
            @click="handleIngest"
            style="margin-left: 12px;"
          >
            <el-icon><Download /></el-icon>
            一键导入大厂供应链样本库
          </el-button>
        </div>
      </el-card>

      <!-- 文档列表 -->
      <el-card class="doc-list-card">
        <template #header>
          <div class="card-header">
            <span><el-icon><Folder /></el-icon> 文档列表</span>
            <el-button text type="primary" @click="refreshList">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>
        </template>

        <el-table
          :data="knowledgeStore.documents"
          v-loading="knowledgeStore.loading"
          empty-text="暂无文档，请上传"
        >
          <el-table-column prop="doc_id" label="文档ID" width="180" />
          <el-table-column prop="filename" label="文件名" />
          <el-table-column prop="chunk_count" label="切片数" width="100" />
          <el-table-column label="可见部门" min-width="200">
            <template #default="{ row }">
              <el-tag
                v-for="group in (row.security_group || [])"
                :key="group"
                size="small"
                :type="group === 'admin' ? 'danger' : ''"
                style="margin-right: 4px;"
              >
                {{ groupLabels[group] || group }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag
                :type="row.status === 'indexed' ? 'success' : 'warning'"
                size="small"
              >
                {{ row.status === 'indexed' ? '已索引' : '处理中' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-popconfirm
                title="确定删除该文档？"
                @confirm="handleDelete(row.doc_id)"
              >
                <template #reference>
                  <el-button type="danger" text size="small">
                    <el-icon><Delete /></el-icon> 删除
                  </el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useKnowledgeStore } from '@/stores/knowledge'
import request from '@/api/request'
const knowledgeStore = useKnowledgeStore()
const uploadRef = ref(null)
const pendingFiles = ref([])
const selectedGroups = ref(['admin'])
const ingesting = ref(false)

// 部门标签映射
const groupLabels = {
  admin: '管理员',
  purchase: '采购部',
  warehouse: '仓库部',
  quality: '质量部',
  production: '生产部',
  finance: '财务部',
  logistics: '物流部',
}

onMounted(() => {
  knowledgeStore.fetchDocuments()
  knowledgeStore.fetchStats()
})

function handleFileChange(file) {
  pendingFiles.value.push(file)
}

async function handleUpload() {
  if (!selectedGroups.value.length) {
    ElMessage.warning('请至少选择一个可见部门')
    return
  }
  const securityGroup = selectedGroups.value.join(',')
  for (const file of pendingFiles.value) {
    try {
      const res = await knowledgeStore.upload(file.raw, securityGroup)
      ElMessage.success(`文档 ${res.filename} 上传成功，已索引 ${res.chunk_count} 个切片`)
    } catch (err) {
      ElMessage.error(`上传失败: ${err.message}`)
    }
  }
  pendingFiles.value = []
  uploadRef.value?.clearFiles()
}

async function handleIngest() {
  ingesting.value = true
  try {
    const res = await request.post('/api/v1/knowledge/ingest')
    ElMessage.success(res.message || `导入成功: ${res.total_chunks} 个 chunk`)
    knowledgeStore.fetchDocuments()
    knowledgeStore.fetchStats()
  } catch (err) {
    ElMessage.error(`导入失败: ${err.message}`)
  } finally {
    ingesting.value = false
  }
}

async function handleDelete(docId) {
  try {
    await knowledgeStore.remove(docId)
    ElMessage.success('文档已删除')
  } catch (err) {
    ElMessage.error(`删除失败: ${err.message}`)
  }
}

function refreshList() {
  knowledgeStore.fetchDocuments()
  knowledgeStore.fetchStats()
}
</script>

<style scoped>
.knowledge-page {
  max-width: 1000px;
  margin: 0 auto;
}

.stats-row {
  margin-bottom: var(--space-5);
}

.stat-card {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.stat-card .icon-wrap {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.icon-wrap--primary { background: rgba(37, 99, 235, 0.08); }
.icon-wrap--success { background: rgba(16, 185, 129, 0.08); }
.icon-wrap--warning { background: rgba(245, 158, 11, 0.08); }

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.stat-label {
  font-size: 12px;
  color: var(--color-text-placeholder);
  margin-top: 2px;
}

.upload-card {
  margin-bottom: var(--space-5);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header span {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 600;
  font-size: 15px;
}

.permission-section {
  margin-bottom: var(--space-4);
}

.permission-label {
  font-size: 14px;
  color: var(--color-text-secondary);
  margin-bottom: var(--space-2);
}

.permission-group {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.upload-actions {
  margin-top: var(--space-4);
  text-align: right;
}

.doc-list-card {
  margin-bottom: var(--space-5);
}

@media (max-width: 767px) { .knowledge-page { padding: 0; } .knowledge-page .el-upload-dragger { width: 100%; padding: 20px 10px; } .knowledge-page .el-table { font-size: 12px; } .knowledge-page .el-table .cell { padding: 6px 4px; } }
</style>
