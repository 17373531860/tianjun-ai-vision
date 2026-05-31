/**
 * 福建金龙 — 双工位检测主页定制 (Tier 2 UI 插件).
 *
 * v3 设计 (按 v3.14 用户反馈调整):
 *   ┌────────────────────────────────────────────────────────────────┐
 *   │ ┌─工位1视频─┐ ┌──工位1统计 (3×2: 检测/OK/NG + 饼图/仪表/NG TOP)─┐ │
 *   │ │          │ │                                                │ │
 *   │ └──────────┘ └────────────────────────────────────────────────┘ │
 *   │ ┌─工位2视频─┐ ┌──工位2统计 (3×2)──────────────────────────────┐ │
 *   │ └──────────┘ └────────────────────────────────────────────────┘ │
 *   │ ┌─SOP 流程卡片 (GW1 7卡 → GW2 2卡, 总 9 张串联, 横向滚动)──────┐ │
 *   │ └────────────────────────────────────────────────────────────┘ │
 *   │ ┌─步骤统计 (合计) + [开始][停止][待机][清零]────────────────┐ │
 *   │ └────────────────────────────────────────────────────────────┘ │
 *   └────────────────────────────────────────────────────────────────┘
 *
 *   关键差异 (vs v2):
 *     - v2 的工作流卡片(横向小步骤条) 升级为主程序 SOP 流程卡片样式 (带截图占位 / 状态色 / 闪烁)
 *     - GW1 GW2 步数不同, 拼成一条 9 张卡片链, 用工位徽章区分
 *     - SOP 区独立成一行, 占满宽度 (不再左下小块)
 *     - 良品/不良 + 合格率改用主程序透传的 host.echarts (饼图 + 仪表盘, 配色与主程序对齐)
 *     - 字号 / cell 大小用 clamp() 自适应 (避免 60 寸/14 寸 表现差异过大)
 *
 *   v1.1.6: 步骤表 PT/结果列接入 monitor.step-cell.* TjSlot (宿主 renderStepCell* 桥接)
 *     - main_version_min 3.14.0 (host.echarts 透传)
 *     - host.echarts 缺失时回退到 SVG 兜底, 不让主程序崩
 */

let _registeredSlot = null;

// ==================== 工位状态徽章 ====================
function buildStatusInfo(chData) {
  if (!chData) return { label: "停止", cls: "is-stopped" };
  if (chData.isDetecting) return { label: "检测中", cls: "is-detecting" };
  if (chData.isRunning) return { label: "待机", cls: "is-standby" };
  return { label: "停止", cls: "is-stopped" };
}

const WP_STATUS_LABELS = {
  registered: "已登记",
  queued: "排队",
  inspecting: "检测中",
  ok: "合格",
  ng: "不良",
};

function wpStatusLabel(status) {
  return WP_STATUS_LABELS[status] || status || "—";
}

function wpStatusClass(status) {
  if (status === "ok") return "is-ok";
  if (status === "ng") return "is-ng";
  if (status === "inspecting") return "is-inspecting";
  return "is-default";
}

/** 视频底栏: 总产量/OK/NG + 多模型 FPS + 主 FPS (与原生双工位一致) */
function buildVideoStatsBar(h, chIdx, chData, modelStats, actions) {
  const disp = typeof actions?.getMonitorDisplay === "function" ? actions.getMonitorDisplay() : {};
  const showFps = disp.showFps !== false;
  const safe = chData || {};
  const models = Array.isArray(modelStats) ? modelStats : [];
  const nodes = [
    h("span", { class: "fjjl-vstat" }, [
      "总: ",
      h("strong", { class: "is-total" }, String(safe.total ?? 0)),
    ]),
    h("span", { class: "fjjl-vstat" }, [
      "OK: ",
      h("strong", { class: "is-ok" }, String(safe.ok ?? 0)),
    ]),
    h("span", { class: "fjjl-vstat" }, [
      "NG: ",
      h("strong", { class: "is-ng" }, String(safe.ng ?? 0)),
    ]),
  ];
  if (models.length >= 2) {
    nodes.push(h("span", { class: "fjjl-vstat-sep" }, "|"));
    models.forEach((m) => {
      nodes.push(
        h("span", {
          class: "fjjl-vstat-model",
          title: `${m.name || "模型"} (${m.model_loaded ? "已加载" : "未加载"})`,
        }, [
          h("span", {
            class: "fjjl-vstat-dot",
            style: { backgroundColor: m.display_color || "#10b981" },
          }),
          h("span", { class: "fjjl-vstat-model-name" }, m.name || "—"),
          showFps ? h("span", { class: "fjjl-vstat-model-fps" }, String(m.fps_inference || 0)) : null,
        ])
      );
    });
  }
  if (showFps) {
    nodes.push(
      h("span", { class: "fjjl-vstat-fps" }, `FPS: ${safe.fps ?? 0}`)
    );
  }
  return h("div", { class: "fjjl-video-stats-bar" }, nodes);
}

