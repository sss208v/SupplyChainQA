import axios from "axios";

const request = axios.create({
  baseURL: "",
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

// 请求拦截器：自动添加 token
request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器
request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response?.status === 401) {
      // Token 过期，清除登录状态
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      window.location.href = "/login";
    }
    if (error.response?.status === 403) {
      // 权限不足：静默刷新用户信息（保证 role/level 新鲜，与后端 RBAC 同步），
      // 页面级隐藏由 v-permission/路由守卫负责，这里仅兜底提示
      refreshUserSilently();
      const msg = error.response?.data?.detail || "权限不足，请联系管理员";
      console.error("Permission denied:", msg);
      return Promise.reject(new Error(msg));
    }
    const msg = error.response?.data?.detail || error.message || "请求失败";
    console.error("API Error:", msg);
    return Promise.reject(new Error(msg));
  }
);

/**
 * 静默刷新用户信息：403 时拉取 /auth/me 更新 localStorage
 * 保证前端 level 与后端一致，避免因 localStorage 过期导致展示层判断失准
 */
function refreshUserSilently() {
  const token = localStorage.getItem("token");
  if (!token) return;
  fetch("/api/v1/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  })
    .then((r) => (r.ok ? r.json() : null))
    .then((me) => {
      if (me?.level) {
        localStorage.setItem("user", JSON.stringify(me));
      }
    })
    .catch(() => {});
}

export default request;
