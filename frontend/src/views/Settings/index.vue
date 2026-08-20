<template>
  <TjSlot name="settings.layout.body">
  <div class="p-6 h-full overflow-y-auto">
    <h2 class="text-2xl font-bold mb-6 border-l-4 border-tech-blue pl-3 text-white">系统设置</h2>

    <el-tabs type="border-card" class="bg-gray-800 border-gray-700">
      
      <!-- Display Settings Tab -->
      <el-tab-pane label="显示设置">
        <DisplaySettingsTab />
      </el-tab-pane>

      <!-- Detection Box Settings Tab -->
      <el-tab-pane label="检测框设置">
        <DetectionBoxSettingsTab />
      </el-tab-pane>

      <!-- Performance Settings Tab -->
      <el-tab-pane label="性能设置">
        <PerformanceSettingsTab />
      </el-tab-pane>

      <!-- 轮询间隔 Tab -->
      <el-tab-pane label="轮询间隔">
        <PollingSettingsTab />
      </el-tab-pane>

      <!-- 触发中心 (RFC 14) -->
      <el-tab-pane label="触发中心" lazy>
        <div class="p-4">
          <TriggerPanel />
        </div>
      </el-tab-pane>

      <!-- Plugin Management Tab -->
      <el-tab-pane label="插件管理">
        <div class="space-y-6 p-4">
          <el-alert
            title="更换或激活插件后，需要重启应用才能让插件代码生效。"
            type="warning"
            :closable="false"
            show-icon
          />

          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-purple-400"><Lightning /></el-icon>
                  <span class="font-bold text-white">插件管理</span>
                  <el-tag v-if="pluginStore.activeCustomerCode" type="success" size="small">当前 active: {{ pluginStore.activeCustomerCode }}</el-tag>
                  <el-tag v-if="pluginStore.licenseMismatch" type="danger" size="small">license 与激活插件 customer_code 不一致</el-tag>
                </div>
                <el-button size="small" :loading="pluginStore.loading" @click="pluginStore.fetchAll()">
                  <el-icon class="mr-1"><Refresh /></el-icon>刷新
                </el-button>
              </div>
            </template>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">当前 License 客户码</div>
                <div class="text-cyan-300 font-mono">{{ pluginStore.licenseCustomer || '未缓存' }}</div>
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">已安装插件</div>
                <div class="text-white text-lg font-bold">{{ pluginStore.items.length }}</div>
              </div>
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-xs text-gray-500 mb-1">安装包</div>
                <el-upload
                  :auto-upload="false"
                  :show-file-list="false"
                  accept=".tjvplugin"
                  :on-change="onPluginFileChange"
                >
                  <el-button type="primary" :loading="pluginStore.uploading">上传 .tjvplugin</el-button>
                </el-upload>
              </div>
            </div>

            <el-table :data="pluginStore.items" stripe size="small" class="bg-transparent" v-loading="pluginStore.loading">
              <el-table-column prop="name" label="插件" min-width="160">
                <template #default="{ row }">
                  <div class="font-bold text-white">{{ row.name }}</div>
                  <div class="text-xs text-gray-500 font-mono">{{ row.customer_code }}</div>
                </template>
              </el-table-column>
              <el-table-column prop="plugin_version" label="版本" width="110" />
              <el-table-column label="状态" width="150">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                    {{ row.is_active ? 'active' : row.status }}
                  </el-tag>
                  <div class="text-xs text-gray-500 mt-1">{{ row.runtime_status || 'stopped' }}</div>
                </template>
              </el-table-column>
              <el-table-column label="最近错误" min-width="180">
                <template #default="{ row }">
                  <span v-if="row.last_error_code" class="text-red-400">
                    {{ row.last_error_code }}：{{ row.last_error_message }}
                  </span>
                  <span v-else class="text-gray-500">无</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="230" fixed="right">
                <template #default="{ row }">
                  <el-button v-if="!row.is_active" size="small" type="success" @click="activateInstalledPlugin(row)">
                    激活
                  </el-button>
                  <el-button v-else size="small" type="warning" @click="deactivateInstalledPlugin(row)">
                    停用
                  </el-button>
                  <el-button size="small" type="danger" @click="removeInstalledPlugin(row)">
                    卸载
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="!pluginStore.hasAny && !pluginStore.loading" class="text-center text-gray-500 text-sm py-6">
              暂无插件，请上传已签名的 .tjvplugin 安装包。
            </div>
            <div v-if="pluginStore.lastError" class="mt-3 text-xs text-red-400">
              {{ pluginStore.lastError }}
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 账号鉴权 Tab (v3.10.0 用户系统) -->
      <el-tab-pane label="账号鉴权">
        <AuthPanel />
      </el-tab-pane>

      <!-- 流水线串行 / 工位组互通已迁到「工位与输入源」页（同属多工位协调域） -->

      <el-tab-pane label="包装箱结算">
        <PackagingFlowPanel />
      </el-tab-pane>

      <!-- 调试设置 Tab: 仅开发者模式可见 (Navbar 齿轮 → 开发者模式 → 密码), 与多工位同款门控 -->
      <el-tab-pane v-if="store.developerMode" label="调试设置">
        <DebugPanel />
      </el-tab-pane>

      <!-- v3.13 M2.2b: 客户插件可注入 Tab. 通过 manifest.frontend.settings_tabs 声明 -->
      <el-tab-pane
        v-for="tab in pluginSettingsTabs"
        :key="tab.key"
        :label="tab.label"
      >
        <TjSlot
          :name="`settings.tab.${tab.key}`"
          :tab="tab"
        >
          <component v-if="tab.component" :is="tab.component" />
          <div v-else class="text-gray-400 p-4">
            插件未提供 Tab 组件 (manifest.frontend.settings_tabs[].component)
          </div>
        </TjSlot>
      </el-tab-pane>
    </el-tabs>
  </div>
  </TjSlot>
