<template>
      <el-card shadow="never" class="bg-slate-800 border-slate-700">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Microphone /></el-icon>
              <span class="font-bold text-white">语音播报</span>
            </div>
            <el-switch v-model="systemStore.detection.voiceEnabled" active-text="启用" @change="saveVoiceSettings" />
          </div>
        </template>
        
        <div class="space-y-4">
          <div class="p-3 bg-slate-900 rounded border border-slate-800">
            <div class="text-gray-300 mb-2">播报音量</div>
            <el-slider v-model="systemStore.detection.voiceVolume" :min="0" :step="0.1" :disabled="!systemStore.detection.voiceEnabled" @change="saveVoiceSettings" />
          </div>
          <div class="p-3 bg-slate-900 rounded border border-slate-800">
            <div class="text-gray-400 text-xs mb-2">
              OK 播报"合格"，NG 播报"不合格"。若显示设置中"NG弹窗显示原因"已开启，NG 原因也会一并播报。
            </div>
            <div class="flex gap-3">
              <el-button size="small" :disabled="!systemStore.detection.voiceEnabled" @click="testVoice('合格')">测试"合格"</el-button>
              <el-button size="small" type="danger" :disabled="!systemStore.detection.voiceEnabled" @click="testVoice('不合格，缺少步骤')">测试"不合格"</el-button>
            </div>
          </div>
        </div>
      </el-card>
</template>

<script setup>
import { ElMessage } from 'element-plus';
import { Microphone } from '@element-plus/icons-vue';
import { useSystemStore } from '@/store/useSystemStore';

const systemStore = useSystemStore();

const saveVoiceSettings = () => {
  systemStore.saveDetectionSettings();
};

const testVoice = (text) => {
  if (!window.speechSynthesis) {
    ElMessage.error('当前浏览器不支持语音合成');
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'zh-CN';
  utterance.volume = systemStore.detection.voiceVolume ?? 1.0;
  utterance.rate = 1.1;
  utterance.onerror = (e) => {
    console.error('语音合成错误:', e);
    ElMessage.error('语音播报失败，请检查系统是否安装了 espeak-ng（sudo apt install espeak-ng）');
  };
  window.speechSynthesis.speak(utterance);
};
</script>
