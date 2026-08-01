/**
 * v-permission 自定义指令：按权限点控制元素显隐
 *
 * 用法：
 *   <el-button v-permission="'knowledge:upload'">上传</el-button>
 *   <div v-permission="'tool:write'">写工具区域</div>
 *
 * 无权限时从 DOM 移除元素（展示层控制，后端仍会兜底校验）。
 */
import { useAuthStore } from "@/stores/auth";

export const permission = {
  mounted(el, binding) {
    const authStore = useAuthStore();
    if (!authStore.can(binding.value)) {
      el.parentNode?.removeChild(el);
    }
  },
};
