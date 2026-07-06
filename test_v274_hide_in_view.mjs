// v2.7.4 物品隐藏标注框 (hide_in_view) - 前端逻辑离线测试
// v3.31.x 语义收窄: hide_in_view 只隐藏实时画面的检测框;
//   SOP 卡片 / 步骤详情 / 步骤统计照常显示照常统计 (过滤条件不再含 hide_in_view)。
// 跑法: node test_v274_hide_in_view.mjs
//
// 不依赖浏览器, 不依赖 Vue, 直接验证过滤/画框逻辑等价物.

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { console.log(`  ✓ ${msg}`); pass++; }
  else { console.log(`  ✗ ${msg}`); fail++; }
}
function hr(t) { console.log("\n" + "=".repeat(60) + "\n" + t + "\n" + "=".repeat(60)); }


// ---- 测试夹具：模拟一份 steps_config ----
const stepsConf = [
  { id: 1, label: "screw1",  displayLabel: "螺丝1", enabled: true,  hide_in_view: false, is_backup: false, backup_for: "" },
  { id: 2, label: "screw2",  displayLabel: "螺丝2", enabled: true                                                       },  // hide_in_view 缺省
  { id: 3, label: "box",     displayLabel: "箱子",   enabled: true,  hide_in_view: true,  is_backup: false              },  // 隐藏画框
  { id: 4, label: "foam",    displayLabel: "泡沫槽", enabled: true,  hide_in_view: true,  is_backup: false              },  // 隐藏画框
  { id: 5, label: "label",   displayLabel: "标签",   enabled: true,  hide_in_view: false, is_backup: false              },
  { id: 6, label: "disabled",displayLabel: "禁用项", enabled: false, hide_in_view: false                                 },
  { id: 7, label: "backup1", displayLabel: "替补",   enabled: true,  hide_in_view: false, is_backup: true               },
  { id: 8, label: "bf",      displayLabel: "替补2",  enabled: true,  hide_in_view: false, backup_for: "screw1"          },
];


// =============================================================
// Test A: 多工位 SOP/步骤详情 filter (Monitor processChannelResult)
// v3.31.x: 过滤条件只剩 enabled / is_backup, hide_in_view 的步骤照常进 SOP/步骤详情
// =============================================================
hr("Test A: 多工位 filter - 隐藏画框的步骤仍进 SOP/步骤详情");
{
  const td = stepsConf.filter(s => s.enabled !== false && !s.is_backup);
  const labels = td.map(s => s.label);
  console.log("  通过 filter 的:", labels);
  assert(labels.includes("screw1"), "screw1 (正常步骤) 通过");
  assert(labels.includes("screw2"), "screw2 (hide_in_view 缺省 = falsy) 通过");
  assert(labels.includes("label"),  "label 通过");
  assert(labels.includes("box"),    "box (hide_in_view=true) 仍显示 (v3.31.x 语义收窄)");
  assert(labels.includes("foam"),   "foam (hide_in_view=true) 仍显示 (v3.31.x 语义收窄)");
  assert(!labels.includes("disabled"), "disabled (enabled=false) 被过滤");
  assert(!labels.includes("backup1"),  "backup1 (is_backup=true) 被过滤");
}


// =============================================================
// Test B: 单工位 stepsToShow filter (Monitor watch newProject)
// v3.31.x: 只再过 backup_for 一道, hide_in_view 不再剔除
// 单工位 stepsToShow 已经先用 .filter(s => s.enabled) 过过
// =============================================================
hr("Test B: 单工位 stepsToShow 二次过滤 - hide_in_view 不再剔除");
{
  const enabled = stepsConf.filter(s => s.enabled);
  const stepsToShow = enabled.filter(s => !s.backup_for);
  const labels = stepsToShow.map(s => s.label);
  console.log("  最终展示:", labels);
  assert(labels.includes("screw1"), "screw1 通过");
  assert(labels.includes("screw2"), "screw2 通过");
  assert(labels.includes("label"),  "label 通过");
  assert(labels.includes("backup1"),"backup1 (is_backup=true 但无 backup_for) 通过 - 与多工位语义一致问题, 但符合源码原行为");
  assert(labels.includes("box"),    "box (hide_in_view=true) 仍显示 (v3.31.x 语义收窄)");
  assert(labels.includes("foam"),   "foam (hide_in_view=true) 仍显示 (v3.31.x 语义收窄)");
  assert(!labels.includes("bf"),    "bf (backup_for 非空) 被过滤");
  assert(!labels.includes("disabled"), "disabled 被 enabled 那道过滤掉");
}


