<template>
  <div class="tools-page">
    <div class="tools-header">
      <h2>工具管理</h2>
      <p class="tools-subtitle">供应链工具注册表 · 基于 ReAct 模式调用</p>
    </div>

    <div class="tools-grid">
      <div
        v-for="tool in tools"
        :key="tool.name"
        :class="[
          'tool-node',
          { 'tool-node--active': activeTool === tool.name },
        ]"
        @click="activeTool = tool.name"
      >
        <!-- 节点头部 -->
        <div class="node-header">
          <div class="node-icon" :style="{ background: toolColor(tool.name) }">
            <el-icon :size="20"
              ><component :is="toolIcon(tool.name)"
            /></el-icon>
          </div>
          <div class="node-title">
            <h4>{{ tool.name }}</h4>
            <span class="node-type">{{ toolType(tool.name) }}</span>
          </div>
          <el-tag size="small" type="success" effect="plain">已注册</el-tag>
        </div>

        <!-- 可用角色标签 -->
        <div class="node-roles">
          <span class="roles-label">可用角色：</span>
          <el-tag
            v-for="role in tool.allowed_roles"
            :key="role"
            size="small"
            effect="plain"
            :type="roleColor(role)"
            class="role-tag"
          >
            {{ roleLabel(role) }}
          </el-tag>
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
              <div
                v-for="param in tool.inputs"
                :key="param.name"
                class="param-item"
              >
                <span class="param-name">{{ param.name }}</span>
                <span class="param-type">{{ param.type }}</span>
                <span class="param-desc">{{ param.description }}</span>
              </div>
              <div
                v-if="!tool.inputs || tool.inputs.length === 0"
                class="param-empty"
              >
                无参数
              </div>
            </div>
          </div>

          <div class="schema-section">
            <div class="schema-label">
              <el-icon><Bottom /></el-icon> 输出格式
            </div>
            <div class="schema-output">
              <code>{{ tool.output || "JSON" }}</code>
            </div>
          </div>
        </div>

        <!-- 测试按钮（写工具仅 manager+ 可见） -->
        <div class="node-actions">
          <el-button
            v-if="canTestTool(tool)"
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
          <div
            v-for="param in currentTool.inputs"
            :key="param.name"
            class="test-input-item"
          >
            <label
              >{{ param.name }}
              <span class="input-type">{{ param.type }}</span></label
            >
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
          style="width: 100%; margin-top: 16px"
        >
          <el-icon><VideoPlay /></el-icon> 执行测试
        </el-button>

        <!-- 测试结果 -->
        <div v-if="testResult" class="test-result-section">
          <h5>执行结果</h5>
          <div class="result-meta">
            <el-tag
              size="small"
              :type="testResult.error ? 'danger' : 'success'"
            >
              {{ testResult.error ? "失败" : "成功" }}
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
            <div
              v-for="(tc, idx) in testResult.tool_calls"
              :key="idx"
              class="call-item"
            >
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
 * 1. 展示工具的输入/输出 Schema（类似 Dify 的代码节点）
 * 2. 支持直接测试工具调用
 * 3. 展示 ReAct 迭代过程
 */
import { ref, onMounted, markRaw } from "vue";
import { ElMessage } from "element-plus";
import {
  Cloudy,
  DataAnalysis,
  Clock,
  Reading,
  VideoPlay,
  SetUp,
  Refresh,
  Box,
  OfficeBuilding,
  List,
  Top,
  Bottom,
} from "@element-plus/icons-vue";
import { getToolList, getToolSchemas, callTool } from "@/api/tool";
import { useAuthStore } from "@/stores/auth";

const authStore = useAuthStore();
const tools = ref([]);
const activeTool = ref(null);
const testDrawerVisible = ref(false);
const currentTool = ref(null);
const testInputs = ref({});
const testResult = ref(null);
const testLoading = ref(false);

// 写操作工具：测试入口需 manager 及以上级别（employee 仅只读工具）
const WRITE_TOOL_NAMES = ["create_ticket"];
function canTestTool(tool) {
  if (!WRITE_TOOL_NAMES.includes(tool.name)) return true;
  return authStore.can("tool:write");
}

