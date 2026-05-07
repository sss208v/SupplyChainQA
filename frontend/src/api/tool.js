/**
 * SmartQA Pro - 工具 API 接口
 */
import request from './request'

const API_PREFIX = '/api/v1'

/** 获取工具列表 */
export function getToolList() {
  return request.get(`${API_PREFIX}/tools/list`)
}

/** 调用工具（测试接口） */
export function callTool(data) {
  return request.post(`${API_PREFIX}/tools/call`, data)
}

/** 获取工具参数Schema */
export function getToolSchema(toolName) {
  return request.get(`${API_PREFIX}/tools/${toolName}/schema`)
}
