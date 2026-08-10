# -*- coding: utf-8 -*-
"""训练平台互连 (v3.47, interconnect-contract 1.0).

与公司 YoloVision 训练平台的双向互连:
- 模型通路 (对方推 → 我方收): .yvmodel 包接收/校验/入模型仓库, 见 package_ingest
- 样本通路 (我方推 → 对方收): 现场推理帧按置信度带/未检出/NG 采样,
  磁盘队列异步回传, 见 sampler + sample_queue + uploader
- 配置: SystemConfig KV 'interconnect.config', 见 config

契约文档在训练平台仓库 TIANJUN_INTERCONNECT_SPEC.md（双方共同事实源）。
"""