// 工具图标（未知工具回退 SetUp 默认图标）
const iconMap = {
  query_inventory: markRaw(Box),
  query_order: markRaw(List),
  create_ticket: markRaw(OfficeBuilding),
  get_datetime: markRaw(Clock),
  get_knowledge: markRaw(Reading),
  query_supplier: markRaw(OfficeBuilding),
  track_logistics: markRaw(Cloudy),
  calculate_reorder_point: markRaw(DataAnalysis),
  web_search: markRaw(Cloudy),
  calculator: markRaw(DataAnalysis),
  code_interpreter: markRaw(SetUp),
};
const colorMap = {
  query_inventory: "#2563eb",
  query_order: "#10b981",
  create_ticket: "#f59e0b",
  get_datetime: "#6b7280",
  get_knowledge: "#ef4444",
  query_supplier: "#8b5cf6",
  track_logistics: "#0ea5e9",
  calculate_reorder_point: "#14b8a6",
  web_search: "#6366f1",
  calculator: "#f97316",
  code_interpreter: "#64748b",
};

// 工具分类标签（仅展示用途；输入 Schema 不再硬编码，改由
// GET /api/v1/tools/schema 从后端 TOOL_REGISTRY 动态拉取）
const toolTypeMap = {
  query_inventory: "数据查询",
  query_order: "数据查询",
  create_ticket: "操作执行",
  get_datetime: "系统工具",
  get_knowledge: "数据查询",
  query_supplier: "数据查询",
  track_logistics: "数据查询",
  calculate_reorder_point: "智能计算",
  web_search: "通用工具",
  calculator: "通用工具",
  code_interpreter: "通用工具",
};

function toolIcon(name) {
  return iconMap[name] || SetUp;
}

function toolColor(name) {
  return colorMap[name] || "#909399";
}

function toolType(name) {
  return toolTypeMap[name] || "自定义";
}

// 角色中文映射
const roleLabelMap = {
  admin: "管理员",
  purchase: "采购部",
  warehouse: "仓库部",
  quality: "质量部",
  production: "生产部",
  finance: "财务部",
  logistics: "物流部",
};

// 角色标签颜色
const roleColorMap = {
  admin: "danger",
  purchase: "success",
  warehouse: "warning",
  quality: "info",
  production: "",
  finance: "",
  logistics: "",
};

function roleLabel(role) {
  return roleLabelMap[role] || role;
}

function roleColor(role) {
  return roleColorMap[role] || "info";
}

async function fetchTools() {
  try {
    // 并行拉取工具列表 + 后端动态生成的输入 Schema（单一事实来源）
    const [res, schemaRes] = await Promise.all([
      getToolList(),
      getToolSchemas(),
    ]);
    const schemas = schemaRes.schemas || {};
    tools.value = (res.tools || []).map((t) => ({
      ...t,
      inputs: schemas[t.name]?.inputs || [],
      output: "JSON",
    }));
  } catch (err) {
    ElMessage.error("获取工具列表失败");
  }
}

function openTestPanel(tool) {
  currentTool.value = tool;
  testInputs.value = {};
  testResult.value = null;
  // 预填测试数据（真实库内示例值）
  if (tool.name === "query_inventory")
    testInputs.value.material_code = "MAT-001";
  if (tool.name === "query_order") testInputs.value.order_id = "PO-20250601";
  if (tool.name === "query_ticket")
    testInputs.value.ticket_id = "TK-202506010000001";
  if (tool.name === "query_stock_move")
    testInputs.value.po_code = "PO-20250602";
  testDrawerVisible.value = true;
}

async function handleTest() {
  if (!currentTool.value) return;
  testLoading.value = true;
  testResult.value = null;

  try {
    // 构造查询：将输入参数组合成自然语言
    const params = Object.entries(testInputs.value)
      .filter(([_, v]) => v)
      .map(([k, v]) => `${k}=${v}`)
      .join(", ");
    const query = params ? `参数: ${params}` : "执行工具";

    const res = await callTool({
      query,
      tool_names: [currentTool.value.name],
    });
    testResult.value = res;
  } catch (err) {
    testResult.value = { error: true, answer: err.message };
  } finally {
    testLoading.value = false;
  }
}

