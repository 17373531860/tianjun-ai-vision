"""AI 采样模式 (logic_mode='ocr' | 'anomaly') —— VSM 侧执行层.

2026-09 新增两种正式逻辑模式, 与 YOLO 检测状态机完全解耦:

    ocr     — OCR 读字判定: 按规则节流采样当前帧, RapidOCR 读指定 ROI 文字,
              文本连续 N 次稳定后按 pattern 匹配判定 OK/NG, 走标准结算链。
              典型场景: 产线序列号/批次号/标签核对。
    anomaly — 异常检测哨兵: 好样本记忆库 (PatchCore/DINOv2) 对当前帧持续打分,
              连续 N 次超阈值触发 NG 事件 (带冷却防刷屏), 恢复正常可选 OK 事件。
              典型场景: 只有好样本、缺陷形态未知的表面质检。

架构决策 (对齐 weighing 范式, 不进 YOLO 推理热路径):
  - 独立节流采样线程 (_ai_sampling_loop), 每 interval_s 秒取一次 get_frame(),
    OCR/异常评分都是重操作, 绝不能每帧跑;
  - 无需 YOLO 模型即可开始检测 —— start_detection 的模型守门对这两种模式放行
    (见 source.py), 推理线程也不启动;
  - 事件/周期/落库全部复用标准链路: start_cycle → _trigger_event → end_cycle,
    Data 页/导出/MES/报警零适配;
  - 运行态快照 _ai_mode_snapshot 由 /source/detection/results 透出 (result['ai_mode']),
    Monitor 专属面板消费;
  - 非该模式时全部状态为 None → 热路径一次 getattr 早退, 零开销 (不变量口径)。
"""
from __future__ import annotations

import re
import threading
import time


