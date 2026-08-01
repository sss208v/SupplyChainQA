/**
 * Knowledge 页面权限显隐组件测试
 *
 * 覆盖 plan 要求：employee 不见上传卡片/删除按钮/一键导入，admin 全见。
 * 通过 mock stores + 真实渲染 v-permission 指令验证。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ElementPlus from "element-plus";
import Knowledge from "./index.vue";
import { permission } from "@/directives/permission";

vi.mock("@/stores/knowledge", () => ({
  useKnowledgeStore: () => ({
    stats: { total_chunks: 0, embedding_model: "", embedding_dimension: 0 },
    documents: [],
    loading: false,
    uploading: false,
    fetchDocuments: vi.fn(),
    fetchStats: vi.fn(),
  }),
}));

vi.mock("@/api/request", () => ({
  default: { post: vi.fn() },
}));

function mountWithLevel(level) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const authStore = useAuthStore();
  authStore.user = {
    username: "tester",
    role: "purchase",
    level,
    department: "采购部",
  };

  const wrapper = mount(Knowledge, {
    global: {
      plugins: [pinia, ElementPlus],
      directives: { permission },
    },
  });
  return { wrapper, authStore };
}

import { useAuthStore } from "@/stores/auth";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("Knowledge 页权限显隐", () => {
  it("employee 看不到上传卡片", async () => {
    const { wrapper } = mountWithLevel("employee");
    await flushPromises();
    expect(wrapper.find(".upload-card").exists()).toBe(false);
  });

  it("employee 看不到一键导入按钮", async () => {
    const { wrapper } = mountWithLevel("employee");
    await flushPromises();
    expect(wrapper.text()).not.toContain("一键导入内置知识库");
  });

  it("employee 文档列表无删除按钮", async () => {
    const { wrapper } = mountWithLevel("employee");
    await flushPromises();
    expect(wrapper.text()).not.toContain("删除");
  });

  it("admin 可见上传卡片与一键导入", async () => {
    const { wrapper } = mountWithLevel("admin");
    await flushPromises();
    expect(wrapper.find(".upload-card").exists()).toBe(true);
    expect(wrapper.text()).toContain("一键导入内置知识库");
  });

  it("manager 可见上传卡片（经理可管理本部门）", async () => {
    const { wrapper } = mountWithLevel("manager");
    await flushPromises();
    expect(wrapper.find(".upload-card").exists()).toBe(true);
  });
});
