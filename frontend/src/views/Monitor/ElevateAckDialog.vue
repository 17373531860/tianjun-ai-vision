<template>
  <!-- v3.23 借管理员密码授权确认: 无 ack 权限的操作员点确认被 403 → 弹此窗 -->
  <!-- 只校验一次管理员账密 + 权限解除阻塞, 不创建登录会话、不改当前登录身份 -->
  <!-- 取消 = 关本窗回到人工确认覆盖层 (pendingAck 未清, 覆盖层仍在) -->
  <div
    class="absolute inset-0 z-[70] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
    @click.stop
  >
    <div class="w-full max-w-md bg-slate-900 border-2 border-cyan-500 rounded-xl shadow-2xl flex flex-col overflow-hidden">
      <div class="px-5 py-3 bg-gradient-to-r from-cyan-700 to-cyan-900 flex items-center gap-3">
        <el-icon :size="26" class="text-cyan-100"><Lock /></el-icon>
        <div class="flex-1">
          <div class="text-white font-bold text-lg">借管理员密码授权确认</div>
          <div class="text-cyan-100 text-xs">当前账号无人工确认权限，请管理员授权本次确认（不改变当前登录身份）</div>
        </div>
        <span class="bg-slate-900/60 px-2 py-0.5 rounded text-cyan-100 text-xs font-bold">
          工位 {{ dialog.channel + 1 }}
        </span>
      </div>

      <div class="px-5 py-4 space-y-3">
        <el-input
          v-model="dialog.username"
          placeholder="管理员账号"
          size="large"
          clearable
          @keyup.enter="$emit('submit')"
        >
          <template #prefix><el-icon><User /></el-icon></template>
        </el-input>
        <el-input
          v-model="dialog.password"
          type="password"
          placeholder="管理员密码"
          size="large"
          show-password
          @keyup.enter="$emit('submit')"
        >
          <template #prefix><el-icon><Lock /></el-icon></template>
        </el-input>
      </div>

      <div class="px-5 py-4 bg-slate-950 border-t border-slate-800 flex items-center justify-end gap-3">
        <el-button size="large" @click="dialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          size="large"
          :loading="dialog.submitting"
          @click="$emit('submit')"
        >
          授权并确认
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { Lock, User } from '@element-plus/icons-vue';

// elevateDialog 的 reactive 对象整体传入; username/password/visible 直接双向读写该对象
// (与原内联实现完全同构, 保持行为零差异)
defineProps({
  dialog: { type: Object, required: true },
});

defineEmits(['submit']);
</script>