class AiModesMixin:
    """宿主: VideoSourceManager。依赖宿主 get_frame / start_cycle / _trigger_event /
    current_cycle_uuid / project_config。状态变量在 source_state_init.py 初始化。
    """

    # ---------- 模式判定 ----------
    def _ai_mode_active(self) -> bool:
        """当前项目是否为 AI 采样模式 (ocr/anomaly)。守门/线程启动共用判据。"""
        cfg = getattr(self, 'project_config', None) or {}
        return cfg.get('logic_mode') in ('ocr', 'anomaly')

    # ---------- 线程生命周期 ----------
    def _start_ai_sampling_thread(self):
        if getattr(self, '_ai_mode_thread', None) and self._ai_mode_thread.is_alive():
            return
        self._ai_mode_running = True
        self._ai_mode_thread = threading.Thread(
            target=self._ai_sampling_loop, daemon=True,
            name=f"ai-sampling-ch{getattr(self, 'channel_id', 0)}")
        self._ai_mode_thread.start()

    def _stop_ai_sampling_thread(self):
        self._ai_mode_running = False
        t = getattr(self, '_ai_mode_thread', None)
        if t and t.is_alive():
            t.join(timeout=3.0)
        self._ai_mode_thread = None

    def _ai_sampling_loop(self):
        mode = (self.project_config or {}).get('logic_mode')
        print(f"[AiMode] 采样线程启动 ch={getattr(self, 'channel_id', 0)} mode={mode}")
        while getattr(self, '_ai_mode_running', False) and self.is_detecting:
            ocr_cfg = getattr(self, '_ai_ocr_cfg', None)
            ano_cfg = getattr(self, '_ai_anomaly_cfg', None)
            cfg = ocr_cfg or ano_cfg
            interval = float((cfg or {}).get('interval_s') or 2.0)
            t0 = time.time()
            try:
                frame = self.get_frame()
                if frame is not None:
                    if ocr_cfg:
                        self._ai_process_ocr(frame, ocr_cfg)
                    elif ano_cfg:
                        self._ai_process_anomaly(frame, ano_cfg)
            except Exception as e:
                print(f"[AiMode] 采样处理异常 (已隔离): {e}")
                snap = getattr(self, '_ai_mode_snapshot', None) or {}
                snap['error'] = str(e)
                snap['updated_at'] = time.time()
                self._ai_mode_snapshot = snap
            # 睡到下一采样点; 小步睡以便快速响应停止
            deadline = t0 + max(0.2, interval)
            while (getattr(self, '_ai_mode_running', False) and self.is_detecting
                   and time.time() < deadline):
                time.sleep(0.05)
        print(f"[AiMode] 采样线程退出 ch={getattr(self, 'channel_id', 0)}")

    # ---------- 周期辅助 ----------
    def _ai_ensure_cycle(self, start_ts: float = None):
        """判定触发前确保周期已开 (与 region_events 首确认开周期同语义)。"""
        if not getattr(self, 'current_cycle_uuid', None):
            self.cycle_start_time = start_ts or time.time()
            try:
                self.cycle_start_frame_pos = self._video_frame_pos()
            except Exception:
                pass
            self.start_cycle()

    # ---------- OCR 模式 ----------
    def _ai_process_ocr(self, frame, cfg: dict):
        from backend.services import ocr_engine
        if not ocr_engine.is_available():
            self._ai_mode_snapshot = {
                'mode': 'ocr', 'available': False,
                'error': 'OCR 引擎不可用 (rapidocr_onnxruntime 未安装)',
                'updated_at': time.time(),
            }
            return

        states = getattr(self, '_ai_rule_states', None)
        if states is None:
            states = self._ai_rule_states = {}
        min_score = float(cfg.get('min_score') or 0.5)
        stable_reads = max(1, int(cfg.get('stable_reads') or 2))
        snap_rules = []

        for rule in cfg.get('rules') or []:
            rid = rule['id']
            st = states.setdefault(rid, {
                'last_text': '', 'stable': 0, 'last_trigger_text': None,
            })
            try:
                items = ocr_engine.read_text(frame, roi=rule.get('roi'),
                                             min_score=min_score)
            except Exception as e:
                print(f"[AiMode/OCR] 规则 {rule.get('name')} 识别失败 (已隔离): {e}")
                items = []
            text = ' '.join(i['text'] for i in items).strip()
            top_score = max((i['score'] for i in items), default=0.0)

            if text and text == st['last_text']:
                st['stable'] += 1
            else:
                st['stable'] = 1 if text else 0
            st['last_text'] = text

            matched = None
            triggered = False
            if text and st['stable'] >= stable_reads:
                pat = rule.get('_pattern')
                matched = bool(pat.search(text)) if pat is not None else True
                # on_change_only (默认开): 同一文本不重复触发, 换文本才再判
                if not (rule.get('on_change_only', True)
                        and text == st.get('last_trigger_text')):
                    st['last_trigger_text'] = text
                    triggered = True
                    event_id = rule.get('ok_event_id') if matched else rule.get('ng_event_id')
                    verdict = '匹配' if matched else f"不匹配 /{rule.get('pattern')}/"
                    reason = f"OCR[{rule['name']}] 读到 \"{text}\" {verdict}" \
                        if rule.get('pattern') else f"OCR[{rule['name']}] 读到 \"{text}\""
                    if event_id:
                        self._ai_ensure_cycle()
                        # 文本进周期步骤序列, Data 页可追溯
                        try:
                            self.current_cycle_steps.append(f"{rule['name']}:{text}")
                        except Exception:
                            pass
                        if rule.get('settle', True):
                            self._trigger_event(int(event_id), reason)
                            self.current_cycle_steps = []
                        else:
                            self.fire_external_event_response(
                                int(event_id), reason, source='ocr')
                    print(f"[AiMode/OCR] {reason} → event_id={event_id}")

            snap_rules.append({
                'id': rid, 'name': rule['name'],
                'last_text': text, 'last_score': round(top_score, 3),
                'stable': st['stable'], 'stable_reads': stable_reads,
                'matched': matched, 'triggered': triggered,
                'pattern': rule.get('pattern') or '',
            })

        self._ai_mode_snapshot = {
            'mode': 'ocr', 'available': True, 'rules': snap_rules,
            'interval_s': float(cfg.get('interval_s') or 2.0),
            'updated_at': time.time(),
        }

    # ---------- 异常检测模式 ----------
    def _ai_process_anomaly(self, frame, cfg: dict):
        from backend.services import anomaly_engine
        bank_id = cfg.get('bank_id')
        if not bank_id:
            self._ai_mode_snapshot = {
                'mode': 'anomaly', 'available': False,
                'error': '未配置记忆库 (bank_id)', 'updated_at': time.time(),
            }
            return

        st = getattr(self, '_ai_rule_states', None)
        if st is None:
            st = self._ai_rule_states = {}
        s = st.setdefault('_anomaly', {
            'consec_ng': 0, 'consec_ok': 0, 'cooldown_until': 0.0,
            'in_alarm': False,
        })

        img = frame
        roi = cfg.get('roi')
        if roi:
            h0, w0 = frame.shape[:2]
            x, y, w, h = [float(v) for v in roi[:4]]
            x1 = max(0, min(w0 - 1, int(round(x * w0))))
            y1 = max(0, min(h0 - 1, int(round(y * h0))))
            x2 = max(x1 + 1, min(w0, int(round((x + w) * w0))))
            y2 = max(y1 + 1, min(h0, int(round((y + h) * h0))))
            img = frame[y1:y2, x1:x2]

        try:
            res = anomaly_engine.score_image(
                bank_id, img,
                threshold=cfg.get('threshold'))
        except Exception as e:
            self._ai_mode_snapshot = {
                'mode': 'anomaly', 'available': False, 'bank_id': bank_id,
                'error': f'评分失败: {e}', 'updated_at': time.time(),
            }
            return

        consecutive = max(1, int(cfg.get('consecutive') or 3))
        now = time.time()
        if res['is_anomaly']:
            s['consec_ng'] += 1
            s['consec_ok'] = 0
        else:
            s['consec_ok'] += 1
            s['consec_ng'] = 0

        # NG 触发: 连续超阈值 + 冷却窗外
        if (s['consec_ng'] >= consecutive and now >= s['cooldown_until']):
            ng_event_id = int(cfg.get('ng_event_id') or 2)
            reason = (f"异常检测: 分数 {res['score']} > 阈值 {res['threshold']} "
                      f"(连续 {s['consec_ng']} 次)")
            self._ai_ensure_cycle()
            self._trigger_event(ng_event_id, reason)
            s['cooldown_until'] = now + float(cfg.get('ng_cooldown_s') or 30)
            s['consec_ng'] = 0
            s['in_alarm'] = True
            print(f"[AiMode/Anomaly] {reason} → event_id={ng_event_id}")

        # 恢复正常: 报警态下连续 N 次正常, 可选 OK 事件收口
        if s['in_alarm'] and s['consec_ok'] >= consecutive:
            s['in_alarm'] = False
            if cfg.get('recover_ok_event'):
                ok_event_id = int(cfg.get('ok_event_id') or 1)
                reason = f"异常检测: 恢复正常 (分数 {res['score']} ≤ 阈值 {res['threshold']})"
                self._ai_ensure_cycle()
                self._trigger_event(ok_event_id, reason)
                print(f"[AiMode/Anomaly] {reason} → event_id={ok_event_id}")

        self._ai_mode_snapshot = {
            'mode': 'anomaly', 'available': True,
            'bank_id': bank_id,
            'score': res['score'], 'threshold': res['threshold'],
            'is_anomaly': res['is_anomaly'],
            'backbone': res.get('backbone'),
            'consec_ng': s['consec_ng'], 'consecutive': consecutive,
            'in_alarm': s['in_alarm'],
            'cooldown_remaining': max(0, round(s['cooldown_until'] - now, 1)),
            'interval_s': float(cfg.get('interval_s') or 2.0),
            'updated_at': now,
        }


