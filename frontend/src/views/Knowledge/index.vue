<template>
  <div class="knowledge-page">
      <!-- 统计卡片 -->
      <el-row :gutter="16" class="stats-row">
        <el-col :span="8">
          <el-card shadow="hover">
            <div class="stat-card">
              <el-icon :size="32" color="#409eff"><Document /></el-icon>
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
              <el-icon :size="32" color="#67c23a"><Cpu /></el-icon>
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
              <el-icon :size="32" color="#e6a23c"><DataAnalysis /></el-icon>
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

        <el-upload
          ref="uploadRef"
          :auto-upload="false"
          :limit="5"
          :on-change="handleFileChange"
          accept=".pdf,.txt,.md,.markdown"
          drag
          multiple
        >
          <el-icon :size="48"><UploadFilled /></el-icon>
          <div class="el-upload__text">
            拖拽文件到此处，或<em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              支持 PDF、TXT、Markdown 格式，单文件不超过 50MB
            </div>
          </template>
        </el-upload>

        <div class="upload-actions">
          <el-button
            type="primary"
            :loading="knowledgeStore.uploading"
            :disabled="!pendingFiles.length"
            @click="handleUpload"
          >
            <el-icon><Upload /></el-icon>
            开始上传并索引
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
/**
 * SmartQA Pro - 知识库管理页面
 *
 * 【学习要点】
 * 1. 文件上传：el-upload 组件 + FormData + multipart/form-data
 * 2. 知识库统计：切片数、嵌入模型、向量维度
 * 3. 上传流程：选文件 → 点击上传 → 后端解析切片 → 存入Milvus → 返回结果
 */
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useKnowledgeStore } from '@/stores/knowledge'
const knowledgeStore = useKnowledgeStore()
const uploadRef = ref(null)
const pendingFiles = ref([])

onMounted(() => {
  knowledgeStore.fetchDocuments()
  knowledgeStore.fetchStats()
})

function handleFileChange(file) {
  pendingFiles.value.push(file)
}

async function handleUpload() {
  for (const file of pendingFiles.value) {
    try {
      const res = await knowledgeStore.upload(file.raw)
      ElMessage.success(`文档 ${res.filename} 上传成功，已索引 ${res.chunk_count} 个切片`)
    } catch (err) {
      ElMessage.error(`上传失败: ${err.message}`)
    }
  }
  pendingFiles.value = []
  uploadRef.value?.clearFiles()
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
  margin-bottom: 20px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-value {
  font-size: 20px;
  font-weight: bold;
  color: #303133;
}

.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.upload-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header span {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.upload-actions {
  margin-top: 16px;
  text-align: right;
}

.doc-list-card {
  margin-bottom: 20px;
}
</style>
