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
      <el-empty v-else description="暂无评估数据，请先执行评估" />
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
            <el-tag type="success">👍 {{ feedbackStats.positive_count }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="差评数">
            <el-tag type="danger">👎 {{ feedbackStats.negative_count }}</el-tag>
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
import { evaluateOnline, evaluateOffline, evaluateJudge, getEvalSummary } from '@/api/evaluate'
import { getFeedbackStats } from '@/api/chat'

// ---- 在线评估 ----
const onlineQuery = ref('')
const onlineLoading = ref(false)
const onlineResult = ref(null)

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
    const res = await getEvalSummary()
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

// ---- 辅助函数 ----
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
})
</script>

<style scoped>
.evaluate-page {
  max-width: 900px;
  margin: 0 auto;
}
.section-card {
  margin-bottom: 16px;
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
.section-desc {
  color: #909399;
  font-size: 13px;
  margin-bottom: 12px;
}
.eval-form {
  margin-bottom: 16px;
}
.eval-result {
  margin-top: 16px;
}
.negative-item {
  padding: 8px 0;
  border-bottom: 1px solid #ebeef5;
}
.time-text {
  color: #909399;
  font-size: 12px;
}
</style>