/** MES 信息条: 工件号 / 未绑码 / 等待扫码 / 工单 / 清除 / 禁用扫码 */
function buildMesBar(h, chIdx, chData, actions) {
  const a = actions || {};
  if (typeof a.shouldShowMesBarFor !== "function" || !a.shouldShowMesBarFor(chIdx)) {
    return null;
  }
  const mes = typeof a.getMesDataFor === "function" ? a.getMesDataFor(chIdx) : null;
  const wp = typeof a.getDisplayWorkpieceFor === "function" ? a.getDisplayWorkpieceFor(chIdx) : null;
  const scanDisabled = typeof a.isScanDisabledFor === "function" && a.isScanDisabledFor(chIdx);
  const hasScanner = typeof a.hasScannerFor === "function" && a.hasScannerFor(chIdx);
  const toggling = typeof a.getScannerDisableToggling === "function" && a.getScannerDisableToggling();

  const leftNodes = [];
  if (scanDisabled) {
    leftNodes.push(h("span", { class: "fjjl-mes-disabled" }, "⛔ 扫码已禁用 · 走项目原生结算"));
  } else if (wp) {
    leftNodes.push(
      h("span", { class: "fjjl-mes-wp" }, [
        h("span", { class: "fjjl-mes-label" }, "工件:"),
        h("span", { class: "fjjl-mes-serial", title: wp.serial_no || "" }, wp.serial_no || "—"),
        h("span", { class: `fjjl-mes-tag ${wpStatusClass(wp.status)}` }, wpStatusLabel(wp.status)),
      ])
    );
  } else if (hasScanner && mes?.warn_no_barcode) {
    leftNodes.push(
      h("span", { class: "fjjl-mes-warn" }, [
        h("strong", null, "⚠ 未绑码"),
        " 请扫描工件条码",
      ])
    );
  } else if (!wp && !mes?.order) {
    leftNodes.push(h("span", { class: "fjjl-mes-wait" }, "等待扫码..."));
  }

  const rightNodes = [];
  if (mes?.order) {
    rightNodes.push(
      h("span", { class: "fjjl-mes-order" }, [
        h("span", { class: "fjjl-mes-label" }, "工单:"),
        h("span", { class: "fjjl-mes-order-no", title: mes.order.order_no || "" }, mes.order.order_no || "—"),
        h("span", { class: "fjjl-mes-order-qty" },
          `${mes.order.completed_qty ?? 0}/${mes.order.planned_qty ?? 0}`),
      ])
    );
  }
  if (!scanDisabled) {
    rightNodes.push(
      h("button", {
        class: "fjjl-mes-btn is-clear",
        type: "button",
        onClick: (e) => {
          e.stopPropagation();
          if (typeof a.clearPendingScan === "function") a.clearPendingScan(chIdx);
        },
      }, "清除")
    );
  }
  rightNodes.push(
    h("button", {
      class: `fjjl-mes-btn ${scanDisabled ? "is-enable-scan" : "is-disable-scan"}`,
      type: "button",
      disabled: toggling,
      onClick: (e) => {
        e.stopPropagation();
        if (typeof a.toggleScanDisableFor === "function") a.toggleScanDisableFor(chIdx);
      },
    }, scanDisabled ? "启用扫码" : "禁用扫码")
  );

  return h("div", { class: "fjjl-mes-bar", onClick: (e) => e.stopPropagation() }, [
    h("div", { class: "fjjl-mes-left" }, leftNodes),
    h("div", { class: "fjjl-mes-right" }, rightNodes),
  ]);
}

/** 单工位控制按钮 (开始/停止/待机/清零) */
function buildChannelControls(h, chIdx, chData, actions, currentProject) {
  const a = actions || {};
  const safe = chData || {};
  const canStart = !!(safe.project || currentProject) && !safe.isDetecting;
  const btn = (label, cls, disabled, onClick) =>
    h("button", {
      class: `fjjl-ch-btn ${cls}`,
      type: "button",
      disabled: !!disabled,
      onClick: (e) => { e.stopPropagation(); onClick(); },
    }, label);

  return h("div", { class: "fjjl-channel-controls", onClick: (e) => e.stopPropagation() }, [
    btn("开始", "is-start", !canStart, () => a.startDetectionForChannel?.(chIdx)),
    btn("停止", "is-stop", !safe.isRunning, () => a.stopDetectionForChannel?.(chIdx)),
    btn("待机", "is-standby", !safe.isDetecting, () => a.standbyForChannel?.(chIdx)),
    btn("清零", "is-reset", safe.isDetecting, () => a.resetCountersForChannel?.(chIdx)),
  ]);
}

