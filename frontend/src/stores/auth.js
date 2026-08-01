import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { login as loginApi } from "@/api/auth";
import {
  can as canCheck,
  hasLevel as hasLevelCheck,
} from "@/utils/permissions";

export const useAuthStore = defineStore("auth", () => {
  const token = ref(localStorage.getItem("token") || "");
  const user = ref(JSON.parse(localStorage.getItem("user") || "null"));

  const isLoggedIn = computed(() => !!token.value);
  const username = computed(() => user.value?.username || "");
  const role = computed(() => user.value?.role || "");
  const level = computed(() => user.value?.level || "employee");
  const department = computed(() => user.value?.department || "");

  /** 判断是否拥有某权限点（展示层控制，后端兜底） */
  function can(permission) {
    return canCheck(level.value, permission);
  }

  /** 判断级别是否达到最低要求 */
  function hasLevel(minLevel) {
    return hasLevelCheck(level.value, minLevel);
  }

  async function login(username, password) {
    const res = await loginApi(username, password);
    // 确保使用后端返回的完整用户数据，不依赖旧 localStorage
    const userData = res.user || {};
    token.value = res.token;
    user.value = {
      username: userData.username || username,
      role: userData.role || "",
      level: userData.level || "employee",
      department: userData.department || "",
    };
    localStorage.setItem("token", res.token);
    localStorage.setItem("user", JSON.stringify(user.value));
    return res;
  }

  /** 从后端拉取最新用户信息（保证 role/level 新鲜，不信任 localStorage 长期缓存） */
  async function refreshUser() {
    try {
      const { getUserInfo } = await import("@/api/auth");
      const me = await getUserInfo();
      user.value = {
        username: me.username || username.value,
        role: me.role || role.value,
        level: me.level || "employee",
        department: me.department || "",
      };
      localStorage.setItem("user", JSON.stringify(user.value));
      return user.value;
    } catch (e) {
      // 刷新失败不阻塞主流程，沿用本地缓存
      return null;
    }
  }

  function logout() {
    token.value = "";
    user.value = null;
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  }

  return {
    token,
    user,
    isLoggedIn,
    username,
    role,
    level,
    department,
    can,
    hasLevel,
    login,
    refreshUser,
    logout,
  };
});