</template>

<script setup>
import TjSlot from '@/components/TjSlot.vue';
import { computed, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { usePluginStore } from '@/store/usePluginStore';
import { usePluginThemeStore } from '@/store/usePluginThemeStore';
import { Refresh, Lightning } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import AuthPanel from './AuthPanel.vue';
import PackagingFlowPanel from './PackagingFlowPanel.vue';
import DebugPanel from './DebugPanel.vue';
import TriggerPanel from './TriggerPanel.vue';
import DisplaySettingsTab from './DisplaySettingsTab.vue';
import DetectionBoxSettingsTab from './DetectionBoxSettingsTab.vue';
import PerformanceSettingsTab from './PerformanceSettingsTab.vue';
import PollingSettingsTab from './PollingSettingsTab.vue';
import { dbg, dbgErr } from '@/utils/debug';

const store = useSystemStore();
const pluginStore = usePluginStore();
const pluginThemeStore = usePluginThemeStore();

// v3.13 M2.2b: 客户插件注入的 Settings tab 列表
const pluginSettingsTabs = computed(() => pluginThemeStore.settingsTabs || []);

async function loadPlugins() {
  try {
    await pluginStore.fetchAll();
  } catch {
    ElMessage.error('加载插件列表失败：' + (pluginStore.lastError || '未知错误'));
  }
}

async function onPluginFileChange(uploadFile) {
  const raw = uploadFile?.raw;
  if (!raw) return;
  dbg('settings.ops', '上传安装插件', `file=${raw?.name ?? ''}`);
  try {
    await pluginStore.install(raw);
    ElMessage.success('插件安装成功，激活后重启生效');
  } catch {
    dbg('settings.ops', '上传安装插件 [失败]', `${pluginStore.lastError ?? '未知错误'}`);
    ElMessage.error(`插件安装失败：${pluginStore.lastError || '未知错误'}`);
  }
}

async function activateInstalledPlugin(row) {
  dbg('settings.ops', '激活插件', `code=${row?.customer_code ?? ''}`);
  try {
    await pluginStore.activate(row.customer_code);
    ElMessage.success('插件已激活，重启后生效');
  } catch (e) {
    dbgErr('settings.ops', '激活插件', e);
    ElMessage.error('激活失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

async function deactivateInstalledPlugin(row) {
  try {
    await pluginStore.deactivate(row.customer_code);
    ElMessage.success('插件已停用，重启后生效');
  } catch (e) {
    ElMessage.error('停用失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

async function removeInstalledPlugin(row) {
  await ElMessageBox.confirm(
    `确认卸载插件 ${row.name}？插件业务数据表会保留。`,
    '卸载插件',
    { type: 'warning' }
  );
  try {
    await pluginStore.remove(row.customer_code);
    ElMessage.success('插件已卸载');
  } catch (e) {
    ElMessage.error('卸载失败：' + (e?.response?.data?.detail?.message || e?.message || ''));
  }
}

onMounted(() => {
  loadPlugins();
});
</script>