// ==================== 视频 cell (工位 N 的视频 + 进度条) ====================
function buildVideoCellComponent(host) {
  const { defineComponent, h, ref, computed, watch, onMounted, onBeforeUnmount, nextTick } = host.vue;

  return defineComponent({
    name: "FjjlVideoCell",
    props: {
      chIdx: { type: Number, required: true },
      chData: { type: Object, default: null },
      streamUrl: { type: String, default: "" },
      streamUrlBuilder: { type: Function, default: null },
      actions: { type: Object, default: () => ({}) },
      selected: { type: Boolean, default: false },
      channelModelStats: { type: Array, default: () => [] },
    },
    emits: ["refresh-stream", "select"],
    setup(props, { emit }) {
      const localProgress = ref(0);
      const localSpeed = ref(1);
      const dragging = ref(false);
      const overlayCanvasRef = ref(null);
      let resizeObserver = null;

      const isVideo = computed(() => props.chData?.sourceType === "video");
      const videoInfo = computed(() => props.chData?.videoInfo || null);
      const statusInfo = computed(() => buildStatusInfo(props.chData));

      const paintOverlay = () => {
        const canvas = overlayCanvasRef.value;
        const fn = props.actions?.renderDetectionOverlay;
        if (canvas && typeof fn === "function") {
          fn(props.chIdx, canvas);
        }
      };

      const bindResizeObserver = () => {
        if (resizeObserver || typeof ResizeObserver === "undefined") return;
        const wrap = overlayCanvasRef.value?.parentElement;
        if (!wrap) return;
        resizeObserver = new ResizeObserver(() => paintOverlay());
        resizeObserver.observe(wrap);
      };

      watch(
        () => props.chData?.detections,
        () => { nextTick(() => paintOverlay()); },
        { deep: true }
      );

      onMounted(() => {
        nextTick(() => {
          bindResizeObserver();
          paintOverlay();
        });
      });

      onBeforeUnmount(() => {
        if (resizeObserver) {
          resizeObserver.disconnect();
          resizeObserver = null;
        }
      });

      watch(videoInfo, (vi) => {
        if (!vi || dragging.value) return;
        localProgress.value = vi.progress ?? 0;
        localSpeed.value = vi.speed ?? 1;
      }, { immediate: true, deep: true });

      const fmtTime = (sec) => {
        if (typeof props.actions?.formatVideoTime === "function") {
          return props.actions.formatVideoTime(sec);
        }
        if (!sec || isNaN(sec)) return "00:00";
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      };

      const refreshStream = () => {
        if (typeof props.streamUrlBuilder === "function") {
          emit("refresh-stream", props.chIdx);
        }
      };

      const commitProgress = async () => {
        dragging.value = false;
        const fn = props.actions?.setVideoProgressForChannel;
        if (typeof fn !== "function") return;
        try {
          await fn(props.chIdx, localProgress.value);
          refreshStream();
        } catch (e) {
          console.warn("[fujian-jinlong] 设置视频进度失败:", e);
        }
      };

      const onSpeedChange = async () => {
        const fn = props.actions?.setVideoSpeedForChannel;
        if (typeof fn !== "function") return;
        try {
          await fn(props.chIdx, localSpeed.value);
        } catch (e) {
          console.warn("[fujian-jinlong] 设置视频倍速失败:", e);
        }
      };

      return () => {
        const s = statusInfo.value;
        const vi = videoInfo.value;
        const detecting = !!props.chData?.isDetecting;
        const projName = props.chData?.projectName || "";

        return h("section", { class: "fjjl-video-cell" }, [
          h("div", {
            class: ["fjjl-video-wrap", "group", props.selected ? "is-selected" : ""],
            onClick: () => emit("select"),
          }, [
            props.streamUrlBuilder && props.streamUrl
              ? h("img", {
                  class: "fjjl-video",
                  src: props.streamUrl,
                  alt: `工位 ${props.chIdx + 1} 视频`,
                  onLoad: (e) => {
                    const img = e?.target;
                    if (img?.naturalWidth) {
                      props.actions?.setFrameNaturalSize?.(props.chIdx, img.naturalWidth, img.naturalHeight);
                    }
                    paintOverlay();
                  },
                })
              : h("div", { class: "fjjl-novideo" }, "暂无视频源"),
            h("canvas", {
              ref: overlayCanvasRef,
              class: "fjjl-det-overlay",
            }),
            h("div", { class: "fjjl-station-tag" }, [
              `工位 ${props.chIdx + 1}`,
              projName ? h("span", { class: "fjjl-proj-name" }, projName) : null,
            ]),
            h("div", { class: `fjjl-status-tag ${s.cls}` }, s.label),
            buildVideoStatsBar(h, props.chIdx, props.chData, props.channelModelStats, props.actions),
            isVideo.value
              ? h("div", { class: "fjjl-video-footer" }, [
                  h("div", { class: "fjjl-video-progress-row" }, [
                    h("span", { class: "fjjl-vtime" }, fmtTime(vi?.currentTime)),
                    h("input", {
                      class: "fjjl-video-slider",
                      type: "range",
                      min: 0,
                      max: 1,
                      step: 0.001,
                      value: localProgress.value,
                      disabled: detecting,
                      onMousedown: () => { dragging.value = true; },
                      onTouchstart: () => { dragging.value = true; },
                      onInput: (e) => { localProgress.value = Number(e.target.value); },
                      onChange: commitProgress,
                      onMouseup: commitProgress,
                      onTouchend: commitProgress,
                    }),
                    h("span", { class: "fjjl-vtime fjjl-vtime-end" }, fmtTime(vi?.duration)),
                    h("select", {
                      class: "fjjl-video-speed",
                      value: localSpeed.value,
                      disabled: detecting,
                      onChange: (e) => {
                        localSpeed.value = Number(e.target.value);
                        onSpeedChange();
                      },
                    }, [
                      h("option", { value: 0.5 }, "0.5x"),
                      h("option", { value: 1 }, "1x"),
                      h("option", { value: 2 }, "2x"),
                      h("option", { value: 4 }, "4x"),
                      h("option", { value: 8 }, "8x"),
                    ]),
                  ]),
                  vi?.ended
                    ? h("div", { class: "fjjl-video-ended" }, "视频播放完毕")
                    : null,
                ])
              : null,
          ]),
          buildMesBar(h, props.chIdx, props.chData, props.actions),
        ]);
      };
    },
  });
}

