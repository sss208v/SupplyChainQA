import { createRouter, createWebHistory } from "vue-router";
import { hasLevel } from "@/utils/permissions";

const routes = [
  {
    path: "/login",
    name: "Login",
    component: () => import("@/views/Login/index.vue"),
    meta: { title: "登录", public: true },
  },
  {
    path: "/",
    redirect: "/dashboard",
  },
  {
    path: "/dashboard",
    name: "Dashboard",
    component: () => import("@/views/Dashboard/index.vue"),
    meta: { title: "系统概览" },
  },
  {
    path: "/chat",
    name: "Chat",
    component: () => import("@/views/Chat/index.vue"),
    meta: { title: "智能对话" },
  },
  {
    path: "/knowledge",
    name: "Knowledge",
    component: () => import("@/views/Knowledge/index.vue"),
    meta: { title: "知识库管理" },
  },
  {
    path: "/tools",
    name: "Tools",
    component: () => import("@/views/Tools/index.vue"),
    meta: { title: "工具管理" },
  },
  {
    path: "/evaluate",
    name: "Evaluate",
    component: () => import("@/views/Evaluate/index.vue"),
    meta: { title: "RAG 评估", level: "admin" },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 路由守卫：登录校验 + 级别校验（meta.level）
router.beforeEach((to) => {
  document.title = `${to.meta.title || "供应链助手"} - 供应链智能助手`;

  // 未登录且非公开页面，跳转登录
  const token = localStorage.getItem("token");
  if (!token && !to.meta.public) {
    return "/login";
  }

  // 页面级别权限：meta.level 要求的最低级别，不足则回概览页
  if (to.meta.level) {
    const user = JSON.parse(localStorage.getItem("user") || "null");
    const level = user?.level || "employee";
    if (!hasLevel(level, to.meta.level)) {
      return "/dashboard";
    }
  }
});

export default router;