# ---------- 配置解析 (apply_project_config 调用) ----------

def _norm_rect(v) -> list | None:
    """归一化矩形 [x,y,w,h] 校验; 非法返回 None (= 整帧)。"""
    if not isinstance(v, (list, tuple)) or len(v) < 4:
        return None
    try:
        x, y, w, h = [float(x) for x in v[:4]]
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return [max(0.0, min(1.0, x)), max(0.0, min(1.0, y)),
            max(0.001, min(1.0, w)), max(0.001, min(1.0, h))]


def parse_ocr_config(raw: dict) -> dict:
    """pipeline_config.ocr → 运行时配置。非法规则跳过不致命。"""
    raw = raw or {}
    rules = []
    for i, r in enumerate(raw.get('rules') or []):
        if not isinstance(r, dict):
            continue
        name = str(r.get('name') or f'规则{i + 1}').strip()
        pattern = str(r.get('pattern') or '').strip()
        compiled = None
        if pattern:
            try:
                compiled = re.compile(pattern)
            except re.error as e:
                print(f"[AiMode/OCR] 规则 {name} pattern 非法, 视为任意匹配: {e}")
                pattern = ''
        rules.append({
            'id': str(r.get('id') or f'ocr_{i}'),
            'name': name,
            'roi': _norm_rect(r.get('roi')),
            'pattern': pattern,
            '_pattern': compiled,
            'ok_event_id': int(r['ok_event_id']) if r.get('ok_event_id') else 1,
            'ng_event_id': int(r['ng_event_id']) if r.get('ng_event_id') else 2,
            'on_change_only': bool(r.get('on_change_only', True)),
            'settle': bool(r.get('settle', True)),
        })
    return {
        'interval_s': max(0.2, float(raw.get('interval_s') or 2.0)),
        'min_score': min(1.0, max(0.05, float(raw.get('min_score') or 0.5))),
        'stable_reads': max(1, int(raw.get('stable_reads') or 2)),
        'rules': rules,
    }