// ==================== ECharts 子组件工厂 (饼图 / 仪表盘) ====================
//   每个 stats cell 各持有两个独立 ECharts 实例; 失效时 SVG 兜底.
function buildEChartsBox({ vue, echarts }) {
  const { defineComponent, h, ref, onMounted, onBeforeUnmount, watch } = vue;
  return defineComponent({
    name: "FjjlEChartsBox",
    props: {
      type: { type: String, required: true }, // "pie" | "gauge"
      ok:   { type: Number, default: 0 },
      ng:   { type: Number, default: 0 },
      yieldRate: { type: Number, default: 0 },
    },
    setup(props) {
      const domRef = ref(null);
      let inst = null;
      let ro = null;

      const buildOption = () => {
        if (props.type === "pie") {
          // 饼图: 圆环加大 (内 65% / 外 95%)
          return {
            backgroundColor: "transparent",
            tooltip: { trigger: "item", textStyle: { fontSize: 11 } },
            series: [{
              type: "pie",
              radius: ["65%", "95%"],
              center: ["50%", "50%"],
              avoidLabelOverlap: false,
              label: { show: false },
              labelLine: { show: false },
              data: [
                { value: props.ok || 0, name: "良品", itemStyle: { color: "#10b981" } },
                { value: props.ng || 0, name: "不良", itemStyle: { color: "#ef4444" } },
              ],
            }],
          };
        }
        // 仪表盘: 弧大约原版 2.5 倍, 数字落在弧中部空白
        const r = Number(props.yieldRate) || 0;
        const color = r >= 90 ? "#10b981" : r >= 70 ? "#f59e0b" : "#ef4444";
        return {
          backgroundColor: "transparent",
          series: [{
            type: "gauge",
            startAngle: 180, endAngle: 0,        /* 上半圆开口朝下 */
            min: 0, max: 100,
            radius: "170%",                       /* 弧半径放大 */
            center: ["50%", "82%"],               /* 弧大了 center 下移 */
            progress: { show: true, width: 24, itemStyle: { color } },
            axisLine: { lineStyle: { width: 24, color: [[1, "#334155"]] } },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
            anchor: { show: false },
            pointer: { show: false },
            title: { show: false },
            detail: {
              valueAnimation: true,
              fontSize: 20,
              fontWeight: "bold",
              offsetCenter: [0, "-20%"],          /* 数字落在弧中部空白 */
              formatter: "{value}%",
              color,
            },
            data: [{ value: Number(r.toFixed ? r.toFixed(1) : r) }],
          }],
        };
      };

      const apply = () => {
        if (!inst || inst.isDisposed()) return;
        inst.setOption(buildOption(), true);
      };

      onMounted(() => {
        if (!domRef.value || !echarts) return;
        try {
          inst = echarts.init(domRef.value);
          apply();
          ro = new ResizeObserver(() => {
            if (inst && !inst.isDisposed()) inst.resize();
          });
          ro.observe(domRef.value);
        } catch (e) {
          console.warn("[fujian-jinlong] echarts init failed:", e);
        }
      });

      onBeforeUnmount(() => {
        if (ro) ro.disconnect();
        if (inst && !inst.isDisposed()) inst.dispose();
        inst = null;
      });

      watch(() => [props.ok, props.ng, props.yieldRate, props.type], apply, { flush: "post" });

      return () => h("div", {
        ref: domRef,
        class: `fjjl-echart-box fjjl-echart-${props.type}`,
      });
    },
  });
}

// SVG 兜底 (host.echarts 不可用时)
function buildPieFallback(h, ok, ng) {
  const total = ok + ng;
  const okRatio = total > 0 ? ok / total : 1;
  const c = 2 * Math.PI * 14;
  const offset = c * (1 - okRatio);
  return h("svg", { viewBox: "0 0 36 36", class: "fjjl-fallback-svg" }, [
    h("circle", { cx: "18", cy: "18", r: "14", stroke: "#ef4444", "stroke-width": "5", fill: "none" }),
    h("circle", {
      cx: "18", cy: "18", r: "14",
      stroke: "#10b981", "stroke-width": "5", fill: "none",
      "stroke-dasharray": String(c),
      "stroke-dashoffset": String(offset),
      transform: "rotate(-90 18 18)",
    }),
  ]);
}
function buildGaugeFallback(h, r) {
  const pct = Math.max(0, Math.min(100, Number(r) || 0));
  return h("div", { class: "fjjl-fallback-gauge" }, [
    h("div", { class: "v" }, `${pct.toFixed(1)}%`),
  ]);
}

