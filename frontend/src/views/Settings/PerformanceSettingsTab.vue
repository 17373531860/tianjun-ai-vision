<template>
        <div class="space-y-6 p-4">
          <!-- WS5(PG): 数据库信息卡片 (只读展示) -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-green-400"><Coin /></el-icon>
                <span class="font-bold text-white">数据库</span>
                <el-tag v-if="dbInfo" size="small" :type="dbInfo.dialect === 'postgresql' ? 'success' : 'info'" data-testid="db-dialect-tag">
                  {{ dbInfo.dialect === 'postgresql' ? 'PostgreSQL' : 'SQLite' }}
                </el-tag>
                <el-button size="small" text class="ml-auto" @click="loadDbInfo">刷新</el-button>
              </div>
            </template>
            <div v-if="dbInfo" class="space-y-2 text-sm">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-400">位置</span>
                <span class="text-gray-200 font-mono text-xs break-all">{{ dbInfo.location || '—' }}</span>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-400">服务端版本</span>
                <span class="text-gray-200">{{ dbInfo.server_version || '—' }}</span>
              </div>
              <div v-if="dbInfo.dialect !== 'postgresql' && dbInfo.file_size_bytes != null"
                   class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-400">文件大小（含 WAL）</span>
                <span class="text-gray-200">{{ (dbInfo.file_size_bytes / 1024 / 1024).toFixed(1) }} MB</span>
              </div>
              <div v-if="dbInfo.pool" class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <span class="text-gray-400">连接池</span>
                <span class="text-gray-200">{{ dbInfo.pool.checked_out }} 使用中 / {{ dbInfo.pool.size }} 池容量（溢出 {{ dbInfo.pool.overflow }}）</span>
              </div>
              <el-alert v-if="dbInfo.dialect !== 'postgresql'" type="info" :closable="false" show-icon>
                <template #default>
                  <div class="text-xs text-gray-300">
                    切换 PostgreSQL：在用户数据目录放置 <code>db_config.json</code>（内容
                    <code>{"database_url": "postgresql+psycopg2://用户:密码@主机:端口/库名"}</code>），
                    先用迁移工具搬数据并校验，再重启应用生效。
                  </div>
                </template>
              </el-alert>
            </div>
            <div v-else class="text-gray-500 text-sm p-3">数据库信息加载中…</div>
          </el-card>

          <!-- 视频流设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-tech-blue"><VideoCamera /></el-icon>
                <span class="font-bold text-white">视频流设置</span>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用帧率限制</span>
                  <div class="text-xs text-gray-500 mt-1">本地应用建议关闭以获得最低延迟</div>
                </div>
                <el-switch v-model="store.performance.frameLimitEnabled" @change="savePerformanceSettings" />
              </div>
              <div v-if="store.performance.frameLimitEnabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-2">目标帧率 (FPS)</div>
                <el-slider 
                  v-model="store.performance.targetStreamFps" 
                  :min="0" 
                  :step="5" 
                  show-stops
                  :marks="{10: '10', 30: '30', 60: '60'}"
                  @change="savePerformanceSettings" 
                />
                <div class="text-xs text-gray-500 mt-2">当前: {{ store.performance.targetStreamFps }} FPS</div>
              </div>
              <el-alert
                title="延迟优化说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>关闭帧率限制</strong>：有新帧立即发送，延迟最低（推荐本地使用）</p>
                    <p>• <strong>开启帧率限制</strong>：限制传输频率，适合低配电脑或远程使用</p>
                  </div>
                </template>
              </el-alert>
              <!-- D1: 多通道视频解码背压 (默认关) -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">多工位视频解码背压</span>
                  <div class="text-xs text-gray-500 mt-1">
                    多工位画面改用 createImageBitmap 解码 + "每工位同时只解一帧、跟不上就丢旧留最新"。<br>
                    长时间多工位运行不再因解码积压导致内存/GC 压力升高；关闭=旧版逐帧解码，行为一致。
                  </div>
                </div>
                <el-switch
                  v-model="store.performance.multiChannelBitmapDecode"
                  data-testid="multi-bitmap-decode-switch"
                  @change="savePerformanceSettings"
                />
              </div>
            </div>
          </el-card>

          <!-- 推理加速设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-yellow-400"><Lightning /></el-icon>
                <span class="font-bold text-white">推理加速</span>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">FP16 半精度推理</span>
                  <div class="text-xs text-gray-500 mt-1">开启后推理速度约提升 50%-100%，适用于 NVIDIA RTX 20/30/40/50 系列 GPU</div>
                </div>
                <el-switch v-model="store.performance.halfPrecision" @change="savePerformanceSettings" />
              </div>
              <el-alert
                title="说明"
                type="warning"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• 开启后使用 16 位浮点精度推理，大幅降低单帧推理耗时</p>
                    <p>• 检测精度几乎无损（差异 &lt; 0.1%），Ultralytics 官方推荐</p>
                    <p>• 仅在 GPU (CUDA) 设备上生效，CPU 推理不受影响</p>
                    <p>• 修改后<strong>下次加载模型时生效</strong>（重新开始检测即可）</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- MediaPipe 骨架叠加 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-purple-400"><Aim /></el-icon>
                <span class="font-bold text-white">MediaPipe 骨架叠加</span>
                <el-tag size="small" type="info">视觉增强</el-tag>
              </div>
            </template>
            <div class="space-y-4">
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用 MediaPipe 叠加</span>
                  <div class="text-xs text-gray-500 mt-1">在画面上实时显示人体骨架和手部关键点，纯视觉效果，不影响检测逻辑</div>
                </div>
                <el-switch v-model="store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
              </div>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">姿态骨架</span>
                  <el-switch v-model="store.performance.mediapipePose" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
                <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                  <span class="text-gray-300">手部关键点</span>
                  <el-switch v-model="store.performance.mediapipeHands" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
              </div>

              <!-- v3.32.0: 自定义纯色骨架样式 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800 space-y-3">
                <div class="flex items-center justify-between">
                  <div>
                    <span class="text-gray-300">自定义骨架样式</span>
                    <div class="text-xs text-gray-500 mt-1">开启后骨架用纯色绘制，颜色和线条粗细可自选；关闭 = MediaPipe 默认多彩配色</div>
                  </div>
                  <el-switch v-model="store.performance.mediapipeCustomStyle" :disabled="!store.performance.mediapipeEnabled" @change="savePerformanceSettings" />
                </div>
                <div v-if="store.performance.mediapipeCustomStyle" class="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">姿态连线颜色</span>
                    <el-color-picker
                      v-model="store.performance.mediapipePoseColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipePoseColor = '#00FF00'; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <div>
                      <span class="text-gray-400 text-sm">姿态关键点颜色</span>
                      <div class="text-xs text-gray-600">清空 = 跟随连线颜色</div>
                    </div>
                    <el-color-picker
                      v-model="store.performance.mediapipePosePointColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipePosePointColor = ''; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">姿态线条粗细</span>
                    <el-input-number
                      v-model="store.performance.mediapipePoseThickness"
                      size="small"
                      :min="1"
                      :max="10"
                      :precision="0"
                      :step="1"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="savePerformanceSettings"
                      style="width: 100px"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">手部连线颜色</span>
                    <el-color-picker
                      v-model="store.performance.mediapipeHandsColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipeHandsColor = '#00FF00'; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <div>
                      <span class="text-gray-400 text-sm">手部关键点颜色</span>
                      <div class="text-xs text-gray-600">清空 = 跟随连线颜色</div>
                    </div>
                    <el-color-picker
                      v-model="store.performance.mediapipeHandsPointColor"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="v => { if (!v) store.performance.mediapipeHandsPointColor = ''; savePerformanceSettings(); }"
                    />
                  </div>
                  <div class="flex items-center justify-between p-2 bg-slate-800 rounded border border-slate-700">
                    <span class="text-gray-400 text-sm">手部线条粗细</span>
                    <el-input-number
                      v-model="store.performance.mediapipeHandsThickness"
                      size="small"
                      :min="1"
                      :max="10"
                      :precision="0"
                      :step="1"
                      :disabled="!store.performance.mediapipeEnabled"
                      @change="savePerformanceSettings"
                      style="width: 100px"
                    />
                  </div>
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">检测置信度</span>
                  <div class="text-xs text-gray-500 mt-1">值越高误检越少但可能漏检（俯视角度建议 0.7-0.9）</div>
                </div>
                <div class="flex items-center gap-2">
                  <el-slider
                    v-model="store.performance.mediapipeConfidence"
                    :min="0"
                    :step="0.05"
                    :disabled="!store.performance.mediapipeEnabled"
                    @change="savePerformanceSettings"
                    style="width: 140px"
                    :show-tooltip="true"
                    :format-tooltip="v => v.toFixed(2)"
                  />
                  <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeConfidence.toFixed(2) }}</span>
                </div>
              </div>
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">处理间隔</span>
                  <div class="text-xs text-gray-500 mt-1">每隔 N 帧处理一次（值越大性能越好但骨架更新越慢）</div>
                </div>
                <el-input-number
                  v-model="store.performance.mediapipeInterval"
                  size="small"
                  :min="0"
                  :precision="0"
                  :step="1"
                  :controls="true"
                  :disabled="!store.performance.mediapipeEnabled"
                  @change="savePerformanceSettings"
                  style="width: 120px"
                />
              </div>

              <!-- v3.8.0: 一键预设 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">手部识别预设</span>
                  <div class="text-xs text-gray-500 mt-1">
                    省 CPU 模式 = 默认（complexity=0, 置信度 0.7）；高精度模式 = 友商同款（complexity=1, 置信度 0.5），手部识别率提升 3-4 倍但 CPU 占用增加约 80%
                  </div>
                </div>
                <div class="flex flex-col gap-2">
                  <el-button
                    size="small"
                    type="info"
                    plain
                    :disabled="!store.performance.mediapipeEnabled"
                    @click="applyMediaPipePreset('eco')"
                  >省 CPU 模式</el-button>
                  <el-button
                    size="small"
                    type="success"
                    plain
                    :disabled="!store.performance.mediapipeEnabled"
                    @click="applyMediaPipePreset('quality')"
                  >高精度模式</el-button>
                </div>
              </div>

              <!-- v3.8.0: 高级选项 -->
              <el-collapse class="border-slate-700">
                <el-collapse-item title="高级选项" name="mp-advanced">
                  <div class="space-y-4">
                    <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                      <div>
                        <span class="text-gray-300">手部模型档位</span>
                        <div class="text-xs text-gray-500 mt-1">
                          0 = 轻量版（推理快、识别黑手套/俯视等困难场景几乎无效）；1 = 完整版（推理慢约 1.8 倍、对手套/握工具等非标场景识别率显著提升）
                        </div>
                      </div>
                      <el-radio-group
                        v-model="store.performance.mediapipeModelComplexity"
                        size="small"
                        :disabled="!store.performance.mediapipeEnabled"
                        @change="savePerformanceSettings"
                      >
                        <el-radio-button :value="0">轻量(0)</el-radio-button>
                        <el-radio-button :value="1">完整(1)</el-radio-button>
                      </el-radio-group>
                    </div>
                    <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                      <div>
                        <span class="text-gray-300">跟踪置信度</span>
                        <div class="text-xs text-gray-500 mt-1">值越低骨架越"粘"住手不易丢失但更抖；首检后跟随期使用</div>
                      </div>
                      <div class="flex items-center gap-2">
                        <el-slider
                          v-model="store.performance.mediapipeTrackConfidence"
                          :min="0.05"
                          :max="0.95"
                          :step="0.05"
                          :disabled="!store.performance.mediapipeEnabled"
                          @change="savePerformanceSettings"
                          style="width: 140px"
                          :show-tooltip="true"
                          :format-tooltip="v => v.toFixed(2)"
                        />
                        <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeTrackConfidence.toFixed(2) }}</span>
                      </div>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>

              <!-- v3.8.0: 工业专用手部模型 (二段管线) -->
              <el-collapse class="border-slate-700">
                <el-collapse-item name="mp-industrial">
                  <template #title>
                    <div class="flex items-center gap-2">
                      <span class="text-gray-200 font-bold">工业专用手部模型</span>
                      <el-tag
                        v-if="mediapipeStatusState === 'baseline'"
                        size="small"
                        type="info"
                      >未启用</el-tag>
                      <el-tag
                        v-else-if="mediapipeStatusState === 'active'"
                        size="small"
                        type="success"
                      >已启用</el-tag>
                      <el-tag
                        v-else-if="mediapipeStatusState === 'pending'"
                        size="small"
                        type="warning"
                      >待加载</el-tag>
                      <el-tag
                        v-else
                        size="small"
                        type="danger"
                      >异常</el-tag>
                    </div>
                  </template>
                  <div class="space-y-4">
                    <el-alert
                      :title="mediapipeStatusMessage"
                      :type="mediapipeStatusAlertType"
                      :closable="false"
                      show-icon
                    >
                      <template #default>
                        <div class="text-xs text-gray-300 mt-1">
                          基础 MediaPipe 在黑手套俯视、握工具遮挡等"工业死区"场景识别率为零。配一个本地训练的 hand-detector
                          (YOLO .pt) 即可启用二段管线 (YOLO 框出手部位置 → ROI 切割 → MediaPipe 画骨架)。
                          训好的模型放本地任意路径，填到下方即可，<strong>不需要重启</strong>。
                        </div>
                      </template>
                    </el-alert>

                    <div class="p-3 bg-slate-900 rounded border border-slate-800">
                      <div class="text-gray-300 mb-2">模型文件路径</div>
                      <div class="text-xs text-gray-500 mb-2">
                        本地 .pt 文件绝对路径（例如 <code>D:/tianjun/models/industrial_hand.pt</code>）。留空 = 关闭二段管线，走基础 MediaPipe
                      </div>
                      <div class="flex gap-2">
                        <el-input
                          v-model="store.performance.mediapipeHandDetectorPath"
                          placeholder="留空走基础模式 / 填本地 .pt 路径启用专用模型"
                          :disabled="!store.performance.mediapipeEnabled"
                          clearable
                          @change="savePerformanceSettings"
                        />
                        <el-button
                          size="default"
                          :disabled="!store.performance.mediapipeEnabled"
                          @click="savePerformanceSettings"
                        >应用</el-button>
                      </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">模型格式</span>
                          <div class="text-xs text-gray-500 mt-1">YOLOv5 老格式选 v5，其余选 v8</div>
                        </div>
                        <el-radio-group
                          v-model="store.performance.mediapipeHandDetectorKind"
                          size="small"
                          :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                          @change="savePerformanceSettings"
                        >
                          <el-radio-button value="v8">v8</el-radio-button>
                          <el-radio-button value="v5">v5</el-radio-button>
                        </el-radio-group>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">检测置信度</span>
                          <div class="text-xs text-gray-500 mt-1">框越多→精度越低</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandDetectorConf"
                            :min="0.05"
                            :max="0.9"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 120px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeHandDetectorConf.toFixed(2) }}</span>
                        </div>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">NMS 阈值</span>
                          <div class="text-xs text-gray-500 mt-1">两个框重叠超此比例时去重</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandDetectorIou"
                            :min="0.1"
                            :max="0.9"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 120px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-8 text-right">{{ store.performance.mediapipeHandDetectorIou.toFixed(2) }}</span>
                        </div>
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                        <div>
                          <span class="text-gray-300">输入尺寸</span>
                          <div class="text-xs text-gray-500 mt-1">YOLO resize 后边长（640 兼顾速度/精度）</div>
                        </div>
                        <el-input-number
                          v-model="store.performance.mediapipeHandDetectorImgsz"
                          size="small"
                          :min="320"
                          :max="1280"
                          :step="32"
                          :precision="0"
                          :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                          @change="savePerformanceSettings"
                          style="width: 110px"
                        />
                      </div>
                      <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800 md:col-span-2">
                        <div>
                          <span class="text-gray-300">ROI 外扩比例</span>
                          <div class="text-xs text-gray-500 mt-1">手指容易被 YOLO 框切到 → 把框向外扩这个比例后再喂给 MediaPipe</div>
                        </div>
                        <div class="flex items-center gap-2">
                          <el-slider
                            v-model="store.performance.mediapipeHandRoiPad"
                            :min="0"
                            :max="1"
                            :step="0.05"
                            :disabled="!store.performance.mediapipeEnabled || !store.performance.mediapipeHandDetectorPath"
                            @change="savePerformanceSettings"
                            style="width: 180px"
                            :show-tooltip="true"
                            :format-tooltip="v => v.toFixed(2)"
                          />
                          <span class="text-gray-400 text-xs w-12 text-right">{{ (store.performance.mediapipeHandRoiPad * 100).toFixed(0) }}%</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </el-collapse-item>
              </el-collapse>

              <el-alert
                title="说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• MediaPipe 由 Google 开发，提供轻量级人体姿态和手部关键点检测</p>
                    <p>• 仅在画面显示中叠加骨架效果，<strong>不参与检测判定、不影响录制</strong></p>
                    <p>• 性能参考（CPU 处理）：省 CPU 模式约 18-22ms/帧；高精度模式约 30-38ms/帧；专用模型模式约 13-16ms/帧（更快）</p>
                    <p>• 需要安装 mediapipe 包：<code>pip install mediapipe</code></p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- GPU/推理设备设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <el-icon class="text-green-400"><Cpu /></el-icon>
                  <span class="font-bold text-white">推理设备 (GPU/CPU)</span>
                </div>
                <el-button type="primary" link @click="refreshGpuList" :loading="loadingGpu">
                  <el-icon class="mr-1"><Refresh /></el-icon> 刷新
                </el-button>
              </div>
            </template>
            <div class="space-y-4">
              <!-- CUDA状态显示 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between">
                  <span class="text-gray-300">CUDA 状态</span>
                  <el-tag :type="gpuInfo.cudaAvailable ? 'success' : 'danger'" size="small">
                    {{ gpuInfo.cudaAvailable ? `可用 (CUDA ${gpuInfo.cudaVersion})` : '不可用' }}
                  </el-tag>
                </div>
                <div v-if="gpuInfo.gpuCount > 0" class="text-xs text-gray-500 mt-1">
                  检测到 {{ gpuInfo.gpuCount }} 个GPU设备
                </div>
              </div>

              <!-- 设备选择 -->
              <div class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">默认推理设备</span>
                  <el-tag v-if="isDefaultDevice" type="success" size="small">已保存为默认</el-tag>
                </div>
                <el-select v-model="selectedDevice" class="w-full" @change="changeDevice" :loading="changingDevice">
                  <el-option
                    v-for="device in gpuInfo.devices"
                    :key="device.id"
                    :label="device.name"
                    :value="device.id"
                  >
                    <div class="flex items-center justify-between w-full">
                      <span>{{ device.name }}</span>
                      <el-tag v-if="device.type === 'GPU'" type="success" size="small">GPU</el-tag>
                      <el-tag v-else-if="device.type === 'CPU'" type="info" size="small">CPU</el-tag>
                      <el-tag v-else type="warning" size="small">自动</el-tag>
                    </div>
                  </el-option>
                </el-select>
                <div class="text-xs text-gray-500 mt-2">
                  选择后自动保存，下次启动将使用此设备
                </div>
              </div>

              <!-- 当前使用的设备 -->
              <div v-if="currentDeviceInfo" class="p-3 bg-slate-900 rounded border" 
                   :class="currentDeviceInfo.type === 'GPU' ? 'border-green-600' : 'border-slate-700'">
                <div class="flex items-center justify-between">
                  <span class="text-gray-300">当前推理设备</span>
                  <el-tag :type="currentDeviceInfo.type === 'GPU' ? 'success' : 'info'" size="small">
                    {{ currentDeviceInfo.type }}
                  </el-tag>
                </div>
                <div class="text-lg font-bold mt-1" :class="currentDeviceInfo.type === 'GPU' ? 'text-green-400' : 'text-gray-400'">
                  {{ currentDeviceInfo.name }}
                </div>
                <div class="text-xs text-gray-500 mt-1">设备: {{ currentDeviceInfo.device }}</div>
              </div>
              <div v-else class="p-3 bg-slate-900 rounded border border-slate-700 text-center text-gray-500">
                尚未加载模型，设备信息将在加载模型后显示
              </div>

              <el-alert
                title="GPU 推理说明"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>GPU推理</strong>：速度快（约10-50ms/帧），需要NVIDIA显卡和驱动</p>
                    <p>• <strong>CPU推理</strong>：速度慢（约100-300ms/帧），任何电脑都支持</p>
                    <p>• 切换设备后需要重新加载模型才能生效</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>

          <!-- 检测框滤波设置 -->
          <el-card shadow="never" class="bg-slate-800 border-slate-700">
            <template #header>
              <div class="flex items-center gap-2">
                <el-icon class="text-purple-400"><Box /></el-icon>
                <span class="font-bold text-white">检测框滤波 (卡尔曼滤波)</span>
              </div>
            </template>
            <div class="space-y-4">
              <!-- 启用开关 -->
              <div class="flex items-center justify-between p-3 bg-slate-900 rounded border border-slate-800">
                <div>
                  <span class="text-gray-300">启用卡尔曼滤波</span>
                  <div class="text-xs text-gray-500 mt-1">平滑检测框运动轨迹，减少抖动</div>
                </div>
                <el-switch v-model="kalmanConfig.enabled" @change="saveKalmanConfig" />
              </div>

              <!-- 过程噪声 Q -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">响应速度 (过程噪声 Q)</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.processNoise.toFixed(3) }}</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.processNoise" 
                  :min="0" 
                  :step="0.005" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  <span class="text-yellow-400">← 更平滑</span>
                  <span class="float-right text-green-400">响应更快 →</span>
                </div>
              </div>

              <!-- 观测噪声 R -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">平滑程度 (观测噪声 R)</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.measurementNoise.toFixed(2) }}</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.measurementNoise" 
                  :min="0" 
                  :step="0.01" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  <span class="text-red-400">← 更抖动</span>
                  <span class="float-right text-blue-400">更平滑 →</span>
                </div>
              </div>

              <!-- 消失帧数阈值 -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-gray-300">目标消失容忍帧数</span>
                  <span class="text-tech-blue font-mono">{{ kalmanConfig.maxMissingFrames }} 帧</span>
                </div>
                <el-slider 
                  v-model="kalmanConfig.maxMissingFrames" 
                  :min="0" 
                  :step="1" 
                  @change="saveKalmanConfig" 
                />
                <div class="text-xs text-gray-500 mt-2">
                  目标消失多少帧后移除滤波器（值越大，短暂遮挡时框越稳定）
                </div>
              </div>

              <!-- 推荐预设 -->
              <div v-if="kalmanConfig.enabled" class="p-3 bg-slate-900 rounded border border-slate-800">
                <div class="text-gray-300 mb-3">快速预设</div>
                <div class="flex gap-2 flex-wrap">
                  <el-button size="small" @click="applyKalmanPreset('smooth')">
                    🎯 更平滑
                  </el-button>
                  <el-button size="small" @click="applyKalmanPreset('balanced')">
                    ⚖️ 平衡
                  </el-button>
                  <el-button size="small" @click="applyKalmanPreset('responsive')">
                    ⚡ 快响应
                  </el-button>
                </div>
              </div>

              <el-alert
                title="滤波参数调节指南"
                type="info"
                :closable="false"
                show-icon
              >
                <template #default>
                  <div class="text-xs text-gray-300 mt-1">
                    <p>• <strong>物体移动较慢</strong>：增大R值（更平滑），减小Q值</p>
                    <p>• <strong>物体移动较快</strong>：增大Q值（响应更快），减小R值</p>
                    <p>• <strong>检测框抖动严重</strong>：增大R值</p>
                    <p>• <strong>检测框跟不上物体</strong>：增大Q值</p>
                  </div>
                </template>
              </el-alert>
            </div>
          </el-card>
        </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useSystemStore } from '@/store/useSystemStore';