// =============================================================
// Test C: drawMultiDetections 画框跳过 (不变: 画面上仍不画 hide_in_view 的框)
// =============================================================
hr("Test C: drawMultiDetections 跳过 hide_in_view 的 detection");
{
  const detections = [
    { label: "screw1", x: 0.1, y: 0.1, w: 0.1, h: 0.1, hidden: false },
    { label: "screw2", x: 0.2, y: 0.2, w: 0.1, h: 0.1, hidden: false },
    { label: "box",    x: 0.3, y: 0.3, w: 0.2, h: 0.2, hidden: false },  // 应被跳过
    { label: "foam",   x: 0.4, y: 0.4, w: 0.2, h: 0.2, hidden: false },  // 应被跳过
    { label: "label",  x: 0.5, y: 0.5, w: 0.1, h: 0.1, hidden: false },
    { label: "screw1", x: 0.6, y: 0.6, w: 0.1, h: 0.1, hidden: true  },  // 后端 backup_for 隐藏
  ];
  // 等价于 processChannelResult 那段 hiddenLabels 构造
  const hiddenLabels = new Set(
    stepsConf.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
  );
  console.log("  hiddenLabels:", [...hiddenLabels]);

  // 等价于 drawMultiDetections forEach 内的过滤
  const drawn = detections.filter(det => {
    if (det.hidden) return false;
    if (hiddenLabels && det.label && hiddenLabels.has(det.label)) return false;
    return true;
  });
  const drawnLabels = drawn.map(d => d.label);
  console.log("  实际画的:", drawnLabels);

  assert(drawnLabels.includes("screw1"), "screw1 被画");
  assert(drawnLabels.includes("screw2"), "screw2 (hide_in_view 缺省) 被画");
  assert(drawnLabels.includes("label"),  "label 被画");
  assert(!drawnLabels.includes("box"),   "box (hide_in_view=true) 不画");
  assert(!drawnLabels.includes("foam"),  "foam (hide_in_view=true) 不画");

  // 另一个 screw1 (backend hidden=true) 也应该不画
  const screw1Count = drawnLabels.filter(l => l === "screw1").length;
  assert(screw1Count === 1, "backend hidden=true 的 detection 也仍被跳过 (兼容性)");
}


// =============================================================
// Test D: 单工位 drawDetections (不变: 画面上仍不画 hide_in_view 的框)
// =============================================================
hr("Test D: 单工位 drawDetections 跳过 hide_in_view");
{
  const enabledLabels = new Set(
    stepsConf.filter(s => s.enabled !== false).map(s => s.label)
  );
  const hiddenLabels = new Set(
    stepsConf.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
  );
  const detections = [
    { label: "screw1" },
    { label: "box"    },     // 隐藏
    { label: "foam"   },     // 隐藏
    { label: "disabled"},    // 不在 enabledLabels 里
    { label: "label"  },
    { label: "unknown_class" },  // 不在 enabled 里 (老 YOLO 误检)
  ];
  const drawn = detections.filter(det => {
    if (!enabledLabels.has(det.label)) return false;
    if (det.hidden) return false;
    if (hiddenLabels.has(det.label)) return false;
    return true;
  });
  const drawnLabels = drawn.map(d => d.label);
  console.log("  实际画的:", drawnLabels);
  assert(drawnLabels.includes("screw1"), "screw1 被画");
  assert(drawnLabels.includes("label"),  "label 被画");
  assert(!drawnLabels.includes("box"),   "box 不画");
  assert(!drawnLabels.includes("foam"),  "foam 不画");
  assert(!drawnLabels.includes("disabled"), "disabled 不画 (原有行为)");
  assert(!drawnLabels.includes("unknown_class"), "unknown_class 不画 (原有行为)");
}


// =============================================================
// Test E: 兼容性 - 旧项目无 hide_in_view 字段
// =============================================================
hr("Test E: 旧项目 steps_config 无 hide_in_view 字段时的兼容性");
{
  const oldStepsConf = [
    { id: 1, label: "a", enabled: true },
    { id: 2, label: "b", enabled: true },
    { id: 3, label: "c", enabled: false },
  ];
  // 多工位 filter (v3.31.x: 不再含 hide_in_view)
  const td = oldStepsConf.filter(s => s.enabled !== false && !s.is_backup);
  assert(td.length === 2, "旧项目 => 过滤只看 enabled/is_backup (a, b 通过)");
  // 画框 hiddenLabels 构造
  const hiddenLabels = new Set(
    oldStepsConf.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
  );
  assert(hiddenLabels.size === 0, "旧项目 hiddenLabels 为空, 所有 detection 正常画");
}


// =============================================================
// Test F: 边界 - 模型给出新 detection 但 steps_config 里没有这个 label
// =============================================================
hr("Test F: 边界 - YOLO 误检不在 steps_config 中的 label");
{
  const hiddenLabels = new Set(
    stepsConf.filter(s => s && s.hide_in_view && s.label).map(s => s.label)
  );
  const detections = [
    { label: "screw1" },
    { label: "ghost_class" },  // YOLO 误检的不在配置里的类别
  ];
  // 多工位 drawMultiDetections (无 enabledLabels 过滤, 仅 hiddenLabels)
  const multiDrawn = detections.filter(det => {
    if (det.hidden) return false;
    if (hiddenLabels.has(det.label)) return false;
    return true;
  });
  assert(multiDrawn.length === 2, "多工位: 即使 ghost_class 不在配置里也会画 (与 v2.7.3 行为一致)");
}


// =============================================================
hr("总结");
console.log(`  通过: ${pass}, 失败: ${fail}`);
process.exit(fail === 0 ? 0 : 1);