// ==================== 工位统计 cell (3x2 网格) ====================
// v1.1.7: 接入主程序"显示设置"(display.monitor.*) — 与原生 Monitor 同一套开关:
//   defaultCounters.showTotal/showGood/showBad → 检测次数/OK/NG 数字框
//   defaultCounters.showNgSteps + ngTop3        → NG 步骤 TOP 列表
//   defectChart  → 良品/不良 饼图
//   capacityChart→ 合格率 仪表盘
//   statsPanel===false → 整个统计网格隐藏
function buildStatsCell(h, chIdx, chData, ChartsBox, actions, currentProject, authOverride) {
  const safe = chData || {};
  // 权威覆盖: 后端 live-stats 在 → 用库里重算的 OK/NG/合格率 (含本插件判废改写);
  // 后端没起/降级返回空 → 回退主程序透传的 chData, 保证不空屏.
  const a = (authOverride && typeof authOverride === "object") ? authOverride : null;
  const total = Number((a ? a.total : safe.total) ?? 0);
  const ok = Number((a ? a.ok : safe.ok) ?? 0);
  const ng = Number((a ? a.ng : safe.ng) ?? 0);
  const yieldRate = Number((a ? a.yield_rate : safe.yieldRate) ?? 0);
  const ngSteps = Array.isArray(safe.ngStepRanking) ? safe.ngStepRanking.slice(0, 5) : [];
  const ctText = typeof actions?.getDisplayCT === "function" ? actions.getDisplayCT(safe) : "--";

  const disp = typeof actions?.getMonitorDisplay === "function" ? (actions.getMonitorDisplay() || {}) : {};
  const counters = disp.defaultCounters || {};
  const showTotal = counters.showTotal !== false;
  const showGood = counters.showGood !== false;
  const showBad = counters.showBad !== false;
  const showNgSteps = counters.showNgSteps !== false && disp.ngTop3 !== false;
  const showPie = disp.defectChart !== false;
  const showGauge = disp.capacityChart !== false;
  const statsPanelOn = disp.statsPanel !== false;

  const numberBox = (label, value, valueCls) =>
    h("div", { class: "fjjl-stat-box" }, [
      h("div", { class: "label" }, label),
      h("div", { class: `value ${valueCls || ""}` }, String(value)),
    ]);

  const gridChildren = [];
  if (showTotal) gridChildren.push(numberBox("检测次数", total));
  if (showGood) gridChildren.push(numberBox("OK 次数", ok, "is-ok"));
  if (showBad) gridChildren.push(numberBox("NG 次数", ng, "is-ng"));
  if (showPie) {
    gridChildren.push(h("div", { class: "fjjl-stat-box is-chart is-rings" }, [
      h("div", { class: "label" }, "良品 / 不良"),
      ChartsBox ? h(ChartsBox, { type: "pie", ok, ng }) : buildPieFallback(h, ok, ng),
    ]));
  }
  if (showGauge) {
    gridChildren.push(h("div", { class: "fjjl-stat-box is-chart is-gauge" }, [
      h("div", { class: "label" }, "合格率"),
      ChartsBox ? h(ChartsBox, { type: "gauge", yieldRate }) : buildGaugeFallback(h, yieldRate),
    ]));
  }
  if (showNgSteps) {
    gridChildren.push(h("div", { class: "fjjl-stat-box is-ngsteps" }, [
      h("div", { class: "label" }, "NG 步骤 TOP"),
      ngSteps.length > 0
        ? h("ol", { class: "fjjl-ngsteps-list" },
            ngSteps.map((sObj) =>
              h("li", null,
                `${sObj.label || sObj.name || sObj.step || "—"} (${sObj.count || sObj.ng_count || 0})`
              )
            )
          )
        : h("div", { class: "fjjl-ngsteps-empty" }, "—"),
    ]));
  }

  return h("section", { class: "fjjl-stats-cell" }, [
    h("div", { class: "fjjl-stats-cell-header" }, [
      h("span", null, `工位 ${chIdx + 1} 数据`),
      h("span", { class: "fjjl-stats-ct" }, `CT: ${ctText}`),
    ]),
    statsPanelOn ? h("div", { class: "fjjl-stats-grid" }, gridChildren) : null,
    buildChannelControls(h, chIdx, chData, actions, currentProject),
  ]);
}

// ==================== SOP 流程卡片串 (工位 1 → 工位 2 拼接) ====================
//   - 仿主程序 Monitor SOP 卡片样式 (border + 标题 + 截图占位 + 闪烁 active)
//   - GW1 步骤 + GW2 步骤拼成一条, 工位 1 / 工位 2 用顶部徽章区分
function getStatusClasses(status) {
  if (status === "ng" || status === "failed") {
    return { card: "is-ng", header: "is-ng", body: "is-ng" };
  }
  if (status === "ok" || status === "completed") {
    return { card: "is-ok", header: "is-ok", body: "is-ok" };
  }
  if (status === "active" || status === "current") {
    return { card: "is-active", header: "is-active", body: "is-active" };
  }
  return { card: "is-pending", header: "is-pending", body: "is-pending" };
}

function buildSopSection(h, ch1Data, ch2Data, actions) {
  // v1.1.7: 接入"显示设置"步骤条开关 (display.monitor.stepStrip), 与原生 Monitor 一致
  const disp = typeof actions?.getMonitorDisplay === "function" ? (actions.getMonitorDisplay() || {}) : {};
  if (disp.stepStrip === false) return null;
  const safe1 = ch1Data || {};
  const safe2 = ch2Data || {};
  const proj1 = safe1.projectName || "";
  const proj2 = safe2.projectName || "";
  const steps1 = Array.isArray(safe1.steps) ? safe1.steps : [];
  const steps2 = Array.isArray(safe2.steps) ? safe2.steps : [];

  const projDisplay = proj1 && proj2
    ? (proj1 === proj2 ? proj1 : `${proj1} → ${proj2}`)
    : (proj1 || proj2 || "未绑定项目");

  // 把 [GW1 steps, GW2 steps] 平铺成 9 张卡片 (每张带 stationIdx 标识)
  const cards = [];
  steps1.forEach((s, idx) => cards.push({ step: s, stationIdx: 0, idx }));
  steps2.forEach((s, idx) => cards.push({ step: s, stationIdx: 1, idx }));

  const nodes = [];
  if (cards.length === 0) {
    nodes.push(h("div", { class: "fjjl-sop-empty" }, "未绑定项目 / 无工序"));
  } else {
    cards.forEach((card, i) => {
      const s = card.step;
      const cls = getStatusClasses(s.status);
      const screenshot = s.screenshot || s.image || null;
      // 工位 1 → 工位 2 分界处加分隔
      const boundary = i > 0 && cards[i - 1].stationIdx !== card.stationIdx;
      if (boundary) {
        nodes.push(h("div", { class: "fjjl-sop-boundary" }, [
          h("span", { class: "fjjl-sop-boundary-line" }),
          h("span", { class: "fjjl-sop-boundary-label" }, "工位切换"),
          h("span", { class: "fjjl-sop-boundary-line" }),
        ]));
      } else if (i > 0) {
        nodes.push(h("div", { class: "fjjl-sop-arrow" }, "→"));
      }
      nodes.push(
        h("div", { class: `fjjl-sop-card ${cls.card}` }, [
          h("div", { class: `fjjl-sop-header ${cls.header}` }, [
            h("span", { class: "fjjl-sop-stationtag" }, `GW${card.stationIdx + 1}`),
            h("span", { class: "fjjl-sop-name", title: s.label || s.name || "" },
              s.label || s.name || `步骤 ${card.idx + 1}`),
          ]),
          h("div", { class: `fjjl-sop-body ${cls.body}` }, [
            screenshot
              ? h("div", { class: "fjjl-sop-img-wrap" }, [
                  h("img", { class: "fjjl-sop-img", src: screenshot, alt: "" }),
                ])
              : h("div", { class: "fjjl-sop-noimg" }, "📷"),
            s.status === "active" || s.status === "current"
              ? h("div", { class: "fjjl-sop-pulse" })
              : null,
          ]),
        ]),
      );
    });
  }

  return h("section", { class: "fjjl-sop-section" }, [
    h("div", { class: "fjjl-sop-header-row" }, [
      h("span", { class: "title" }, "SOP 流程卡片"),
      h("span", { class: "proj" }, projDisplay),
    ]),
    h("div", { class: "fjjl-sop-track" }, nodes),
  ]);
}

