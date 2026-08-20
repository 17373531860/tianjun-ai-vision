// ==================== v3.9.x 事件人工确认 (从 index.vue 原样外置) ====================
// nowTimestamp: 实时时钟 ref, 0.5s 一次刷新, 用于驱动倒计时 / 已等待秒数 computed
// pendingAckDisplay: 当前要在覆盖层展示哪个通道的阻塞信息 (优先选中, 其次最早阻塞)
// pendingAckWaitedSec / pendingAckRemainSec: 实时计算的秒数 (依赖 nowTimestamp + multiChannelData)
// ackPendingForChannel: 调后端 ack-event 接口, 成功后乐观清前端 pendingAck (后端下次 polling 也会清)
import { ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { ackPendingEvent, ackPendingEventElevated } from '@/api/detection';

export function useManualAck({ channelCount, multiChannelData, selectedChannel }) {
  const nowTimestamp = ref(Math.floor(Date.now() / 1000));
  let _nowTickInterval = null;

  const startNowTick = () => {
    if (_nowTickInterval) return;
    _nowTickInterval = setInterval(() => {
      nowTimestamp.value = Math.floor(Date.now() / 1000);
    }, 500);
  };
  const stopNowTick = () => {
    if (_nowTickInterval) { clearInterval(_nowTickInterval); _nowTickInterval = null; }
  };

  const pendingAckChannelStates = computed(() => {
    const list = [];
    for (let ch = 0; ch < channelCount.value; ch++) {
      const pa = multiChannelData.value[ch]?.pendingAck;
      if (pa && pa.active) {
        list.push({
          channel: ch,
          eventId: pa.event_id,
          eventName: pa.event_name,
          startedAt: pa.started_at || nowTimestamp.value,
          timeoutSec: Number(pa.timeout_sec || 0),
          // v3.43.1 优先用阻塞态自带原因 (后端固化, 不随 30s 事件窗滚动丢失);
          // 老后端无 reason 字段时兜底回 recentEvents 捞
          reason: pa.reason || (multiChannelData.value[ch]?.recentEvents || [])
            .filter(e => e.require_ack)
            .slice(-1)[0]?.reason || '',
          // v3.43.1 确认后的处置方式: true=保留周期断点续做 / false=清运行时整件重做
          keepsCycle: !!pa.keeps_cycle,
          acking: !!multiChannelData.value[ch]?.pendingAckSubmitting,
          // v3.23 缺步骤延迟落账挂起 (有值 → ack 窗展示缺项 + "补步骤/认NG/重做"三按钮)
          remediation: multiChannelData.value[ch]?.pendingRemediation || null,
          // v3.44 包装层 NG 箱账挂起等处置 (有值 → 弹窗露"认NG落账/重做本箱"双选,
          // 明示"重做不记 NG 箱"; 结构 {box, sliders, target, is_tail, ...})
          pkgHold: pa.pkg_hold || null,
        });
      }
    }
    return list;
  });

  const pendingAckDisplay = computed(() => {
    const list = pendingAckChannelStates.value;
    if (list.length === 0) return null;
    const cur = list.find(s => s.channel === selectedChannel.value);
    if (cur) return cur;
    return list.slice().sort((a, b) => a.startedAt - b.startedAt)[0];
  });

  const pendingAckWaitedSec = computed(() => {
    const d = pendingAckDisplay.value;
    if (!d) return 0;
    return Math.max(0, Math.floor(nowTimestamp.value - d.startedAt));
  });

  const pendingAckRemainSec = computed(() => {
    const d = pendingAckDisplay.value;
    if (!d || !d.timeoutSec) return 0;
    return Math.max(0, d.timeoutSec - pendingAckWaitedSec.value);
  });

  // v3.23 action: null/redo=重做 / supplement_step=补步骤判OK / confirm_ng=认NG落账
  const ackPendingForChannel = async (ch, action = null) => {
    const chData = multiChannelData.value[ch];
    if (!chData || !chData.pendingAck?.active) return;
    if (chData.pendingAckSubmitting) return;
    chData.pendingAckSubmitting = true;
    try {
      const res = await ackPendingEvent(ch, action);
      if (res?.data?.acked) {
        // v3.44: 后端带回包装挂账解挂结果 → 文案按箱语义说清落没落账
        const pkg = res?.data?.packaging;
        const msg = pkg && action === 'confirm_ng' ? '已认 NG 落账，进入下一箱'
          : pkg ? '已确认，本箱不记 NG，同箱重做'
          : action === 'supplement_step' ? '已补步骤判合格'
          : action === 'confirm_ng' ? '已确认 NG'
          : res?.data?.kept_cycle ? '已确认，保留周期从断点继续'
          : '已确认，重置当前周期';
        ElMessage.success(`工位 ${ch + 1} ${msg}`);
      } else {
        ElMessage.info(`工位 ${ch + 1} 当前没有待确认事件`);
      }
      chData.pendingAck = { active: false };
      chData.pendingRemediation = null;
    } catch (e) {
      // 403 = 当前登录账号没有"人工确认"权限 (常见: 操作员) → 弹借管理员密码提权窗 (携带本次动作)
      if (e?.response?.status === 403) {
        openElevateDialog(ch, action);
      } else {
        console.error('[ackPendingForChannel] failed', e);
        ElMessage.error('确认失败：' + (e?.message || '未知错误'));
      }
    } finally {
      chData.pendingAckSubmitting = false;
    }
  };

  // v3.23 借密码提权确认: 操作员无 ack 权限时, 输入管理员账密授权一次, 不改当前登录身份.
  // 确认完仍是该操作员的会话 (后端 ack-event-elevated 只校验一次账密 + 权限, 不发 token).
  const elevateDialog = ref({ visible: false, channel: 0, username: '', password: '', submitting: false, action: null });

  const openElevateDialog = (ch, action = null) => {
    elevateDialog.value = { visible: true, channel: ch, username: '', password: '', submitting: false, action };
  };

  const submitElevatedAck = async () => {
    const d = elevateDialog.value;
    if (!d.username || !d.password) {
      ElMessage.warning('请输入管理员账号和密码');
      return;
    }
    d.submitting = true;
    try {
      const res = await ackPendingEventElevated(d.channel, d.username, d.password, d.action);
      if (res?.data?.acked) {
        const ch = d.channel;
        ElMessage.success(`已由 ${res.data.authorized_by || d.username} 授权，工位 ${ch + 1} 确认成功`);
        const chData = multiChannelData.value[ch];
        if (chData) { chData.pendingAck = { active: false }; chData.pendingRemediation = null; }
      } else {
        ElMessage.info('当前没有待确认事件');
      }
      elevateDialog.value.visible = false;
    } catch (e) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail || e?.message || '未知错误';
      if (status === 401) {
        ElMessage.error('账号或密码错误');
      } else if (status === 403) {
        ElMessage.error(detail);
      } else {
        ElMessage.error('授权失败：' + detail);
      }
    } finally {
      elevateDialog.value.submitting = false;
    }
  };

  return {
    nowTimestamp,
    startNowTick,
    stopNowTick,
    pendingAckChannelStates,
    pendingAckDisplay,
    pendingAckWaitedSec,
    pendingAckRemainSec,
    ackPendingForChannel,
    elevateDialog,
    openElevateDialog,
    submitElevatedAck,
  };
}
