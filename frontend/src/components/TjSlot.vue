<script setup>
/**
 * TjSlot - 插件 UI Slot 组件 (v3.13 M2.2a).
 *
 * 客户视角:
 *   主程序在"可被插件覆盖的位置"显式声明 slot:
 *     <TjSlot name="monitor.step-cell.duration" :duration="step.duration" :step="step">
 *       <!-- 默认内容: 主程序自己的实现 -->
 *       <span>{{ step.duration }}</span>
 *     </TjSlot>
 *
 *   插件在 register() 里注册覆盖组件:
 *     registry.slots.register('monitor.step-cell.duration', MyDurationCell)
 *
 *   主程序行为:
 *     - 插件注册了 → 渲染插件组件, 主程序 props 全部透传给插件
 *     - 未注册 → 渲染 <slot> 默认内容
 *     - 在 manifest.frontend.ui_hidden 列表里 → 连默认内容都不渲染 (返 null)
 *
 * 安全语义:
 *   - 插件覆盖失败 (组件运行时抛错) 被 Vue ErrorBoundary 兜底, 主程序不崩
 *     (errorHandler 已在 main.js 注册)
 *   - 同名 slot 只允许一个 active 插件注册 (与单 active plugin 设计一致)
 *   - 主程序 props 是契约: 主程序不能随意删字段 (升 plugin SDK 主版本才允许)
 *
 * 性能:
 *   - 用 computed 取插件组件 / hidden 状态, 仅当 store state 变化时 re-render
 */
import { computed } from 'vue';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';

const props = defineProps({
  name: { type: String, required: true },
});

const store = usePluginThemeStore();

const isHidden = computed(() => store.isSlotHidden(props.name));
const pluginComponent = computed(() => store.getSlotComponent(props.name));
</script>

<template>
  <template v-if="isHidden">
    <!-- M2.2a: ui_hidden 列表命中, 连默认内容都不渲染 -->
  </template>
  <template v-else-if="pluginComponent">
    <component :is="pluginComponent" v-bind="$attrs">
      <!-- 把 <TjSlot> 的默认 slot 透传给插件组件, 让插件能引用主程序默认实现 -->
      <slot />
    </component>
  </template>
  <template v-else>
    <slot />
  </template>
</template>