// ==================== 底部: 步骤统计表格 (双工位合并 9+ 行) + 4 按钮 ====================
//   - 上 80%: 5 列表格 (No / 步骤 / 状态 / PT/s / 结果), GW1 全部步骤 → GW2 全部步骤
//   - 下 20%: 4 个控制按钮一排
//   数据来源: ch.tableData ({ label, step, status, cycleResult }) + ch.cycleSumStepDurations[label]
function _fmtPT(chData, label, status, isTrk, actions) {
  if (typeof actions?.formatStepPTForChannel === "function") {
    const chIdx = chData?._chIdx;
    if (typeof chIdx === "number") {
      return actions.formatStepPTForChannel(chIdx, label);
    }
  }
  // fallback: 与主程序 formatStepPT() 守门口径对齐
  if (status !== "completed" && !isTrk) return "--";
  const map = chData?.cycleSumStepDurations || {};
  const v = map[label];
  if (typeof v !== "number" || !isFinite(v)) return "--";
  return v.toFixed(1);
}
function _resultNode(h, status, cycleResult, isTrk) {
  if (status === "completed" || isTrk) {
    if (cycleResult === "ok") return h("span", { class: "res-ok" }, "OK");
    if (cycleResult === "ng") return h("span", { class: "res-ng" }, "NG");
  }
  return h("span", { class: "res-empty" }, "--");
}
function _ptCell(h, R, actions) {
  const r = R.row || {};
  const label = r.label || r.step || "";
  const step = { ...r, label, step: r.step || label };
  const ptText = (R.isTrk || r.status === "completed")
    ? _fmtPT(R.chData, label, r.status, R.isTrk, actions)
    : "--";
  const render = actions?.renderStepCellDuration;
  if (typeof render === "function") {
    return render({
      step,
      label,
      status: r.status,
      isTrackingMode: R.isTrk,
      ptText,
      channelIdx: R.chData?._chIdx,
    });
  }
  return ptText;
}

function _resultCell(h, R, actions) {
  const r = R.row || {};
  const label = r.label || r.step || "";
  const step = { ...r, label, step: r.step || label };
  const render = actions?.renderStepCellStatus;
  if (typeof render === "function") {
    return render({
      step,
      label,
      status: r.status,
      cycleResult: r.cycleResult,
      isTrackingMode: R.isTrk,
      channelIdx: R.chData?._chIdx,
    });
  }
  return _resultNode(h, r.status, r.cycleResult, R.isTrk);
}
function _statusNode(h, status) {
  if (status === "completed") return h("span", { class: "st-completed" }, "已检测");
  if (status === "active" || status === "current") return h("span", { class: "st-active" }, "检测中");
  return h("span", { class: "st-pending" }, "待检测");
}

