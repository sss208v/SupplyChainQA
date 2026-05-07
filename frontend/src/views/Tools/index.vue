<template>
  <div class="tools-page">
    <div class="tools-header">
      <h2>🔧 工具管理</h2>
      <p class="tools-subtitle">供应链工具注册表 · 基于 ReAct 模式调用</p>
    </div>

    <div class="tools-grid">
      <div
        v-for="tool in tools"
        :key="tool.name"
        :class="['tool-node', { 'tool-node--active': activeTool === tool.name }]"
        @click="activeTool = tool.name"
      >
        <!-- 节点头部 -->
        <div class="node-header">
          <div class="node-icon" :style="{ background: toolColor(tool.name) }">
            <el-icon :size="20"><component :is="toolIcon(tool.name)" /></el-icon>
          </div>
          <div class="node-title">
            <h4>{{ tool.name }}</h4>
            <span class="node-type">{{ toolType(tool.name) }}</span>
          </div>
          <el-tag size="small" type="success" effect="plain">已注册</el-tag>
        </div>

        <!-- 节点描述 -->
        <p class="node-desc">{{ tool.description }}</p>

        <!-- 输入/输出 Schema -->
        <div class="node-schema">
          <div class="schema-section">
            <div class="schema-label">
              <el-icon><Top /></el-icon> 输入参数
            </div>
            <div class="schema-params">
              <div v-for="param in tool.inputs" :key="param.name" class="param-item">
                <span class="param-name">{{ param.name }}</span>
                <span class="param-type">{{ param.type }}</span>
                <span class="param-desc">{{ param.description }}</span>
              </div>
              <div v-if="!tool.inputs || tool.inputs.length === 0" class="param-empty">
                无参数
              </div>
            </div>
          </div>

          <div class="schema-section">
            <div class="schema-label">
              <el-icon><Bottom /></el-icon> 输出格式
            </div>
            <div class="schema-output">
              <code>{{ tool.output || 'JSON' }}</code>
            </div>
          </div>
        </div>

        <!-- 测试按钮 -->
        <div class="node-actions">
          <el-button
            type="primary"
            text
            size="small"
            @click.stop="openTestPanel(tool)"
          >
            <el-icon><VideoPlay /></el-icon> 测试调用
          </el-button>
        </div>
      </div>
    </div>

    <!-- 测试面板 -->
    <el-drawer
      v-model="testDrawerVisible"
      :title="`测试工具: ${currentTool?.name}`"
      direction="rtl"
      size="450px"
    >
      <div v-if="currentTool" class="test-panel">
        <!-- 工具信息 -->
        <div class="test-tool-info">
          <h4>{{ currentTool.name }}</h4>
          <p>{{ currentTool.description }}</p>
        </div>

        <!-- 输入表单 -->
        <div class="test-input-section">
          <h5>输入参数</h5>
          <div v-for="param in currentTool.inputs" :key="param.name" class="test-input-item">
            <label>{{ param.name }} <span class="input-type">{{ param.type }}</span></label>
            <el-input
              v-model="testInputs[param.name]"
              :placeholder="param.description"
              size="small"
            />
          </div>
        </div>

        <!-- 执行按钮 -->
        <el-button
          type="primary"
          :loading="testLoading"
          @click="handleTest"
          style="width: 100%; margin-top: 16px;"
        >
          <el-icon><VideoPlay /></el-icon> 执行测试
        </el-button>

        <!-- 测试结果 -->
        <div v-if="testResult" class="test-result-section">
          <h5>执行结果</h5>
          <div class="result-meta">
            <el-tag size="small" :type="testResult.error ? 'danger' : 'success'">
              {{ testResult.error ? '失败' : '成功' }}
            </el-tag>
            <span v-if="testResult.iterations" class="result-iter">
              ReAct 迭代: {{ testResult.iterations }} 次
            </span>
          </div>

          <div class="result-output">
            <pre>{{ testResult.answer }}</pre>
          </div>

          <div v-if="testResult.tool_calls?.length" class="result-calls">
            <h5>工具调用链</h5>
            <div v-for="(tc, idx) in testResult.tool_calls" :key="idx" class="call-item">
              <div class="call-header">
                <el-tag size="small">{{ tc.tool }}</el-tag>
                <span class="call-step">#{{ idx + 1 }}</span>
              </div>
              <div class="call-detail">
                <div class="detail-row">
                  <span class="detail-label">输入:</span>
                  <code>{{ JSON.stringify(tc.input) }}</code>
                </div>
                <div class="detail-row">
                  <span class="detail-label">输出:</span>
                  <code>{{ tc.observation }}</code>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