def parse_anomaly_config(raw: dict) -> dict:
    """pipeline_config.anomaly → 运行时配置。"""
    raw = raw or {}
    thr = raw.get('threshold')
    try:
        thr = float(thr) if thr not in (None, '', 0) else None
    except (TypeError, ValueError):
        thr = None
    return {
        'bank_id': str(raw.get('bank_id') or '').strip() or None,
        'threshold': thr,  # None = 用库自带阈值
        'interval_s': max(0.2, float(raw.get('interval_s') or 2.0)),
        'consecutive': max(1, int(raw.get('consecutive') or 3)),
        'roi': _norm_rect(raw.get('roi')),
        'ok_event_id': int(raw['ok_event_id']) if raw.get('ok_event_id') else 1,
        'ng_event_id': int(raw['ng_event_id']) if raw.get('ng_event_id') else 2,
        'ng_cooldown_s': max(0.0, float(raw.get('ng_cooldown_s') or 30)),
        'recover_ok_event': bool(raw.get('recover_ok_event', False)),
    }


def apply_ai_modes_config(h, config: dict, pipeline_config: dict):
    """apply_project_config 的 AI 采样模式接线: 解析配置 + 重置运行时状态。

    非 ocr/anomaly 模式清空全部状态 (零开销不变量)。线程的启停跟随
    start_detection / stop_detection, 这里只管配置。
    """
    mode = config.get('logic_mode')
    h._ai_ocr_cfg = None
    h._ai_anomaly_cfg = None
    h._ai_rule_states = {}
    h._ai_mode_snapshot = None
    if mode == 'ocr':
        h._ai_ocr_cfg = parse_ocr_config(pipeline_config.get('ocr'))
        print(f"[AiMode] OCR 模式已配置: {len(h._ai_ocr_cfg['rules'])} 条规则, "
              f"采样间隔 {h._ai_ocr_cfg['interval_s']}s")
    elif mode == 'anomaly':
        h._ai_anomaly_cfg = parse_anomaly_config(pipeline_config.get('anomaly'))
        print(f"[AiMode] 异常检测模式已配置: bank={h._ai_anomaly_cfg['bank_id']}, "
              f"采样间隔 {h._ai_anomaly_cfg['interval_s']}s")
