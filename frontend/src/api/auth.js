import request from './request'

export function login(username, password) {
  return request.post('/api/v1/auth/login', { username, password })
}

export function getUserInfo() {
  return request.get('/api/v1/auth/me')
}
