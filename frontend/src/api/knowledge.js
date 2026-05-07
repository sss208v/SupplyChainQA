/**
 * SmartQA Pro - 知识库 API 接口
 */
import request from './request'

const API_PREFIX = '/api/v1'

/** 上传文档到知识库 */
export function uploadDocument(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request.post(`${API_PREFIX}/knowledge/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000, // 上传大文件需要更长时间
  })
}

/** 获取知识库文档列表 */
export function getDocumentList() {
  return request.get(`${API_PREFIX}/knowledge/list`)
}

/** 获取知识库统计信息 */
export function getKnowledgeStats() {
  return request.get(`${API_PREFIX}/knowledge/stats`)
}

/** 删除文档 */
export function deleteDocument(docId) {
  return request.delete(`${API_PREFIX}/knowledge/${docId}`)
}
