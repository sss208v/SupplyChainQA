/**
 * 权限点定义与级别映射
 *
 * 与后端 check_level / require_level 使用同一套语义：
 * - level 排序：admin(3) > manager(2) > employee(1)
 * - 权限点命名如 'knowledge:upload'，与后端 API 校验点同名同义
 *
 * 后端是唯一裁决方；本文件只用于前端展示层控制（隐藏入口），
 * 被绕过时后端仍会返回 403。
 */
export const LEVEL_RANK = {
  admin: 3,
  manager: 2,
  employee: 1,
};

// 权限点 → 最低级别
export const PERMISSIONS = {
  "knowledge:upload": "manager", // 知识库上传（经理及以上）
  "knowledge:delete": "manager", // 知识库删除（经理及以上，限本部门）
  "knowledge:ingest": "admin", // 一键导入内置知识库
  "tool:write": "manager", // 写操作工具（创建工单等）
  "memory:dept:write": "manager", // 部门记忆沉淀
  "user:manage": "admin", // 用户管理
  "evaluate:view": "admin", // RAG 评估页
};

/** 判断级别是否达到最低要求 */
export function hasLevel(level, minLevel) {
  if (level === "admin" || minLevel === "employee") return true;
  return LEVEL_RANK[level] >= LEVEL_RANK[minLevel];
}

/** 判断用户是否拥有某权限点 */
export function can(level, permission) {
  const minLevel = PERMISSIONS[permission];
  if (!minLevel) return false;
  return hasLevel(level, minLevel);
}
