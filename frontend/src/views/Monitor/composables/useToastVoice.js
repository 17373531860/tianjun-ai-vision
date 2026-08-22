/**
 * useToastVoice — 监控页提示框 + TTS 语音域（巨石重构阶段1④，v3.54.1 逐行等价移植）
 *
 * 职责：单工位/多工位事件提示框（按项目级/通道级 detection 配置取色/字号/位置/时长）、
 * TTS 语音队列（顺序播报不互斥、去重合并、上限丢最旧）。
 *
 * ctx 依赖（index.vue 注入）：
 *   channelCount — ref（>1 时按通道取 detection 配置）
 *   kioskMode    — ref（kiosk 只读副屏禁止播报/弹提示）
 *   systemStore  — pinia store 实例（detection / getChannelDetection / display）
 */
import { ref, computed } from 'vue';
import { CircleCheck, CircleClose, Warning } from '@element-plus/icons-vue';
import { dbg } from '@/utils/debug';

export function useToastVoice(ctx) {
  const { channelCount, kioskMode, systemStore } = ctx;

  const activeToasts = ref([]);
  const multiActiveToasts = ref({});
  let toastIdCounter = 0;

  const getToastConfig = (toastId, ch = null) => {
    const det = (ch != null && channelCount.value > 1) ? systemStore.getChannelDetection(ch) : systemStore.detection;
    if (toastId === 'ok') return det.toasts.ok;
    if (toastId === 'ng') return det.toasts.ng;
    if (toastId === 'scan') return det.toasts.scan;
    if (toastId === 'warn_no_barcode') return det.toasts.warn_no_barcode;
    const customToast = (det.customToasts || []).find(t => t.id === toastId);
    if (customToast) return customToast;
    return det.toasts.ok;
  };

  // 提示框位置样式
  const getPositionClass = (position) => {
    switch (position) {
      case 'top-right': return 'top-24 right-8';
      case 'top-left': return 'top-24 left-4';
      case 'bottom-right': return 'bottom-8 right-8';
      case 'bottom-left': return 'bottom-8 left-4';
      case 'center': return 'top-24 left-[29%] -translate-x-1/2';
      default: return 'top-24 right-8';
    }
  };

  // 默认位置（兼容旧代码）
  const toastPositionClass = computed(() => {
    return getPositionClass(systemStore.detection.toasts.ok.position);
  });

  // TTS voice announcement — queue mode: voices play sequentially, never cancel each other
  const speechQueue = [];
  let isSpeaking = false;
  // D5: 语音队列上限。TTS 卡住/语速慢时队列会越堆越多, 高 NG 率刷屏更明显。
  // 设 20 远高于正常节拍(正常队列 0~1); 到顶丢最旧, 且合并连续相同播报。
  const MAX_SPEECH_QUEUE = 20;
  const _playNext = () => {
    if (!speechQueue.length) { isSpeaking = false; return; }
    isSpeaking = true;
    const { text, volume } = speechQueue.shift();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'zh-CN';
    utterance.volume = volume;
    utterance.rate = 1.1;
    utterance.onend = () => _playNext();
    utterance.onerror = () => _playNext();
    window.speechSynthesis.speak(utterance);
  };
  const speak = (text, ch = null) => {
    const det = (ch != null && channelCount.value > 1) ? systemStore.getChannelDetection(ch) : systemStore.detection;
    if (!det.voiceEnabled || !window.speechSynthesis) return;
    // 合并连续相同播报(常见: 连续 NG 刷屏), 队尾相同就不重复入队
    const last = speechQueue[speechQueue.length - 1];
    if (last && last.text === text) return;
    // 上限保护: 队列积压(TTS 卡住)时丢最旧, 防无限涨
    if (speechQueue.length >= MAX_SPEECH_QUEUE) speechQueue.shift();
    speechQueue.push({ text, volume: det.voiceVolume ?? 1.0 });
    if (!isSpeaking) _playNext();
  };

  // 显示提示框（新版：根据 toast_id 获取配置）
  const showToastById = (toastId, eventName, reason = '') => {
    dbg('monitor.events', `事件提示 [${toastId}] ${eventName || ''}`, reason || '');
    const config = getToastConfig(toastId);

    const icons = {
      ok: CircleCheck,
      ng: CircleClose,
      custom: Warning
    };

    let subtitle = config.subText || '';
    if (toastId === 'ng' && reason && systemStore.detection.showNgReason) {
      subtitle = reason;
    }

    const toast = {
      id: ++toastIdCounter,
      toastId,
      title: config.text || eventName,
      subtitle: subtitle,
      color: config.color,
      fontSize: config.fontSize,
      position: config.position,
      icon: toastId === 'ok' ? icons.ok : (toastId === 'ng' ? icons.ng : icons.custom)
    };

    activeToasts.value.push(toast);

    // Voice announcement
    if (toastId === 'ok') {
      speak('合格');
    } else if (toastId === 'ng') {
      const showReason = systemStore.detection.showNgReason && reason;
      speak(showReason ? `不合格，${reason}` : '不合格');
    } else {
      speak(config.text || eventName || '事件触发');
    }

    // 自动移除
    setTimeout(() => {
      const idx = activeToasts.value.findIndex(t => t.id === toast.id);
      if (idx > -1) {
        activeToasts.value.splice(idx, 1);
      }
    }, config.duration * 1000);
  };

  // 兼容旧版显示提示框
  const showToast = (type, title, subtitle = '') => {
    const toastId = type === 'ok' ? 'ok' : (type === 'ng' ? 'ng' : 'ok');
    const config = getToastConfig(toastId);

    const icons = {
      ok: CircleCheck,
      ng: CircleClose,
      custom: Warning
    };

    const toast = {
      id: ++toastIdCounter,
      toastId,
      title: title || config.text,
      subtitle: config.subText || '',
      color: config.color,
      fontSize: config.fontSize,
      position: config.position,
      icon: icons[type] || icons.custom
    };

    activeToasts.value.push(toast);

    // 自动移除
    setTimeout(() => {
      const idx = activeToasts.value.findIndex(t => t.id === toast.id);
      if (idx > -1) {
        activeToasts.value.splice(idx, 1);
      }
    }, config.duration * 1000);
  };

  const getMultiPositionClass = (position) => {
    switch (position) {
      case 'top-right': return 'top-2 right-2';
      case 'top-left': return 'top-2 left-2';
      case 'bottom-right': return 'bottom-2 right-2';
      case 'bottom-left': return 'bottom-2 left-2';
      case 'center': return 'top-2 left-1/2 -translate-x-1/2';
      default: return 'top-2 right-2';
    }
  };

  const showMultiToast = (ch, toastId, eventName, reason = '', eventId = null) => {
    // kiosk 一期只读且各副窗独立 renderer，禁止重复播报/堆叠全局交互提示。
    if (kioskMode.value) return;
    const det = systemStore.getChannelDetection(ch);
    const config = getToastConfig(toastId, ch);
    const icons = { ok: CircleCheck, ng: CircleClose, custom: Warning };

    let subtitle = config.subText || '';
    if (toastId === 'ng' && reason && det.showNgReason) {
      subtitle = reason;
    }

    const toast = {
      id: ++toastIdCounter,
      toastId,
      // 原始事件编号(1=合格OK / 2=不良NG / 其它=自定义事件)，透传给 cycle-result.indicator 插槽，
      // 让整页覆盖型插件能按事件身份决定"内置OK/NG交给自绘横幅、自定义事件仍弹标准提示框"。
      eventId,
      title: config.text || eventName,
      subtitle,
      color: config.color,
      fontSize: config.fontSize,
      position: config.position,
      icon: toastId === 'ok' ? icons.ok : (toastId === 'ng' ? icons.ng : icons.custom)
    };

    if (!multiActiveToasts.value[ch]) multiActiveToasts.value[ch] = [];
    multiActiveToasts.value[ch].push(toast);
    while (multiActiveToasts.value[ch].length > 2) {
      multiActiveToasts.value[ch].shift();
    }

    if (toastId === 'ok') {
      speak('合格', ch);
    } else if (toastId === 'ng') {
      const showReason = det.showNgReason && reason;
      speak(showReason ? `不合格，${reason}` : '不合格', ch);
    } else {
      speak(config.text || eventName || '事件触发', ch);
    }

    setTimeout(() => {
      const arr = multiActiveToasts.value[ch];
      if (arr) {
        const idx = arr.findIndex(t => t.id === toast.id);
        if (idx > -1) arr.splice(idx, 1);
      }
    }, config.duration * 1000);
  };

  return {
    activeToasts, multiActiveToasts,
    getToastConfig, getPositionClass, getMultiPositionClass, toastPositionClass,
    speak, showToastById, showToast, showMultiToast,
  };
}
