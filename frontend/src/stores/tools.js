/**
 * Supply Chain QA - 工具调用状态管理
 *
 * 管理工具注册表的可见性状态和工具调用记录：
 * - 工具列表（按角色过滤后）
 * - 工具调用历史
 * - 活跃工具的测试状态
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getToolList, callTool, getToolSchema } from '@/api/tool'

export const useToolsStore = defineStore('tools', () => {
  // ---- 状态 ----
  const tools = ref([])               // 当前用户可用的工具列表
  const activeTool = ref(null)        // 当前选中的工具名
  const toolSchemas = ref({})         // 工具参数 Schema 缓存
  const callHistory = ref([])          // 工具调用历史
  const testLoading = ref(false)       // 测试执行中
  const testResult = ref(null)         // 测试结果

  // ---- Actions ----

  /**
   * 获取当前用户可用的工具列表
   */
  async function fetchTools() {
    try {
      const res = await getToolList()
      tools.value = res.tools || []
      if (tools.value.length && !activeTool.value) {
        activeTool.value = tools.value[0].name
      }
    } catch (e) {
      console.error('[ToolsStore] 获取工具列表失败', e)
    }
  }

  /**
   * 获取指定工具的 Schema
   */
  async function fetchSchema(toolName) {
    if (toolSchemas.value[toolName]) return toolSchemas.value[toolName]
    try {
      const res = await getToolSchema(toolName)
      toolSchemas.value[toolName] = res
      return res
    } catch (e) {
      console.error('[ToolsStore] 获取 Schema 失败', e)
    }
  }

  /**
   * 直接调用工具（测试接口）
   */
  async function testTool(query, toolNames, sessionId) {
    testLoading.value = true
    testResult.value = null
    try {
      const res = await callTool({ query, tool_names: toolNames, session_id: sessionId })
      testResult.value = {
        answer: res.answer,
        tool_calls: res.tool_calls || [],
        iterations: res.iterations,
        error: null,
      }
      // 记录到历史（上限 100 条，防内存泄漏）
      callHistory.value.unshift({
        id: Date.now(),
        tool: toolNames?.[0] || 'mixed',
        query,
        result: testResult.value,
        timestamp: new Date().toLocaleTimeString(),
      })
      if (callHistory.value.length > 100) {
        callHistory.value = callHistory.value.slice(0, 100)
      }
      return res
    } catch (e) {
      testResult.value = { answer: '', tool_calls: [], error: e.message }
    } finally {
      testLoading.value = false
    }
  }

  /**
   * 清除测试结果
   */
  function clearTestResult() {
    testResult.value = null
  }

  return {
    tools,
    activeTool,
    toolSchemas,
    callHistory,
    testLoading,
    testResult,
    fetchTools,
    fetchSchema,
    testTool,
    clearTestResult,
  }
})