/**
 * 工具管理页面 - 类 Dify 代码节点风格
 *
 * 【学习要点】
 * 1. 展示工具的输入/输出 Schema（类似 Dify 的代码节点）
 * 2. 支持直接测试工具调用
 * 3. 展示 ReAct 迭代过程
 */
import { ref, onMounted, markRaw } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Cloudy, DataAnalysis, Clock, Reading,
  VideoPlay, SetUp, Refresh, Box,
  OfficeBuilding, List, Top, Bottom,
} from '@element-plus/icons-vue'
import { getToolList, callTool } from '@/api/tool'

const tools = ref([])
const activeTool = ref(null)
const testDrawerVisible = ref(false)
const currentTool = ref(null)
const testInputs = ref({})
const testResult = ref(null)
const testLoading = ref(false)

// 工具图标
const iconMap = {
  query_inventory: markRaw(Box),
  query_order: markRaw(List),
  create_ticket: markRaw(OfficeBuilding),
  get_datetime: markRaw(Clock),
  get_knowledge: markRaw(Reading),
}
const colorMap = {
  query_inventory: '#409eff',
  query_order: '#67c23a',
  create_ticket: '#e6a23c',
  get_datetime: '#909399',
  get_knowledge: '#f56c6c',
}

// 工具输入 Schema（从代码中提取）
const toolSchemas = {
  query_inventory: {
    inputs: [
      { name: 'material_code', type: 'str', description: '物料编码（如 MAT-001）' },
    ],
    output: '{ material_code, name, quantity, unit, safety_stock, status }',
    type: '数据查询',
  },
  query_order: {
    inputs: [
      { name: 'order_id', type: 'str', description: '采购订单号（如 PO-20250101）' },
    ],
    output: '{ order_id, supplier, status, items, total_amount, expected_date }',
    type: '数据查询',
  },
  create_ticket: {
    inputs: [
      { name: 'title', type: 'str', description: '工单标题' },
      { name: 'description', type: 'str', description: '工单详细描述' },
      { name: 'priority', type: 'str', description: '优先级（高/中/低）' },
    ],
    output: '{ ticket_id, title, status, created_at }',
    type: '操作执行',
  },
  get_datetime: {
    inputs: [],
    output: '当前时间字符串 (YYYY-MM-DD HH:MM:SS)',
    type: '系统工具',
  },
  get_knowledge: {
    inputs: [
      { name: 'query', type: 'str', description: '查询关键词' },
    ],
    output: '知识库检索结果文本',
    type: '数据查询',
  },
}

function toolIcon(name) {
  return iconMap[name] || SetUp
}

function toolColor(name) {
  return colorMap[name] || '#909399'
}

function toolType(name) {
  return toolSchemas[name]?.type || '自定义'
}

async function fetchTools() {
  try {
    const res = await getToolList()
    // 合并 Schema 信息
    tools.value = (res.tools || []).map(t => ({
      ...t,
      inputs: toolSchemas[t.name]?.inputs || [],
      output: toolSchemas[t.name]?.output || 'JSON',
    }))
  } catch (err) {
    ElMessage.error('获取工具列表失败')
  }
}

