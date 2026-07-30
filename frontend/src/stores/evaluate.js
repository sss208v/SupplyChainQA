/**
 * Supply Chain QA - 评估状态管理
 *
 * 管理 RAG 评估相关的状态：
 * - 在线评估结果（检索质量指标）
 * - 离线评估历史
 * - LLM-as-Judge 评判结果
 * - 评估汇总统计
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  evaluateOnline,
  evaluateOffline,
  evaluateJudge,
  getEvaluationSummary,
} from '@/api/evaluate'

export const useEvaluateStore = defineStore('evaluate', () => {
  // ---- 状态 ----
  const onlineResult = ref(null)      // 在线评估结果
  const offlineHistory = ref([])      // 离线评估历史
  const judgeResult = ref(null)       // Judge 评判结果
  const summary = ref(null)           // 评估汇总
  const loading = ref(false)

  // ---- Actions ----

  /**
   * 在线评估（无需 ground truth）
   */
  async function runOnlineEval(query, topK = 5) {
    loading.value = true
    try {
      const res = await evaluateOnline({ query, top_k: topK })
      onlineResult.value = res.evaluation
      return res.evaluation
    } finally {
      loading.value = false
    }
  }

  /**
   * 离线评估（需 ground truth）
   */
  async function runOfflineEval(query, retrievedChunkIds, relevantChunkIds) {
    loading.value = true
    try {
      const res = await evaluateOffline({ query, retrieved_chunk_ids: retrievedChunkIds, relevant_chunk_ids: relevantChunkIds })
      offlineHistory.value.push(res.evaluation)
      return res.evaluation
    } finally {
      loading.value = false
    }
  }

  /**
   * LLM-as-Judge 评判
   */
  async function runJudge(query, retrievedContexts, generatedAnswer, referenceAnswer) {
    loading.value = true
    try {
      const res = await evaluateJudge({ query, retrieved_contexts: retrievedContexts, generated_answer: generatedAnswer, reference_answer: referenceAnswer })
      judgeResult.value = res.judge_result
      return res.judge_result
    } finally {
      loading.value = false
    }
  }

  /**
   * 获取评估汇总
   */
  async function fetchSummary() {
    try {
      const res = await getEvaluationSummary()
      summary.value = res.summary
      return res.summary
    } catch (e) {
      console.error('[EvaluateStore] 获取汇总失败', e)
    }
  }

  function clearResults() {
    onlineResult.value = null
    judgeResult.value = null
  }

  return {
    onlineResult,
    offlineHistory,
    judgeResult,
    summary,
    loading,
    runOnlineEval,
    runOfflineEval,
    runJudge,
    fetchSummary,
    clearResults,
  }
})
