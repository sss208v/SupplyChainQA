<template>
  <div class="rag-dag-wrapper" v-if="hasData">
    <!-- 折叠触发条 -->
    <div class="dag-toggle" @click="expanded = !expanded">
      <span class="dag-toggle-icon">{{ expanded ? '▼' : '▶' }}</span>
      <span class="dag-toggle-label">RAG 处理流程</span>
      <span class="dag-toggle-status" v-if="allDone">✓ 完成</span>
      <span class="dag-toggle-status running" v-else-if="anyRunning">处理中...</span>
    </div>

    <!-- DAG 可视化区域 -->
    <transition name="dag-slide">
      <div v-show="expanded" class="dag-container">
        <svg
          :width="svgWidth"
          :height="svgHeight"
          :viewBox="`0 0 ${svgWidth} ${svgHeight}`"
          class="dag-svg"
        >
          <!-- 绘制边（箭头连线） -->
          <g v-for="edge in computedEdges" :key="`${edge.from}-${edge.to}`">
            <line
              :x1="nodeCenterX"
              :y1="getNodeY(edge.from) + nodeH"
              :x2="nodeCenterX"
              :y2="getNodeY(edge.to)"
              :stroke="getEdgeColor(edge)"
              stroke-width="2"
              stroke-dasharray="none"
            />
            <!-- 箭头 -->
            <polygon
              :points="arrowPoints(edge)"
              :fill="getEdgeColor(edge)"
            />
          </g>

          <!-- 绘制节点 -->
          <g
            v-for="(node, idx) in dagNodes"
            :key="node.name"
            class="dag-node"
          >
            <!-- 节点背景圆角矩形 -->
            <rect
              :x="nodeX"
              :y="getNodeY(idx)"
              :width="nodeW"
              :height="nodeH"
              rx="8"
              ry="8"
              :fill="getNodeFill(node)"
              :stroke="getNodeStroke(node)"
              stroke-width="2"
              :class="{ 'node-pulse': node.status === 'running' }"
            />
            <!-- 状态图标 -->
            <text
              :x="nodeX + 16"
              :y="getNodeY(idx) + nodeH / 2 + 1"
              dominant-baseline="middle"
              font-size="14"
              :fill="getIconColor(node)"
            >
              {{ getStatusIcon(node) }}
            </text>
            <!-- 节点名称 -->
            <text
              :x="nodeX + 36"
              :y="getNodeY(idx) + nodeH / 2 - 6"
              dominant-baseline="middle"
              font-size="13"
              font-weight="600"
              :fill="getTextColor(node)"
            >
              {{ node.name }}
            </text>
            <!-- 耗时 -->
            <text
              :x="nodeX + 36"
              :y="getNodeY(idx) + nodeH / 2 + 12"
              dominant-baseline="middle"
              font-size="11"
              fill="#909399"
            >
              {{ formatDuration(node) }}
            </text>
          </g>
        </svg>
      </div>
    </transition>
  </div>
</template>

<script setup>
/**
 * RagDag.vue — RAG 流程 DAG 可视化组件
 *
 * 纯 SVG 绘制纵向流程图，展示 RAG 查询的各处理阶段。
 * 支持折叠/展开，实时显示每个节点的状态和耗时。
 */
import { ref, computed } from 'vue'

const props = defineProps({
  dagData: {
    type: Object,
    default: () => null,
    // 格式: { nodes: [{name, status, duration_ms}], edges: [{from, to}] }
  },
})

const expanded = ref(false)

// 默认节点定义（用于无数据时的占位）
const defaultNodeNames = [
  '意图路由',
  '查询理解',
  '向量检索',
  'BM25检索',
  'Reranker精排',
  '答案生成',
]

// 合并 props 数据与默认节点
const dagNodes = computed(() => {
  if (props.dagData?.nodes?.length) {
    return props.dagData.nodes
  }
  return defaultNodeNames.map((name) => ({
    name,
    status: 'pending',
    duration_ms: 0,
  }))
})

const computedEdges = computed(() => {
  if (props.dagData?.edges?.length) {
    return props.dagData.edges
  }
  // 默认纵向顺序边
  const edges = []
  for (let i = 0; i < dagNodes.value.length - 1; i++) {
    edges.push({ from: i, to: i + 1 })
  }
  return edges
})