import { Box, VideoCamera, Cpu, Refresh, Lightning, Aim, Coin } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import api from '@/api/index';
import { dbg, dbgErr } from '@/utils/debug';

const store = useSystemStore();

// GPU相关状态
const loadingGpu = ref(false);
const changingDevice = ref(false);
const selectedDevice = ref('auto');
const currentDeviceInfo = ref(null);
const isDefaultDevice = ref(false);
const gpuInfo = reactive({
  devices: [],
  cudaAvailable: false,
  cudaVersion: null,
  gpuCount: 0
});

// 卡尔曼滤波配置
const kalmanConfig = reactive({
  enabled: false,
  processNoise: 0.03,
  measurementNoise: 0.1,
  maxMissingFrames: 5
});

// 保存性能设置
const savePerformanceSettings = async () => {
  dbg('settings.ops', '保存性能设置', `mediapipe=${store.performance?.mediapipeEnabled} half=${store.performance?.halfPrecision}`);
  store.savePerformanceSettings();
  try {
    await api.post('/source/stream/config', {
      frame_limit_enabled: store.performance.frameLimitEnabled,
      target_stream_fps: store.performance.targetStreamFps,
      use_half: store.performance.halfPrecision,
      mediapipe_enabled: store.performance.mediapipeEnabled,
      mediapipe_pose: store.performance.mediapipePose,
      mediapipe_hands: store.performance.mediapipeHands,
      mediapipe_confidence: store.performance.mediapipeConfidence,
      mediapipe_interval: store.performance.mediapipeInterval,
      mediapipe_model_complexity: store.performance.mediapipeModelComplexity,
      mediapipe_track_confidence: store.performance.mediapipeTrackConfidence,
      // v3.8.0 工业 hand-detector
      mediapipe_hand_detector_path: store.performance.mediapipeHandDetectorPath || '',
      mediapipe_hand_detector_kind: store.performance.mediapipeHandDetectorKind || 'v8',
      mediapipe_hand_detector_conf: store.performance.mediapipeHandDetectorConf,
      mediapipe_hand_detector_iou: store.performance.mediapipeHandDetectorIou,
      mediapipe_hand_detector_imgsz: store.performance.mediapipeHandDetectorImgsz,
      mediapipe_hand_roi_pad: store.performance.mediapipeHandRoiPad,
      // v3.32.0 自定义纯色骨架样式
      mediapipe_custom_style: store.performance.mediapipeCustomStyle,
      mediapipe_pose_color: store.performance.mediapipePoseColor,
      mediapipe_pose_point_color: store.performance.mediapipePosePointColor || '',
      mediapipe_pose_thickness: store.performance.mediapipePoseThickness,
      mediapipe_hands_color: store.performance.mediapipeHandsColor,
      mediapipe_hands_point_color: store.performance.mediapipeHandsPointColor || '',
      mediapipe_hands_thickness: store.performance.mediapipeHandsThickness
    });
    ElMessage.success('性能设置已保存');
    // 保存后立即刷新二段状态（让徽章动）
    await refreshMediaPipeStatus();
  } catch (e) {
    dbgErr('settings.ops', '保存性能设置', e);
    console.error('同步性能设置到后端失败:', e);
  }
};

