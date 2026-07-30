<template>
  <div class="evaluate-page">
    <!-- 在线评估 -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><DataAnalysis /></el-icon> 在线检索评估</span>
        </div>
      </template>
      <p class="section-desc">输入查询，实时评估检索质量（无需 Ground Truth）</p>
      <div class="eval-form">
        <el-input
          v-model="onlineQuery"
          placeholder="输入测试查询，例如：什么是RAG？"
          @keydown.enter.prevent="runOnlineEval"
        >
          <template #append>
            <el-button :loading="onlineLoading" @click="runOnlineEval">
              <el-icon><Search /></el-icon> 评估
            </el-button>
          </template>
        </el-input>
      </div>

      <div v-if="onlineResult" class="eval-result">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="检索数量">{{ onlineResult.retrieved_count }}</el-descriptions-item>
          <el-descriptions-item label="平均Rerank分数">
            <el-tag :type="scoreType(onlineResult.avg_rerank_score)">
              {{ onlineResult.avg_rerank_score }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="质量等级">
            <el-tag :type="qualityType(onlineResult.quality_label)">
              {{ qualityLabel(onlineResult.quality_label) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="最高分">{{ onlineResult.max_rerank_score }}</el-descriptions-item>
          <el-descriptions-item label="最低分">{{ onlineResult.min_rerank_score }}</el-descriptions-item>
          <el-descriptions-item label="置信度">{{ onlineResult.avg_confidence }}</el-descriptions-item>
          <el-descriptions-item label="向量检索占比">
            <el-progress :percentage="Math.round(onlineResult.vector_ratio * 100)" :stroke-width="8" />
          </el-descriptions-item>
          <el-descriptions-item label="BM25检索占比">
            <el-progress :percentage="Math.round(onlineResult.bm25_ratio * 100)" :stroke-width="8" status="success" />
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>

    <!-- 离线评估 -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><Document /></el-icon> 离线评估（需 Ground Truth）</span>
        </div>
      </template>
      <p class="section-desc">提供检索结果和标准答案，计算 Recall/Precision/MRR/NDCG 指标</p>
      <el-form :model="offlineForm" label-width="120px" size="small">
        <el-form-item label="查询文本">
          <el-input v-model="offlineForm.query" placeholder="查询文本" />
        </el-form-item>
        <el-form-item label="检索结果ID">
          <el-input
            v-model="offlineForm.retrieved_ids"
            type="textarea"
            :rows="2"
            placeholder="按排名顺序输入chunk_id，逗号分隔"
          />
        </el-form-item>
        <el-form-item label="标准答案ID">
          <el-input
            v-model="offlineForm.relevant_ids"
            type="textarea"
            :rows="2"
            placeholder="相关chunk_id，逗号分隔"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="offlineLoading" @click="runOfflineEval">
            执行评估
          </el-button>
        </el-form-item>
      </el-form>

      <div v-if="offlineResult" class="eval-result">
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item label="Recall@1">{{ offlineResult.recall_at_1 }}</el-descriptions-item>
          <el-descriptions-item label="Recall@3">{{ offlineResult.recall_at_3 }}</el-descriptions-item>
          <el-descriptions-item label="Recall@5">{{ offlineResult.recall_at_5 }}</el-descriptions-item>
          <el-descriptions-item label="综合得分">
            <el-tag type="success">{{ offlineResult.retrieval_score }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Precision@1">{{ offlineResult.precision_at_1 }}</el-descriptions-item>
          <el-descriptions-item label="Precision@3">{{ offlineResult.precision_at_3 }}</el-descriptions-item>
          <el-descriptions-item label="Precision@5">{{ offlineResult.precision_at_5 }}</el-descriptions-item>
          <el-descriptions-item label="MRR">{{ offlineResult.mrr_at_k }}</el-descriptions-item>
          <el-descriptions-item label="NDCG@1">{{ offlineResult.ndcg_at_1 }}</el-descriptions-item>
          <el-descriptions-item label="NDCG@3">{{ offlineResult.ndcg_at_3 }}</el-descriptions-item>
          <el-descriptions-item label="NDCG@5">{{ offlineResult.ndcg_at_5 }}</el-descriptions-item>
          <el-descriptions-item label="MAP">{{ offlineResult.map_at_k }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>

    <!-- 评估历史汇总 -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><TrendCharts /></el-icon> 评估历史汇总</span>
          <el-button text type="primary" @click="fetchSummary">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
      </template>

      <div v-if="summary && summary.total_queries > 0">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="评估总数">{{ summary.total_queries }}</el-descriptions-item>
          <el-descriptions-item label="平均Recall@5">{{ summary.avg_recall_at_5 }}</el-descriptions-item>
          <el-descriptions-item label="平均NDCG@5">{{ summary.avg_ndcg_at_5 }}</el-descriptions-item>
          <el-descriptions-item label="平均MRR">{{ summary.avg_mrr }}</el-descriptions-item>
          <el-descriptions-item label="平均MAP">{{ summary.avg_map }}</el-descriptions-item>
          <el-descriptions-item label="综合得分均值">
            <el-tag type="success" size="large">{{ summary.avg_retrieval_score }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="P50">{{ summary.retrieval_score_p50 }}</el-descriptions-item>
          <el-descriptions-item label="P90">{{ summary.retrieval_score_p90 }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <el-empty v-else description="暂无评估数据，请先执行评估">
        <el-button type="primary" size="small" :loading="sampleLoading" @click="runSampleEval">
          <el-icon><DataAnalysis /></el-icon> 运行示例评估
        </el-button>
      </el-empty>
    </el-card>

    <!-- 用户反馈统计 -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><ChatDotRound /></el-icon> 用户反馈统计</span>
          <el-button text type="primary" @click="fetchFeedbackStats">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
      </template>

      <div v-if="feedbackStats && feedbackStats.total_feedback > 0">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="总反馈数">{{ feedbackStats.total_feedback }}</el-descriptions-item>
          <el-descriptions-item label="好评数">
            <el-tag type="success">好评 {{ feedbackStats.positive_count }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="差评数">
            <el-tag type="danger">差评 {{ feedbackStats.negative_count }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="满意度">
            <el-progress
              :percentage="Math.round(feedbackStats.satisfaction_rate * 100)"
              :stroke-width="16"
              :text-inside="true"
              :color="feedbackStats.satisfaction_rate >= 0.8 ? '#67c23a' : feedbackStats.satisfaction_rate >= 0.5 ? '#e6a23c' : '#f56c6c'"
            />
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="feedbackStats.recent_negative && feedbackStats.recent_negative.length">
          <el-divider content-position="left">最近差评</el-divider>
          <div v-for="(item, idx) in feedbackStats.recent_negative" :key="idx" class="negative-item">
            <p><strong>问题:</strong> {{ item.query }}</p>
            <p v-if="item.comment"><strong>备注:</strong> {{ item.comment }}</p>
            <p class="time-text">{{ item.created_at }}</p>
          </div>
        </div>
      </div>
      <el-empty v-else description="暂无反馈数据" />
    </el-card>

    <!-- RAGAS 评估 -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><DataAnalysis /></el-icon> 官方 RAGAS 评估结果</span>
          <el-button
            type="primary"
            size="small"
            :loading="ragasLoading"
            @click="runRagasEval"
          >
            <el-icon><DataAnalysis /></el-icon> 加载官方 RAGAS
          </el-button>
        </div>
      </template>
      <p class="section-desc">展示最近一次【官方 RAGAS】(ragas 0.4.3, LLM-as-Judge) 评测的四项指标（由 backend/eval/run_comprehensive_ragas.py 生成）</p>

      <div v-if="ragasResult && ragasResult.metrics" class="ragas-result">
        <div class="ragas-metrics">
          <div class="metric-card">
            <div class="metric-label">Faithfulness</div>
            <div class="metric-value" :style="{ color: scoreColor(ragasResult.metrics.faithfulness) }">
              {{ (ragasResult.metrics.faithfulness * 100).toFixed(1) }}%
            </div>
            <div class="metric-desc">忠实度/防幻觉</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Answer Relevancy</div>
            <div class="metric-value" :style="{ color: scoreColor(ragasResult.metrics.answer_relevancy) }">
              {{ (ragasResult.metrics.answer_relevancy * 100).toFixed(1) }}%
            </div>
            <div class="metric-desc">回答相关性</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Context Precision</div>
            <div class="metric-value" :style="{ color: scoreColor(ragasResult.metrics.context_precision) }">
              {{ (ragasResult.metrics.context_precision * 100).toFixed(1) }}%
            </div>
            <div class="metric-desc">检索准确率</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Context Recall</div>
            <div class="metric-value" :style="{ color: scoreColor(ragasResult.metrics.context_recall) }">
              {{ (ragasResult.metrics.context_recall * 100).toFixed(1) }}%
            </div>
            <div class="metric-desc">检索召回率</div>
          </div>
          <div class="metric-card metric-card-overall">
            <div class="metric-label">Overall</div>
            <div class="metric-value" :style="{ color: scoreColor(ragasResult.overall) }">
              {{ (ragasResult.overall * 100).toFixed(1) }}%
            </div>
            <div class="metric-desc">综合得分</div>
          </div>
        </div>
        <div class="ragas-info">
          <el-tag size="small" type="success">官方 ragas 0.4.3 (LLM-as-Judge)</el-tag>
          <el-tag size="small">judge: {{ ragasResult.judge_model || 'N/A' }}</el-tag>
          <el-tag size="small" type="info">{{ ragasResult.samples }} 条样本</el-tag>
          <el-tag size="small" type="info">{{ ragasResult.date || '' }}</el-tag>
        </div>
      </div>
      <el-empty v-else-if="!ragasLoading" description="点击「加载官方 RAGAS」显示最近一次官方评测结果（需先运行 backend/eval/run_comprehensive_ragas.py 生成）" />
    </el-card>

    <!-- LLM-as-Judge -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span><el-icon><Medal /></el-icon> LLM-as-Judge 质量评判</span>
        </div>
      </template>
      <p class="section-desc">使用大模型评判生成答案的质量（答案正确性/相关性/上下文利用/幻觉程度）</p>
      <el-form :model="judgeForm" label-width="100px" size="small">
        <el-form-item label="原始问题">
          <el-input v-model="judgeForm.query" placeholder="问题" />
        </el-form-item>
        <el-form-item label="检索上下文">
          <el-input
            v-model="judgeForm.contexts"
            type="textarea"
            :rows="3"
            placeholder="检索到的上下文（每行一段）"
          />
        </el-form-item>
        <el-form-item label="生成答案">
          <el-input
            v-model="judgeForm.answer"
            type="textarea"
            :rows="3"
            placeholder="LLM生成的答案"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="judgeLoading" @click="runJudge">
            评判
          </el-button>
        </el-form-item>
      </el-form>

      <div v-if="judgeResult" class="eval-result">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="答案正确性">
            <el-rate v-model="judgeResult.answer_correctness" disabled :max="5" />
          </el-descriptions-item>
          <el-descriptions-item label="答案相关性">
            <el-rate v-model="judgeResult.answer_relevance" disabled :max="5" />
          </el-descriptions-item>
          <el-descriptions-item label="上下文利用">
            <el-rate v-model="judgeResult.context_utilization" disabled :max="5" />
          </el-descriptions-item>
          <el-descriptions-item label="幻觉程度(越低越好)">
            <el-rate v-model="judgeResult.hallucination" disabled :max="5" />
          </el-descriptions-item>
          <el-descriptions-item label="综合评分" :span="2">
            <el-tag type="success" size="large">{{ judgeResult.overall_score }}/5</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="评审反馈" :span="2">
            {{ judgeResult.feedback }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>

    <!-- RAG 评估雷达图 -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span>RAGAS 评估雷达图</span>
          <el-tag size="small" type="info">官方 RAGAS (ragas 0.4.3, DeepSeek judge, 20条)</el-tag>
        </div>
      </template>
      <div ref="radarChartRef" style="width:100%;height:400px"></div>
    </el-card>

    <!-- RAG 检索诊断白盒 -->
    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <span>RAG 检索诊断白盒</span>
          <el-tag size="small" type="warning">示例：向量分 vs BM25分 → 融合分 → 精排分</el-tag>
        </div>
      </template>
      <div ref="diagnosisChartRef" style="width:100%;height:350px"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  DataAnalysis, Search, Document, TrendCharts,
  Refresh, Medal,
  ChatDotRound,
} from '@element-plus/icons-vue'
import { evaluateOnline, evaluateOffline, evaluateJudge, getEvaluationSummary, runFullEvaluation } from '@/api/evaluate'
import { getFeedbackStats } from '@/api/chat'

// ---- ECharts refs (loaded from CDN, no npm install needed) ----
const radarChartRef = ref(null)
const diagnosisChartRef = ref(null)
let echartsModule = null

function loadEchartsCDN() {
  return new Promise((resolve, reject) => {
    if (window.echarts) { resolve(window.echarts); return }
    const script = document.createElement('script')
    script.src = 'https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js'
    script.onload = () => resolve(window.echarts)
    script.onerror = () => reject(new Error('echarts CDN load failed'))
    document.head.appendChild(script)
  })
}

async function getEcharts() {
  if (!echartsModule) {
    try { echartsModule = await loadEchartsCDN() } catch { return null }
  }
  return echartsModule
}

// ---- 在线评估 ----
const onlineQuery = ref('')
const onlineLoading = ref(false)
const onlineResult = ref(null)
const sampleLoading = ref(false)
const sampleResults = ref([])

async function runOnlineEval() {
  if (!onlineQuery.value.trim()) return
  onlineLoading.value = true
  try {
    const res = await evaluateOnline({ query: onlineQuery.value, top_k: 5 })
    onlineResult.value = res.evaluation
  } catch (e) {
    ElMessage.error('评估失败: ' + (e.message || e))
  } finally {
    onlineLoading.value = false
  }
}

/**
 * 运行预设的示例评估（无需手动输入查询）
 * 发送3个供应链测试查询到在线评估接口，结果汇总展示
 */
async function runSampleEval() {
  sampleLoading.value = true
  sampleResults.value = []
  const sampleQueries = [
    '新供应商准入需要什么资质？',
    '安全库存的计算公式是什么？',
    '质量检验的抽检比例是多少？',
  ]
  try {
    for (const query of sampleQueries) {
      const res = await evaluateOnline({ query, top_k: 5 })
      sampleResults.value.push({
        query,
        result: res.evaluation,
      })
    }
    // 设置第一个结果为默认显示
    if (sampleResults.value.length > 0) {
      onlineResult.value = sampleResults.value[0].result
    }
    // 刷新汇总
    await fetchSummary()
    ElMessage.success(`已完成 ${sampleQueries.length} 个示例评估`)
  } catch (e) {
    ElMessage.error('示例评估失败: ' + (e.message || e))
  } finally {
    sampleLoading.value = false
  }
}

// ---- 离线评估 ----
const offlineForm = ref({
  query: '',
  retrieved_ids: '',
  relevant_ids: '',
})
const offlineLoading = ref(false)
const offlineResult = ref(null)

async function runOfflineEval() {
  const { query, retrieved_ids, relevant_ids } = offlineForm.value
  if (!query || !retrieved_ids || !relevant_ids) {
    ElMessage.warning('请填写所有字段')
    return
  }
  offlineLoading.value = true
  try {
    const res = await evaluateOffline({
      query,
      retrieved_chunk_ids: retrieved_ids.split(',').map(s => s.trim()),
      relevant_chunk_ids: relevant_ids.split(',').map(s => s.trim()),
    })
    offlineResult.value = res.evaluation
  } catch (e) {
    ElMessage.error('评估失败: ' + (e.message || e))
  } finally {
    offlineLoading.value = false
  }
}

// ---- 汇总 ----
const summary = ref(null)

async function fetchSummary() {
  try {
    const res = await getEvaluationSummary()
    summary.value = res.summary
  } catch (e) {
    console.error('获取汇总失败', e)
  }
}

// ---- LLM-as-Judge ----
const judgeForm = ref({ query: '', contexts: '', answer: '' })
const judgeLoading = ref(false)
const judgeResult = ref(null)

async function runJudge() {
  const { query, contexts, answer } = judgeForm.value
  if (!query || !answer) {
    ElMessage.warning('请填写问题和答案')
    return
  }
  judgeLoading.value = true
  try {
    const res = await evaluateJudge({
      query,
      retrieved_contexts: contexts.split('\n').filter(s => s.trim()),
      generated_answer: answer,
    })
    judgeResult.value = res.judge_result
  } catch (e) {
    ElMessage.error('评判失败: ' + (e.message || e))
  } finally {
    judgeLoading.value = false
  }
}

// ---- RAGAS 全量评估 ----
const ragasLoading = ref(false)
const ragasResult = ref(null)

async function runRagasEval() {
  ragasLoading.value = true
  ragasResult.value = null
  try {
    const res = await runFullEvaluation()
    if (res.success) {
      ragasResult.value = res
      const m = res.metrics || {}
      ElMessage.success(`官方 RAGAS(judge=${res.judge_model || 'N/A'}): F=${(m.faithfulness * 100).toFixed(1)}% / AR=${(m.answer_relevancy * 100).toFixed(1)}%`)
    } else {
      ElMessage.warning(res.error || '暂无官方 RAGAS 结果')
    }
  } catch (e) {
    ElMessage.error('全量评估失败: ' + (e.message || e))
  } finally {
    ragasLoading.value = false
  }
}

// ---- 辅助函数 ----
function scoreColor(score) {
  if (score >= 0.8) return '#10b981'
  if (score >= 0.5) return '#f59e0b'
  return '#ef4444'
}

function scoreType(score) {
  if (score >= 0.8) return 'success'
  if (score >= 0.5) return 'warning'
  return 'danger'
}

function qualityType(label) {
  const map = { excellent: 'success', good: 'success', fair: 'warning', poor: 'danger' }
  return map[label] || 'info'
}

function qualityLabel(label) {
  const map = { excellent: '优秀', good: '良好', fair: '一般', poor: '较差', no_results: '无结果', no_signal: '无信号' }
  return map[label] || label
}

// ---- 用户反馈统计 ----
const feedbackStats = ref(null)

async function fetchFeedbackStats() {
  try {
    const res = await getFeedbackStats()
    feedbackStats.value = res
  } catch (e) {
    console.error('获取反馈统计失败', e)
  }
}

onMounted(() => {
  fetchSummary()
  fetchFeedbackStats()
  initRadarChart()
  initDiagnosisChart()
})

// ---- ECharts 图表初始化 ----
async function initRadarChart() {
  if (!radarChartRef.value) return
  const echarts = await getEcharts()
  if (!echarts) return
  const chart = echarts.init(radarChartRef.value)

  // RAGAS 默认指标（来自 eval_ragas_result_full_sc.json 的均值）
  const option = {
    title: { text: 'RAGAS 四大核心指标', left: 'center', textStyle: { fontSize: 14, color: '#303133' } },
    tooltip: {},
    legend: { data: ['官方 RAGAS'], bottom: 0 },
    radar: {
      indicator: [
        { name: 'Context Precision\n检索准确率', max: 1 },
        { name: 'Faithfulness\n忠实度', max: 1 },
        { name: 'Answer Relevance\n回答相关性', max: 1 },
        { name: 'Context Recall\n检索召回率', max: 1 },
      ],
      radius: '60%',
    },
    series: [{
      type: 'radar',
      name: 'RAGAS Scores',
      data: [{
        value: [0.693, 0.803, 0.839, 0.825],
        name: '官方 RAGAS',
        areaStyle: { color: 'rgba(37,99,235,0.15)' },
        lineStyle: { color: '#2563eb', width: 2 },
        itemStyle: { color: '#2563eb' },
        label: {
          show: true,
          distance: 15,
          formatter: function(params) {
            return (params.value * 100).toFixed(1) + '%';
          },
          color: '#2563eb',
          fontSize: 12,
          fontWeight: 600,
        },
      }],
    }],
  }
  chart.setOption(option)
}

async function initDiagnosisChart() {
  if (!diagnosisChartRef.value) return
  const echarts = await getEcharts()
  if (!echarts) return
  const chart = echarts.init(diagnosisChartRef.value)

  // RAG 检索诊断：模拟两路检索的分数对比
  const chunks = ['Chunk A', 'Chunk B', 'Chunk C', 'Chunk D', 'Chunk E']
  const vectorScores = [0.45, 0.72, 0.38, 0.61, 0.29]
  const bm25Scores = [0.33, 0.55, 0.62, 0.41, 0.48]
  const fusionScores = [0.35, 0.68, 0.55, 0.53, 0.42]
  const rerankScores = [0.30, 0.85, 0.48, 0.72, 0.25]

  const option = {
    title: { text: '检索链路分数对比', left: 'center', textStyle: { fontSize: 14, color: '#303133' } },
    tooltip: { trigger: 'axis' },
    legend: { data: ['向量检索分', 'BM25分', 'RRF融合分', 'Reranker精排分'], bottom: 0 },
    xAxis: { type: 'category', data: chunks, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: 'Score', max: 1 },
    series: [
      { name: '向量检索分', type: 'bar', data: vectorScores, itemStyle: { color: '#2563eb' }, barGap: '10%' },
      { name: 'BM25分', type: 'bar', data: bm25Scores, itemStyle: { color: '#10b981' } },
      { name: 'RRF融合分', type: 'line', data: fusionScores, lineStyle: { color: '#f59e0b', width: 2 }, symbol: 'diamond' },
      { name: 'Reranker精排分', type: 'line', data: rerankScores, lineStyle: { color: '#ef4444', width: 2.5 }, symbol: 'circle' },
    ],
    grid: { top: 50, bottom: 40 },
  }
  chart.setOption(option)
}
</script>

<style scoped>
.evaluate-page {
  max-width: 900px;
  margin: 0 auto;
  padding: var(--space-5);
}
.section-card {
  margin-bottom: var(--space-4);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
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
  font-size: 14px;
}
.section-desc {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-bottom: var(--space-4);
}
.eval-form {
  margin-bottom: var(--space-4);
}
.eval-result {
  margin-top: var(--space-4);
}
.negative-item {
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border-light);
}
.time-text {
  color: var(--color-text-meta);
  font-size: 12px;
}

/* RAGAS 评估指标卡片 */
.ragas-metrics {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.metric-card {
  flex: 1;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  text-align: center;
  transition: all var(--transition-base);
}

.metric-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-raised);
}

.metric-card-overall {
  background: var(--color-primary-light);
  border-color: rgba(37, 99, 235, 0.15);
}

.metric-label {
  font-family: var(--font-heading);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-secondary);
  margin-bottom: 8px;
}

.metric-value {
  font-family: var(--font-heading);
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 4px;
}

.metric-desc {
  font-size: 12px;
  color: var(--color-text-placeholder);
}
.ragas-info {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.ragas-detail-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--color-border-light);
}
.detail-query {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 13px;
}
.detail-scores {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

@media (max-width: 767px) { .evaluate-page { padding: 0; } .evaluate-page .el-card { margin-bottom: 10px; } .evaluate-page .el-row { flex-direction: column; } .evaluate-page .el-col { max-width: 100%; flex: 0 0 100%; margin-bottom: 10px; } .evaluate-page .el-table { font-size: 11px; } }
</style>