onMounted(fetchTools);
</script>

<style scoped>
.tools-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--space-5);
}

.tools-header {
  margin-bottom: var(--space-6);
}

.tools-header h2 {
  font-family: var(--font-heading);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  margin-bottom: 4px;
}

.tools-subtitle {
  color: var(--color-text-placeholder);
  font-size: 14px;
}

/* 工具网格 */
.tools-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--space-4);
}

/* 工具节点卡片 */
.tool-node {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  cursor: pointer;
  transition: all var(--transition-base);
}

.tool-node:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow-card-hover);
  transform: translateY(-2px);
}

.tool-node--active {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
}

/* 节点头部 */
.node-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

/* 可用角色标签 */
.node-roles {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  flex-wrap: wrap;
}

.roles-label {
  font-size: 12px;
  color: var(--color-text-meta);
  flex-shrink: 0;
}

.role-tag {
  font-size: 11px;
}

.node-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
}

.node-title h4 {
  font-size: 15px;
  margin: 0 0 2px;
  font-family: var(--font-mono);
  font-weight: 500;
}

.node-type {
  font-size: 12px;
  color: var(--color-text-placeholder);
}

/* 描述 */
.node-desc {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.5;
  margin-bottom: var(--space-4);
}

/* Schema 区域 */
.node-schema {
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  margin-bottom: var(--space-3);
}

.schema-section {
  margin-bottom: var(--space-2);
}

.schema-section:last-child {
  margin-bottom: 0;
}

.schema-label {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.schema-params {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.param-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 12px;
}

.param-name {
  font-family: var(--font-mono);
  color: var(--color-primary);
  font-weight: 500;
}

.param-type {
  color: var(--color-text-meta);
  font-size: 11px;
}

.param-desc {
  color: var(--color-text-secondary);
  font-size: 11px;
}

.param-empty {
  font-size: 12px;
  color: var(--color-text-meta);
  font-style: italic;
}

.schema-output code {
  font-size: 12px;
  color: var(--color-schema-text);
  background: var(--color-schema-output);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}

/* 操作区 */
.node-actions {
  display: flex;
  justify-content: flex-end;
}

/* 测试面板 */
.test-tool-info {
  margin-bottom: var(--space-5);
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.test-tool-info h4 {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 4px;
}

.test-tool-info p {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.test-input-section h5 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: var(--space-3);
  color: var(--color-text-primary);
}

.test-input-item {
  margin-bottom: var(--space-3);
}

.test-input-item label {
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
  color: var(--color-text-secondary);
}

.input-type {
  color: var(--color-text-meta);
  font-size: 11px;
}

/* 测试结果 */
.test-result-section {
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.test-result-section h5 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: var(--space-3);
  color: var(--color-text-primary);
}

.result-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.result-iter {
  font-size: 12px;
  color: var(--color-text-meta);
}

.result-output pre {
  background: var(--color-code-bg);
  color: var(--color-code-text);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  font-size: 13px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.result-calls {
  margin-top: var(--space-4);
}

.result-calls h5 {
  font-size: 13px;
  margin-bottom: var(--space-2);
  color: var(--color-text-primary);
}

.call-item {
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-2);
  margin-bottom: var(--space-2);
}

.call-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: 6px;
}

.call-step {
  font-size: 12px;
  color: var(--color-text-meta);
}

.call-detail {
  font-size: 12px;
}

.detail-row {
  display: flex;
  gap: var(--space-2);
  margin-bottom: 4px;
}

.detail-label {
  color: var(--color-text-meta);
  flex-shrink: 0;
}

.detail-row code {
  background: var(--color-code-inline-bg);
  padding: 1px 4px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  word-break: break-all;
  font-family: var(--font-mono);
}

@media (max-width: 767px) {
  .tools-page {
    padding: 0;
  }
  .tools-page .el-card {
    margin-bottom: 10px;
  }
  .tools-page .el-descriptions {
    font-size: 12px;
  }
  .tools-page .el-table {
    font-size: 12px;
  }
}
</style>
