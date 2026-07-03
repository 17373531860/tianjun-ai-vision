// ==================== 推理格式显示名映射（2026-07 拆分批次 P-2 自 index.vue 外置） ====================
// index.vue（基础设置 Tab / 副模型卡片 / 转换提示）与 FormatSelectDialog.vue 共用。
export const FORMAT_DISPLAY_NAMES = {
  'pytorch_fp32': 'PyTorch FP32',
  'pytorch_fp16': 'PyTorch FP16',
  'onnx': 'ONNX',
  'torchscript': 'TorchScript',
  'tensorrt_fp32': 'TensorRT FP32',
  'tensorrt_fp16': 'TensorRT FP16',
  'tensorrt_int8': 'TensorRT INT8',
};

export const getFormatDisplayName = (key) => FORMAT_DISPLAY_NAMES[key] || key;