function buildBottomCell(h, ch1Data, ch2Data, allDetectingRef, anyRunningRef, handlers, actions) {
  const safe1 = ch1Data || {};
  const safe2 = ch2Data || {};
  const isTrk1 = !!(safe1.tracking && safe1.tracking.enabled);
  const isTrk2 = !!(safe2.tracking && safe2.tracking.enabled);
  const a = actions || {};
  const cols = typeof a.getStepTableColumns === "function" ? a.getStepTableColumns() : {};
  const showTable = typeof a.isStepTableEnabled !== "function" || a.isStepTableEnabled();
  const showNo = cols.showNo !== false;
  const showStep = cols.showStep !== false;
  const showStatus = cols.showStatus !== false;
  const showPt = cols.showPt !== false;
  const showResult = cols.showResult !== false;

  const td1 = Array.isArray(safe1.tableData) ? safe1.tableData : [];
  const td2 = Array.isArray(safe2.tableData) ? safe2.tableData : [];

  // 把 GW1, GW2 步骤拼成一条编号从 1 起的合并表
  const rows = [];
  td1.forEach((s) => rows.push({ stationIdx: 0, chData: { ...safe1, _chIdx: 0 }, isTrk: isTrk1, row: s }));
  td2.forEach((s) => rows.push({ stationIdx: 1, chData: { ...safe2, _chIdx: 1 }, isTrk: isTrk2, row: s }));

  const parseCt = (s) => {
    if (!s || s === "--") return 0;
    return parseFloat(String(s).replace(/s$/, "")) || 0;
  };
  const ct1 = typeof a.getDisplayCT === "function" ? a.getDisplayCT(safe1) : "--";
  const ct2 = typeof a.getDisplayCT === "function" ? a.getDisplayCT(safe2) : "--";
  const ctMax = Math.max(parseCt(ct1), parseCt(ct2));
  const ctDisplay = ctMax > 0 ? `${ctMax.toFixed(1)}s` : "--";

  const trNodes = rows.length === 0
    ? [h("tr", null, [h("td", { colspan: 5, class: "fjjl-stepstats-empty" }, "未绑定项目 / 无步骤")])]
    : rows.map((R, i) => {
        const r = R.row || {};
        const label = r.label || r.step || "";
        const stationTag = R.stationIdx === 0
          ? h("span", { class: "stationtag stationtag-gw1" }, "GW1")
          : h("span", { class: "stationtag stationtag-gw2" }, "GW2");
        const cells = [];
        if (showNo) cells.push(h("td", { class: "col-num" }, String(i + 1)));
        if (showStep) cells.push(h("td", null, [stationTag, label]));
        if (showStatus) cells.push(h("td", { class: "col-state" }, [_statusNode(h, r.status)]));
        if (showPt) {
          const hidden = typeof a.isMonitorSlotHidden === "function"
            && a.isMonitorSlotHidden("monitor.step-cell.duration");
          if (!hidden) {
            cells.push(h("td", { class: "col-pt", "data-slot": "monitor.step-cell.duration" },
              [_ptCell(h, R, a)]));
          }
        }
        if (showResult) {
          const hidden = typeof a.isMonitorSlotHidden === "function"
            && a.isMonitorSlotHidden("monitor.step-cell.status");
          if (!hidden) {
            cells.push(h("td", { class: "col-res", "data-slot": "monitor.step-cell.status" },
              [_resultCell(h, R, a)]));
          }
        }
        return h("tr", { class: r.status === "completed" ? "is-completed" : "" }, cells);
      });

  const theadCells = [];
  if (showNo) theadCells.push(h("th", { class: "col-num" }, "No"));
  if (showStep) theadCells.push(h("th", null, "步骤"));
  if (showStatus) theadCells.push(h("th", { class: "col-state" }, "状态"));
  if (showPt) theadCells.push(h("th", { class: "col-pt" }, "PT/s"));
  if (showResult) theadCells.push(h("th", { class: "col-res" }, "结果"));

  const tableSection = showTable
    ? h("div", { class: "fjjl-stepstats-area" }, [
        h("div", { class: "fjjl-stepstats-head" }, [
          h("span", { class: "title" }, "步骤统计 (双工位合并)"),
          h("span", { class: "ct" }, `CT: ${ctDisplay}`),
        ]),
        h("div", { class: "fjjl-stepstats-table-wrap" }, [
          h("table", { class: "fjjl-stepstats-table" }, [
            h("thead", null, [h("tr", null, theadCells)]),
            h("tbody", null, trNodes),
          ]),
        ]),
      ])
    : null;

  return h("section", { class: "fjjl-bottom-cell" }, [
    tableSection,
    h("div", { class: "fjjl-btn-row" }, [
      h("button", {
        class: "fjjl-btn is-start",
        disabled: allDetectingRef.value,
        onClick: handlers.start,
      }, "开始"),
      h("button", {
        class: "fjjl-btn is-stop",
        disabled: !anyRunningRef.value,
        onClick: handlers.stop,
      }, "停止"),
      h("button", {
        class: "fjjl-btn is-standby",
        disabled: !anyRunningRef.value,
        onClick: handlers.standby,
      }, "待机"),
      h("button", {
        class: "fjjl-btn is-reset",
        onClick: handlers.reset,
      }, "清零"),
    ]),
  ]);
}