// 加载性能设置
const loadPerformanceSettings = async () => {
  store.loadPerformanceSettings();
  try {
    const res = await api.get('/source/stream/config');
    if (res.data) {
      store.performance.frameLimitEnabled = res.data.frame_limit_enabled;
      store.performance.targetStreamFps = res.data.target_stream_fps;
      if (res.data.use_half !== undefined) {
        store.performance.halfPrecision = res.data.use_half;
      }
      if (res.data.mediapipe_enabled !== undefined) {
        store.performance.mediapipeEnabled = res.data.mediapipe_enabled;
      }
      if (res.data.mediapipe_pose !== undefined) {
        store.performance.mediapipePose = res.data.mediapipe_pose;
      }
      if (res.data.mediapipe_hands !== undefined) {
        store.performance.mediapipeHands = res.data.mediapipe_hands;
      }
      if (res.data.mediapipe_confidence !== undefined) {
        store.performance.mediapipeConfidence = res.data.mediapipe_confidence;
      }
      if (res.data.mediapipe_interval !== undefined) {
        store.performance.mediapipeInterval = res.data.mediapipe_interval;
      }
      if (res.data.mediapipe_model_complexity !== undefined) {
        store.performance.mediapipeModelComplexity = res.data.mediapipe_model_complexity;
      }
      if (res.data.mediapipe_track_confidence !== undefined) {
        store.performance.mediapipeTrackConfidence = res.data.mediapipe_track_confidence;
      }
      if (res.data.mediapipe_hand_detector_path !== undefined) {
        store.performance.mediapipeHandDetectorPath = res.data.mediapipe_hand_detector_path || '';
      }
      if (res.data.mediapipe_hand_detector_kind !== undefined) {
        store.performance.mediapipeHandDetectorKind = res.data.mediapipe_hand_detector_kind || 'v8';
      }
      if (res.data.mediapipe_hand_detector_conf !== undefined) {
        store.performance.mediapipeHandDetectorConf = res.data.mediapipe_hand_detector_conf;
      }
      if (res.data.mediapipe_hand_detector_iou !== undefined) {
        store.performance.mediapipeHandDetectorIou = res.data.mediapipe_hand_detector_iou;
      }
      if (res.data.mediapipe_hand_detector_imgsz !== undefined) {
        store.performance.mediapipeHandDetectorImgsz = res.data.mediapipe_hand_detector_imgsz;
      }
      if (res.data.mediapipe_hand_roi_pad !== undefined) {
        store.performance.mediapipeHandRoiPad = res.data.mediapipe_hand_roi_pad;
      }
      // v3.32.0 自定义纯色骨架样式
      if (res.data.mediapipe_custom_style !== undefined) {
        store.performance.mediapipeCustomStyle = res.data.mediapipe_custom_style;
      }
      if (res.data.mediapipe_pose_color !== undefined) {
        store.performance.mediapipePoseColor = res.data.mediapipe_pose_color || '#00FF00';
      }
      if (res.data.mediapipe_pose_point_color !== undefined) {
        store.performance.mediapipePosePointColor = res.data.mediapipe_pose_point_color || '';
      }
      if (res.data.mediapipe_pose_thickness !== undefined) {
        store.performance.mediapipePoseThickness = res.data.mediapipe_pose_thickness;
      }
      if (res.data.mediapipe_hands_color !== undefined) {
        store.performance.mediapipeHandsColor = res.data.mediapipe_hands_color || '#00FF00';
      }
      if (res.data.mediapipe_hands_point_color !== undefined) {
        store.performance.mediapipeHandsPointColor = res.data.mediapipe_hands_point_color || '';
      }
      if (res.data.mediapipe_hands_thickness !== undefined) {
        store.performance.mediapipeHandsThickness = res.data.mediapipe_hands_thickness;
      }
      // v3.8.0 二段管线状态
      if (res.data.mediapipe_two_stage_status) {
        mediapipeStatusState.value = res.data.mediapipe_two_stage_status.state || 'baseline';
        mediapipeStatusMessage.value = res.data.mediapipe_two_stage_status.message || '';
      }
    }
  } catch (e) {
    console.error('加载后端性能设置失败:', e);
  }
};

