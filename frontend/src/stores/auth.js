import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as loginApi } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))

  const isLoggedIn = computed(() => !!token.value)
  const username = computed(() => user.value?.username || '')
  const role = computed(() => user.value?.role || '')
  const department = computed(() => user.value?.department || '')

  async function login(username, password) {
    const res = await loginApi(username, password)
    // 确保使用后端返回的完整用户数据，不依赖旧 localStorage
    const userData = res.user || {}
    token.value = res.token
    user.value = {
      username: userData.username || username,
      role: userData.role || '',
      department: userData.department || '',
    }
    localStorage.setItem('token', res.token)
    localStorage.setItem('user', JSON.stringify(user.value))
    return res
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  return {
    token, user, isLoggedIn, username, role, department,
    login, logout,
  }
})
