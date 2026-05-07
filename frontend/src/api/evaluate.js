/**
 * SmartQA Pro - 评估 API 客户端
 */
import request from './request'

/**
 * 在线评估（无需 ground truth）
 */
export function evaluateOnline(data) {
  return request({
    url: '/api/v1/evaluate/online',
    method: 'post',
    data,
  })
}

/**
 * 离线评估（需要 ground truth）
 */
export function evaluateOffline(data) {
  return request({
    url: '/api/v1/evaluate/offline',
    method: 'post',
    data,
  })
}

/**
 * LLM-as-Judge 评判
 */
export function evaluateJudge(data) {
  return request({
    url: '/api/v1/evaluate/judge',
    method: 'post',
    data,
  })
}

/**
 * 获取评估历史汇总
 */
export function getEvalSummary() {
  return request({
    url: '/api/v1/evaluate/summary',
    method: 'get',
  })
}