// v3.8.0 二段管线状态轮询（只在面板展开时调一次）
const mediapipeStatusState = ref('baseline');     // baseline / active / pending / path_invalid / load_failed
const mediapipeStatusMessage = ref('未启用专用手部模型 (走基础 MediaPipe)');
const mediapipeStatusAlertType = computed(() => {
  const s = mediapipeStatusState.value;
  if (s === 'active') return 'success';
  if (s === 'baseline' || s === 'pending') return 'info';
  return 'error';
});
const refreshMediaPipeStatus = async () => {
  try {
    const res = await api.get('/source/stream/config');
    if (res.data?.mediapipe_two_stage_status) {
      mediapipeStatusState.value = res.data.mediapipe_two_stage_status.state || 'baseline';
      mediapipeStatusMessage.value = res.data.mediapipe_two_stage_status.message || '';
    }
  } catch (e) {
    // 静默
  }
};

// v3.8.0 一键预设：省 CPU 模式 / 高精度模式（友商同款）
const applyMediaPipePreset = (mode) => {
  if (mode === 'eco') {
    store.performance.mediapipeModelComplexity = 0;
    store.performance.mediapipeConfidence = 0.7;
    store.performance.mediapipeTrackConfidence = 0.5;
    ElMessage.success('已切换到「省 CPU 模式」');
  } else if (mode === 'quality') {
    store.performance.mediapipeModelComplexity = 1;
    store.performance.mediapipeConfidence = 0.5;
    store.performance.mediapipeTrackConfidence = 0.5;
    ElMessage.success('已切换到「高精度模式」');
  }
  savePerformanceSettings();
};

