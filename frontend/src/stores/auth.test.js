/**
 * Auth Store 权限判定单元测试
 *
 * 覆盖 plan 要求：auth store can() 判定——不同 level 下权限点判断正确，
 * level 持久化（localStorage 初始化、login 保存、refreshUser 拉新）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "./auth";

vi.mock("@/api/auth", () => ({
  login: vi.fn(async (username, password) => ({
    token: "t-123",
    user: {
      username,
      role: "purchase",
      level: "manager",
      department: "采购部",
    },
  })),
  getUserInfo: vi.fn(async () => ({
    id: 1,
    username: "tester",
    role: "purchase",
    level: "admin",
    department: "采购部",
    is_active: true,
    created_at: null,
  })),
}));

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

describe("AuthStore can() 权限判定", () => {
  it("employee 不能上传知识库", () => {
    const store = useAuthStore();
    store.user = { role: "purchase", level: "employee" };
    expect(store.can("knowledge:upload")).toBe(false);
    expect(store.can("tool:write")).toBe(false);
  });

  it("manager 可上传但不可一键导入", () => {
    const store = useAuthStore();
    store.user = { role: "purchase", level: "manager" };
    expect(store.can("knowledge:upload")).toBe(true);
    expect(store.can("knowledge:ingest")).toBe(false);
  });

  it("admin 拥有全部权限点", () => {
    const store = useAuthStore();
    store.user = { role: "admin", level: "admin" };
    expect(store.can("knowledge:upload")).toBe(true);
    expect(store.can("knowledge:ingest")).toBe(true);
    expect(store.can("tool:write")).toBe(true);
    expect(store.can("evaluate:view")).toBe(true);
  });

  it("hasLevel 矩阵正确", () => {
    const store = useAuthStore();
    store.user = { role: "purchase", level: "manager" };
    expect(store.hasLevel("employee")).toBe(true);
    expect(store.hasLevel("manager")).toBe(true);
    expect(store.hasLevel("admin")).toBe(false);
  });
});

describe("AuthStore level 持久化", () => {
  it("login 保存后端返回的 level", async () => {
    const store = useAuthStore();
    await store.login("tester", "pass123456");
    expect(store.level).toBe("manager");
    expect(JSON.parse(localStorage.getItem("user")).level).toBe("manager");
  });

  it("refreshUser 从后端拉取最新 level", async () => {
    const store = useAuthStore();
    store.user = { role: "purchase", level: "manager" };
    await store.refreshUser();
    expect(store.level).toBe("admin");
    expect(localStorage.getItem("user")).toContain('"level":"admin"');
  });
});
