/**
 * 权限点判定单元测试
 *
 * 覆盖：级别矩阵（admin/manager/employee）、权限点映射、未知权限点
 */
import { describe, it, expect } from "vitest";
import { hasLevel, can, PERMISSIONS } from "./permissions";

describe("hasLevel", () => {
  it("admin 达到所有级别", () => {
    expect(hasLevel("admin", "admin")).toBe(true);
    expect(hasLevel("admin", "manager")).toBe(true);
    expect(hasLevel("admin", "employee")).toBe(true);
  });

  it("manager 达到 manager 与 employee，达不到 admin", () => {
    expect(hasLevel("manager", "manager")).toBe(true);
    expect(hasLevel("manager", "employee")).toBe(true);
    expect(hasLevel("manager", "admin")).toBe(false);
  });

  it("employee 仅达到 employee", () => {
    expect(hasLevel("employee", "employee")).toBe(true);
    expect(hasLevel("employee", "manager")).toBe(false);
    expect(hasLevel("employee", "admin")).toBe(false);
  });
});

describe("can", () => {
  it("knowledge:upload 需 manager 及以上", () => {
    expect(can("admin", "knowledge:upload")).toBe(true);
    expect(can("manager", "knowledge:upload")).toBe(true);
    expect(can("employee", "knowledge:upload")).toBe(false);
  });

  it("knowledge:ingest 仅 admin", () => {
    expect(can("admin", "knowledge:ingest")).toBe(true);
    expect(can("manager", "knowledge:ingest")).toBe(false);
    expect(can("employee", "knowledge:ingest")).toBe(false);
  });

  it("tool:write 需 manager 及以上", () => {
    expect(can("manager", "tool:write")).toBe(true);
    expect(can("employee", "tool:write")).toBe(false);
  });

  it("evaluate:view 仅 admin", () => {
    expect(can("admin", "evaluate:view")).toBe(true);
    expect(can("manager", "evaluate:view")).toBe(false);
  });

  it("未知权限点返回 false", () => {
    expect(can("admin", "unknown:perm")).toBe(false);
    expect(can("admin", undefined)).toBe(false);
  });
});

describe("PERMISSIONS 完整性", () => {
  it("所有权限点均有合法的最低级别", () => {
    for (const minLevel of Object.values(PERMISSIONS)) {
      expect(["admin", "manager", "employee"]).toContain(minLevel);
    }
  });
});
