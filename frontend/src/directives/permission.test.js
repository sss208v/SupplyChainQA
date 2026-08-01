/**
 * v-permission 指令单元测试
 *
 * 覆盖：有权限保留元素、无权限移除元素
 */
import { describe, it, expect, beforeEach } from "vitest";
import { createApp, h } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { permission } from "./permission";
import { useAuthStore } from "@/stores/auth";

function mountWithPermission(level, permissionKey) {
  const app = createApp({
    render: () => h("div", { id: "target" }, "content"),
  });
  app.use(createPinia());
  app.directive("permission", permission);
  const el = document.createElement("div");
  document.body.appendChild(el);
  app.mount(el);

  const authStore = useAuthStore();
  authStore.user = { role: "purchase", level };
  // 手动触发指令 mounted 逻辑（模拟 v-permission 绑定）
  const target = document.getElementById("target");
  permission.mounted(target, { value: permissionKey });
  return { app, target, authStore };
}

beforeEach(() => {
  setActivePinia(createPinia());
  document.body.innerHTML = "";
});

describe("v-permission 指令", () => {
  it("manager 拥有 knowledge:upload → 元素保留", () => {
    const { target } = mountWithPermission("manager", "knowledge:upload");
    expect(target.parentNode).not.toBeNull();
  });

  it("employee 无 knowledge:upload → 元素被移除", () => {
    const { target } = mountWithPermission("employee", "knowledge:upload");
    expect(target.parentNode).toBeNull();
  });

  it("admin 拥有 knowledge:ingest → 元素保留", () => {
    const { target } = mountWithPermission("admin", "knowledge:ingest");
    expect(target.parentNode).not.toBeNull();
  });

  it("manager 无 knowledge:ingest → 元素被移除", () => {
    const { target } = mountWithPermission("manager", "knowledge:ingest");
    expect(target.parentNode).toBeNull();
  });
});