const hasData = computed(() => !!props.dagData)

const allDone = computed(
  () => dagNodes.value.length > 0 && dagNodes.value.every((n) => n.status === 'done')
)

const anyRunning = computed(() => dagNodes.value.some((n) => n.status === 'running'))

// SVG 尺寸
const svgWidth = 360
const nodeW = 260
const nodeH = 48
const nodeGap = 10
const paddingTop = 10
const nodeX = (svgWidth - nodeW) / 2
const nodeCenterX = svgWidth / 2

const svgHeight = computed(() => {
  return paddingTop * 2 + dagNodes.value.length * (nodeH + nodeGap) - nodeGap
})

function getNodeY(idx) {
  return paddingTop + idx * (nodeH + nodeGap)
}

// 颜色映射
function getNodeFill(node) {
  switch (node.status) {
    case 'done':
      return '#f0f9eb'
    case 'running':
      return '#ecf5ff'
    default:
      return '#f5f7fa'
  }
}

function getNodeStroke(node) {
  switch (node.status) {
    case 'done':
      return '#67c23a'
    case 'running':
      return '#409eff'
    default:
      return '#dcdfe6'
  }
}

function getTextColor(node) {
  switch (node.status) {
    case 'done':
      return '#67c23a'
    case 'running':
      return '#409eff'
    default:
      return '#c0c4cc'
  }
}

function getIconColor(node) {
  switch (node.status) {
    case 'done':
      return '#67c23a'
    case 'running':
      return '#409eff'
    default:
      return '#c0c4cc'
  }
}

function getStatusIcon(node) {
  switch (node.status) {
    case 'done':
      return '✓'
    case 'running':
      return '⟳'
    default:
      return '○'
  }
}

function formatDuration(node) {
  if (!node.duration_ms || node.duration_ms <= 0) {
    return node.status === 'running' ? '运行中...' : '等待中'
  }
  if (node.duration_ms >= 1000) {
    return `${(node.duration_ms / 1000).toFixed(1)}s`
  }
  return `${node.duration_ms}ms`
}

function getEdgeColor(edge) {
  const fromNode = dagNodes.value[edge.from]
  if (fromNode?.status === 'done') return '#67c23a'
  if (fromNode?.status === 'running') return '#409eff'
  return '#dcdfe6'
}

function arrowPoints(edge) {
  const y2 = getNodeY(edge.to)
  const cx = nodeCenterX
  return `${cx - 5},${y2 - 2} ${cx},${y2 + 4} ${cx + 5},${y2 - 2}`
}
</script>

<style scoped>
.rag-dag-wrapper {
  margin: 8px 0;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.dag-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  cursor: pointer;
  user-select: none;
  background: #fafafa;
  border-bottom: 1px solid transparent;
  transition: background 0.2s;
  font-size: 13px;
}

.dag-toggle:hover {
  background: #f0f2f5;
}

.rag-dag-wrapper:has(.dag-container[style*="display"]) .dag-toggle {
  border-bottom-color: #ebeef5;
}

.dag-toggle-icon {
  font-size: 10px;
  color: #909399;
  width: 14px;
  text-align: center;
  transition: transform 0.2s;
}

.dag-toggle-label {
  font-weight: 600;
  color: #303133;
}

.dag-toggle-status {
  margin-left: auto;
  font-size: 12px;
  color: #67c23a;
}

.dag-toggle-status.running {
  color: #409eff;
}

.dag-container {
  padding: 8px;
  display: flex;
  justify-content: center;
  overflow: hidden;
}

.dag-svg {
  display: block;
}

/* 节点脉冲动画 */
.node-pulse {
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.7;
  }
}

/* 折叠展开过渡 */
.dag-slide-enter-active,
.dag-slide-leave-active {
  transition: all 0.25s ease;
  max-height: 500px;
}

.dag-slide-enter-from,
.dag-slide-leave-to {
  max-height: 0;
  opacity: 0;
  padding-top: 0;
  padding-bottom: 0;
}
</style>