// 刷新GPU列表
// retries: 页面自动加载时传入, 吸收后端 CUDA 首次冷初始化导致的偶发失败 (静默重试再提示)
const refreshGpuList = async ({ retries = 0 } = {}) => {
  loadingGpu.value = true;
  try {
    const res = await api.get('/source/gpu/list');
    if (res.data) {
      gpuInfo.devices = res.data.devices || [];
      gpuInfo.cudaAvailable = res.data.cuda_available;
      gpuInfo.cudaVersion = res.data.cuda_version;
      gpuInfo.gpuCount = res.data.gpu_count;
    }
  } catch (e) {
    if (retries > 0) {
      await new Promise((r) => setTimeout(r, 1500));
      return refreshGpuList({ retries: retries - 1 });
    }
    console.error('获取GPU列表失败:', e);
    ElMessage.error('获取GPU列表失败');
  } finally {
    loadingGpu.value = false;
  }
};

// 获取当前设备信息
const loadCurrentDevice = async () => {
  try {
    const res = await api.get('/source/gpu/current');
    if (res.data) {
      selectedDevice.value = res.data.device || 'auto';
      currentDeviceInfo.value = res.data.current_device_info;
      isDefaultDevice.value = true;  // 从后端加载的就是已保存的默认设备
    }
  } catch (e) {
    console.error('获取当前设备信息失败:', e);
  }
};

