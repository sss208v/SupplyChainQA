/**
 * SmartQA Pro - 评估 API 接口
 */
import request from './request'

const API_PREFIX = '/api/v1'

/**
 * 离线评估（需 ground truth）
 * @param {string} query - 查询文本
 * @param {string[]} retrievedChunkIds - 检索返回的 chunk_id 列表
 * @param {string[]} relevantChunkIds - 实际相关的 chunk_id 列表
 */
export function evaluateOffline(data) {
  return request.post(`${API_PREFIX}/evaluate/offline`, data)
}

/**
 * 在线评估（无需 ground truth）
 * @param {string} query - 查询文本
 * @param {number} topK - Top-K
 */
export function evaluateOnline(data) {
  return request.post(`${API_PREFIX}/evaluate/online`, data)
}

/**
 * LLM-as-Judge 评判
 * @param {string} query - 原始问题
 * @param {string[]} retrievedContexts - 检索到的上下文
 * @param {string} generatedAnswer - 生成的答案
 * @param {string} [referenceAnswer] - 参考答案（可选）
 */
export function evaluateJudge(data) {
  return request.post(`${API_PREFIX}/evaluate/judge`, data)
}

/**
 * 获取评估历史汇总
 */
export function getEvaluationSummary() {
  return request.get(`${API_PREFIX}/evaluate/summary`)
}

/**
 * 运行全量 RAGAS 评估套件
 * 对黄金测试集中的所有 query 执行检索并计算三大指标
 */
export function runFullEvaluation() {
  return request.get(`${API_PREFIX}/evaluate/full`)
}