// ==================== 顶层组件 build ====================
function buildDualStationMonitor(host) {
  const { defineComponent, h, computed, ref, onBeforeUnmount } = host.vue;
  // 只在 host.echarts 存在时构造 ChartsBox 子组件
  const ChartsBox = host.echarts ? buildEChartsBox(host) : null;
  const VideoCell = buildVideoCellComponent(host);

  return defineComponent({
    name: "FujianJinlongDualStationMonitor",

    props: {
      channelCount: { type: Number, default: 1 },
      multiChannelData: { type: Object, default: () => ({}) },
      selectedChannel: { type: Number, default: 0 },
      channelModelStats: { type: Object, default: () => ({}) },
      currentProject: { type: Object, default: null },
      actions: { type: Object, default: () => ({}) },
      streamUrlBuilder: { type: Function, default: null },
    },

    emits: ["update:selectedChannel"],

    setup(props, { emit }) {
      const isApplicable = computed(() => props.channelCount === 2);

      const stream0Url = ref("");
      const stream1Url = ref("");
      const refreshUrls = () => {
        if (!props.streamUrlBuilder) return;
        try {
          stream0Url.value = props.streamUrlBuilder(0);
          stream1Url.value = props.streamUrlBuilder(1);
        } catch (e) {
          console.warn("[fujian-jinlong] streamUrlBuilder 抛错:", e);
        }
      };
      refreshUrls();
      setInterval(refreshUrls, 30000);

      const handleStart = async () => {
        const a = props.actions || {};
        if (typeof a.startDetectionForChannel !== "function") return;
        try {
          await a.startDetectionForChannel(0);
          await a.startDetectionForChannel(1);
        } catch (e) { console.warn("[fujian-jinlong] 开始失败:", e); }
      };
      const handleStop = async () => {
        const a = props.actions || {};
        if (typeof a.stopDetectionForChannel !== "function") return;
        await a.stopDetectionForChannel(0);
        await a.stopDetectionForChannel(1);
      };
      const handleStandby = async () => {
        const a = props.actions || {};
        if (typeof a.standbyForChannel !== "function") return;
        await a.standbyForChannel(0);
        await a.standbyForChannel(1);
      };
      const handleReset = async () => {
        const a = props.actions || {};
        if (typeof a.resetCountersForChannel !== "function") return;
        await a.resetCountersForChannel(0);
        await a.resetCountersForChannel(1);
      };

      const allDetecting = computed(() =>
        !!(props.multiChannelData[0]?.isDetecting && props.multiChannelData[1]?.isDetecting)
      );
      const anyRunning = computed(() =>
        !!(props.multiChannelData[0]?.isRunning || props.multiChannelData[1]?.isRunning)
      );

      // ==================== 权威实时合格/不良 (覆盖主程序计数卡) ====================
      // 主程序那张实时计数卡走事件计数动作, 被本插件 pre_cycle_end 判废的周期仍被记成
      // 合格 (主程序计数语义改写未跟进). 这里轮询本插件后端 /durations/live-stats
      // (直接从库里 is_good 重算, 改写已落库), 把对的 OK/NG 覆盖进统计卡. 纯插件, 不动主程序.
      const authStats = ref({});        // { "0": {total,ok,ng,yield_rate}, "1": {...} }
      let _statsTimer = null;
      const pollAuthStats = async () => {
        if (!host.api || typeof host.api.get !== "function") return;
        try {
          const resp = await host.api.get("/plugins/internal-demo/durations/live-stats");
          const ch = resp?.data?.channels;
          if (ch && typeof ch === "object") authStats.value = ch;
        } catch (e) { /* 降级: 后端没起/路由缺失 → 回退主程序计数, 不报错 */ }
      };
      pollAuthStats();
      _statsTimer = setInterval(pollAuthStats, 1500);
      onBeforeUnmount(() => { if (_statsTimer) clearInterval(_statsTimer); });

      return () => {
        if (!isApplicable.value) return null;

        const ch1Data = props.multiChannelData[0];
        const ch2Data = props.multiChannelData[1];

        return h("div", { class: "fjjl-monitor" }, [
          h("main", { class: "fjjl-grid" }, [
            // 上两行: 视频 + 统计 × 2 (两个工位)
            h(VideoCell, {
              chIdx: 0,
              chData: ch1Data,
              streamUrl: stream0Url.value,
              streamUrlBuilder: props.streamUrlBuilder,
              actions: props.actions,
              selected: props.selectedChannel === 0,
              channelModelStats: props.channelModelStats[0] || [],
              onSelect: () => emit("update:selectedChannel", 0),
              onRefreshStream: () => { stream0Url.value = props.streamUrlBuilder(0); },
            }),
            buildStatsCell(h, 0, ch1Data, ChartsBox, props.actions, props.currentProject, authStats.value["0"]),
            h(VideoCell, {
              chIdx: 1,
              chData: ch2Data,
              streamUrl: stream1Url.value,
              streamUrlBuilder: props.streamUrlBuilder,
              actions: props.actions,
              selected: props.selectedChannel === 1,
              channelModelStats: props.channelModelStats[1] || [],
              onSelect: () => emit("update:selectedChannel", 1),
              onRefreshStream: () => { stream1Url.value = props.streamUrlBuilder(1); },
            }),
            buildStatsCell(h, 1, ch2Data, ChartsBox, props.actions, props.currentProject, authStats.value["1"]),
            // 第三行: SOP 流程卡片 (跨两列)
            buildSopSection(h, ch1Data, ch2Data, props.actions),
            // 第四行: 底部统计 + 4 按钮 (跨两列)
            buildBottomCell(h, ch1Data, ch2Data, allDetecting, anyRunning, {
              start: handleStart,
              stop: handleStop,
              standby: handleStandby,
              reset: handleReset,
            }, props.actions),
          ]),
        ]);
      };
    },
  });
}

// ==================== 插件入口 ====================
export default {
  async register({ host, registry }) {
    const DualStationMonitor = buildDualStationMonitor(host);
    if (registry.slots && typeof registry.slots.register === "function") {
      registry.slots.register("monitor.layout.body", DualStationMonitor);
      _registeredSlot = "monitor.layout.body";
      console.log("[fujian-jinlong] monitor.layout.body slot 已注册 (v3 layout, echarts=" + !!host.echarts + ")");
    } else {
      console.warn("[fujian-jinlong] registry.slots 不可用, 主程序可能 < v3.13.1");
      return { loaded: false, reason: "slots-api-missing" };
    }
    return { loaded: true, slot: _registeredSlot };
  },

  async unregister({ registry }) {
    if (_registeredSlot && registry?.slots?.unregister) {
      registry.slots.unregister(_registeredSlot);
      _registeredSlot = null;
    }
  },
};
