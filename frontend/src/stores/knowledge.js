/**
 * Supply Chain QA - 知识库状态管理
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { uploadDocument, getDocumentList, getKnowledgeStats, deleteDocument } from '@/api/knowledge'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const documents = ref([])
  const stats = ref({
    collection_name: '',
    total_chunks: 0,
    embedding_model: 'BAAI/bge-m3',
    embedding_dimension: 1024,
  })
  const loading = ref(false)
  const uploading = ref(false)

  async function fetchDocuments() {
    loading.value = true
    try {
      const res = await getDocumentList()
      documents.value = res.documents || []
    } catch (e) {
      console.error('获取文档列表失败', e)
      documents.value = []
    } finally {
      loading.value = false
    }
  }

  async function fetchStats() {
    try {
      const res = await getKnowledgeStats()
      stats.value = res
    } catch (e) {
      console.error('获取知识库统计失败', e)
    }
  }

  async function upload(file, securityGroup = 'admin') {
    uploading.value = true
    try {
      const res = await uploadDocument(file, securityGroup)
      await fetchDocuments()
      await fetchStats()
      return res
    } finally {
      uploading.value = false
    }
  }

  async function remove(docId) {
    try {
      await deleteDocument(docId)
      await fetchDocuments()
      await fetchStats()
    } catch (e) {
      console.error('删除文档失败', e)
    }
  }

  return {
    documents,
    stats,
    loading,
    uploading,
    fetchDocuments,
    fetchStats,
    upload,
    remove,
  }
})