function openTestPanel(tool) {
  currentTool.value = tool
  testInputs.value = {}
  testResult.value = null
  // 预填测试数据
  if (tool.name === 'query_inventory') testInputs.value.material_code = 'MAT-001'
  if (tool.name === 'query_order') testInputs.value.order_id = 'PO-20250101'
  testDrawerVisible.value = true
}

async function handleTest() {
  if (!currentTool.value) return
  testLoading.value = true
  testResult.value = null

  try {
    // 构造查询：将输入参数组合成自然语言
    const params = Object.entries(testInputs.value)
      .filter(([_, v]) => v)
      .map(([k, v]) => `${k}=${v}`)
      .join(', ')
    const query = `调用${currentTool.value.name}工具${params ? '，参数: ' + params : ''}`

    const res = await callTool({ query })
    testResult.value = res
  } catch (err) {
    testResult.value = { error: true, answer: err.message }
  } finally {
    testLoading.value = false
  }
}

onMounted(fetchTools)
</script>

<style scoped>
.tools-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
}

.tools-header {
  margin-bottom: 24px;
}

.tools-header h2 {
  font-size: 22px;
  margin-bottom: 4px;
}

.tools-subtitle {
  color: #909399;
  font-size: 14px;
}

/* 工具网格 */
.tools-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

/* 工具节点卡片 */
.tool-node {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 12px;
  padding: 20px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.tool-node:hover {
  border-color: #409eff;
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.1);
}

.tool-node--active {
  border-color: #409eff;
  background: #f0f7ff;
}

/* 节点头部 */
.node-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.node-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
}

.node-title h4 {
  font-size: 15px;
  margin: 0 0 2px;
  font-family: 'SF Mono', 'Consolas', monospace;
}

.node-type {
  font-size: 12px;
  color: #909399;
}

/* 描述 */
.node-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.5;
  margin-bottom: 16px;
}

/* Schema 区域 */
.node-schema {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}

.schema-section {
  margin-bottom: 10px;
}

.schema-section:last-child {
  margin-bottom: 0;
}

.schema-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
}

.schema-params {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.param-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.param-name {
  font-family: 'SF Mono', 'Consolas', monospace;
  color: #409eff;
  font-weight: 500;
}

.param-type {
  color: #909399;
  font-size: 11px;
}

.param-desc {
  color: #606266;
  font-size: 11px;
}

.param-empty {
  font-size: 12px;
  color: #c0c4cc;
  font-style: italic;
}

.schema-output code {
  font-size: 12px;
  color: #67c23a;
  background: #f0f9eb;
  padding: 2px 6px;
  border-radius: 4px;
}

/* 操作区 */
.node-actions {
  display: flex;
  justify-content: flex-end;
}

/* 测试面板 */
.test-tool-info {
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}

.test-tool-info h4 {
  font-size: 16px;
  margin-bottom: 4px;
}

.test-tool-info p {
  font-size: 13px;
  color: #606266;
}

.test-input-section h5 {
  font-size: 14px;
  margin-bottom: 12px;
  color: #303133;
}

.test-input-item {
  margin-bottom: 12px;
}

.test-input-item label {
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
  color: #606266;
}

.input-type {
  color: #909399;
  font-size: 11px;
}

/* 测试结果 */
.test-result-section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}

.test-result-section h5 {
  font-size: 14px;
  margin-bottom: 12px;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.result-iter {
  font-size: 12px;
  color: #909399;
}

.result-output pre {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.result-calls {
  margin-top: 16px;
}

.result-calls h5 {
  font-size: 13px;
  margin-bottom: 8px;
}

.call-item {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 8px;
}

.call-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.call-step {
  font-size: 12px;
  color: #909399;
}

.call-detail {
  font-size: 12px;
}

.detail-row {
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}

.detail-label {
  color: #909399;
  flex-shrink: 0;
}

.detail-row code {
  background: #e8eaec;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
  word-break: break-all;
}
</style>
