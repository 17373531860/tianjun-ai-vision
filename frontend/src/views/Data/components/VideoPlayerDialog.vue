<template>
  <el-dialog v-model="visible" title="视频播放" width="70%" destroy-on-close :close-on-click-modal="false" class="dark-dialog">
    <!-- v3.54: 长会话录像分段选择 (单段/周期/步骤视频不显示) -->
    <div v-if="segments.length > 1" class="flex items-center gap-2 mb-2 flex-wrap" data-testid="video-segment-bar">
      <span class="text-xs text-gray-500 flex-shrink-0">录像分段：</span>
      <el-button
        v-for="(seg, idx) in segments"
        :key="seg.video_uuid"
        size="small"
        :type="idx === activeSegment ? 'primary' : 'default'"
        :disabled="!seg.file_exists"
        :data-testid="`video-segment-${idx + 1}`"
        @click="switchSegment(idx)"
      >
        第{{ idx + 1 }}段 {{ segTimeLabel(seg) }}
      </el-button>
    </div>
    <div class="video-container">
      <video v-if="currentVideoUrl" ref="videoPlayerRef" :src="currentVideoUrl" controls autoplay class="w-full" style="max-height: 70vh; background: #000; border-radius: 8px;" @error="handleVideoError" @loadedmetadata="applyPlaybackRate">
        您的浏览器不支持视频播放
      </video>
      <div v-else class="text-center text-gray-500 py-10">视频加载中...</div>
    </div>
    <template #footer>
      <div class="flex items-center justify-between">
        <span class="text-gray-500 text-xs">提示：视频首次加载可能需要几秒钟进行格式转换</span>
        <div class="flex items-center gap-3">
          <!-- v3.48.1: 播放倍速 -->
          <el-radio-group v-model="videoPlaybackRate" size="small" @change="applyPlaybackRate" data-testid="video-speed-group">
            <el-radio-button v-for="r in [0.5, 1, 1.5, 2, 4]" :key="r" :label="r">{{ r }}x</el-radio-button>
          </el-radio-group>
          <!-- v3.48.1: 下载录像 -->
          <el-button size="small" type="primary" plain @click="downloadCurrentVideo" data-testid="video-download-btn">
            <el-icon class="mr-1"><Download /></el-icon>下载录像
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
// 录像回放弹窗（会话/周期/步骤视频共用）。父视图拿 ref 调 open(url) 打开;
// 长会话分段录像调 openSegments(list, urlOf) 打开并显示分段切换条。
import { ref } from 'vue';
import { Download } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { dbg } from '@/utils/debug';

const visible = ref(false);
const currentVideoUrl = ref('');
// v3.54: 分段列表 [{video_uuid, start_time, end_time, file_exists}] + url 构造器
const segments = ref([]);
const activeSegment = ref(0);
let segmentUrlOf = null;

const open = (url) => {
  segments.value = [];
  activeSegment.value = 0;
  currentVideoUrl.value = url;
  visible.value = true;
};

const openSegments = (segList, urlOf) => {
  segments.value = segList || [];
  segmentUrlOf = urlOf;
  // 默认播首个存在的段
  const first = segments.value.findIndex((s) => s.file_exists);
  activeSegment.value = first >= 0 ? first : 0;
  const seg = segments.value[activeSegment.value];
  currentVideoUrl.value = seg ? urlOf(seg.video_uuid) : '';
  visible.value = true;
};

const switchSegment = (idx) => {
  if (idx === activeSegment.value || !segmentUrlOf) return;
  const seg = segments.value[idx];
  if (!seg) return;
  activeSegment.value = idx;
  currentVideoUrl.value = segmentUrlOf(seg.video_uuid);
  dbg('data.query', '切换录像分段', `第${idx + 1}段 ${seg.video_uuid}`);
};

const segTimeLabel = (seg) => {
  const hm = (t) => (t ? t.slice(11, 16) : '');
  const a = hm(seg.start_time);
  const b = hm(seg.end_time);
  if (a && b) return `${a}-${b}`;
  if (a) return `${a} 起`;
  return '';
};

// 视频播放错误处理
const handleVideoError = (e) => {
  console.error('视频播放错误:', e);
  ElMessage.error('视频加载失败：文件可能损坏或格式转换未完成，可点「下载录像」用本地播放器查看');
};

// v3.48.1: 播放倍速（记住选择, 换视频不重置）
const videoPlayerRef = ref(null);
const videoPlaybackRate = ref(1);
const applyPlaybackRate = () => {
  if (videoPlayerRef.value) {
    videoPlayerRef.value.playbackRate = Number(videoPlaybackRate.value) || 1;
  }
};

// v3.48.1: 下载当前录像（走同一 /data/videos/{id} 端点, FileResponse 带文件名）
const downloadCurrentVideo = () => {
  if (!currentVideoUrl.value) return;
  dbg('data.query', '下载录像', currentVideoUrl.value);
  const a = document.createElement('a');
  a.href = currentVideoUrl.value;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};

defineExpose({ open, openSegments });
</script>