// 切换推理设备
const changeDevice = async (device) => {
  changingDevice.value = true;
  dbg('settings.ops', '切换推理设备', `device=${device ?? ''}`);
  try {
    const res = await api.post('/source/gpu/set', { device });
    if (res.data) {
      if (res.data.status === 'success') {
        // Step 8 (feat/multi-model-roi-link): 多模型场景透出已重载的 slot 列表
        const reloaded = res.data.reloaded_models;
        if (Array.isArray(reloaded) && reloaded.length >= 2) {
          ElMessage.success(
            `${res.data.message} (重载: ${reloaded.join(', ')})`
          );
        } else {
          ElMessage.success(res.data.message);
        }
        currentDeviceInfo.value = res.data.current_device_info;
        isDefaultDevice.value = true;  // 标记已保存
      } else {
        ElMessage.error(res.data.message);
      }
    }
  } catch (e) {
    dbgErr('settings.ops', '切换推理设备', e);
    console.error('切换设备失败:', e);
    ElMessage.error('切换设备失败');
  } finally {
    changingDevice.value = false;
  }
};

// ========== 卡尔曼滤波配置 ==========
const loadKalmanConfig = async () => {
  try {
    const res = await api.get('/source/kalman/config');
    if (res.data) {
      kalmanConfig.enabled = res.data.enabled;
      kalmanConfig.processNoise = res.data.process_noise;
      kalmanConfig.measurementNoise = res.data.measurement_noise;
      kalmanConfig.maxMissingFrames = res.data.max_missing_frames;
    }
  } catch (e) {
    console.error('获取卡尔曼配置失败:', e);
  }
};

const saveKalmanConfig = async () => {
  dbg('settings.ops', '保存卡尔曼滤波参数', `enabled=${kalmanConfig?.enabled}`);
  try {
    await api.post('/source/kalman/config', {
      enabled: kalmanConfig.enabled,
      process_noise: kalmanConfig.processNoise,
      measurement_noise: kalmanConfig.measurementNoise,
      max_missing_frames: kalmanConfig.maxMissingFrames
    });
    ElMessage.success('滤波参数已更新');
  } catch (e) {
    dbgErr('settings.ops', '保存卡尔曼滤波参数', e);
    console.error('保存卡尔曼配置失败:', e);
    ElMessage.error('保存失败');
  }
};

const applyKalmanPreset = (preset) => {
  switch (preset) {
    case 'smooth':
      // 更平滑：低Q高R
      kalmanConfig.processNoise = 0.01;
      kalmanConfig.measurementNoise = 0.5;
      kalmanConfig.maxMissingFrames = 10;
      break;
    case 'balanced':
      // 平衡
      kalmanConfig.processNoise = 0.03;
      kalmanConfig.measurementNoise = 0.1;
      kalmanConfig.maxMissingFrames = 5;
      break;
    case 'responsive':
      // 快响应：高Q低R
      kalmanConfig.processNoise = 0.1;
      kalmanConfig.measurementNoise = 0.05;
      kalmanConfig.maxMissingFrames = 3;
      break;
  }
  saveKalmanConfig();
};

// ========== WS5(PG): 数据库信息卡片 ==========
const dbInfo = ref(null);

async function loadDbInfo() {
  try {
    const res = await api.get('/system/db-info');
    dbInfo.value = res?.data || null;
  } catch (e) {
    console.warn('加载数据库信息失败:', e?.message);
  }
}

onMounted(() => {
  loadPerformanceSettings();
  refreshGpuList({ retries: 2 });
  loadCurrentDevice();
  loadKalmanConfig();
  loadDbInfo();  // WS5(PG): 数据库信息卡片
});
</script>
