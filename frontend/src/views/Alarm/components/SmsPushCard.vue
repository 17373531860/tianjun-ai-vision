<template>
      <el-card
        data-testid="sms-config-card"
        shadow="never"
        class="bg-slate-800 border-slate-700 lg:col-span-2"
        v-loading="smsLoading"
      >
        <template #header>
          <div class="flex items-center justify-between gap-4">
            <div class="flex items-center gap-2">
              <el-icon class="text-tech-blue"><Message /></el-icon>
              <span class="font-bold text-white">NG 短信/微信推送</span>
              <el-tag size="small" type="info">滚动 12 小时汇总</el-tag>
            </div>
            <el-switch
              v-model="smsConfig.enabled"
              data-testid="sms-enabled-switch"
              active-text="开启"
              inactive-text="关闭"
              @change="onSmsEnabledChange"
            />
          </div>
        </template>

        <div class="space-y-5">
          <el-alert
            data-testid="sms-summary-notice"
            :title="smsSummaryNoticeTitle"
            type="info"
            :closable="false"
            show-icon
          >
            <div class="text-xs leading-5">
              {{ smsSummaryNoticeBody }}
              不含进行中周期，窗口内合格与 NG 均为 0 时不发送。
              不再按单次 NG 或累计 N 次即时推送。
            </div>
          </el-alert>

          <div>
            <div class="text-gray-300 mb-2">汇总调度</div>
            <el-radio-group
              v-model="smsConfig.summary_schedule_mode"
              data-testid="sms-schedule-mode-group"
            >
              <el-radio-button value="rolling_12h" data-testid="sms-schedule-rolling">
                滚动 12 小时
              </el-radio-button>
              <el-radio-button value="daily_shift" data-testid="sms-schedule-shift">
                班次（如早八～晚八）
              </el-radio-button>
            </el-radio-group>
            <div class="mt-3">
              <div class="text-gray-300 mb-2">推送数字口径</div>
              <el-radio-group
                v-model="smsConfig.summary_count_source"
                data-testid="sms-count-source-group"
              >
                <el-radio-button value="panel" data-testid="sms-count-panel">
                  监控面板（当前会话）
                </el-radio-button>
                <el-radio-button value="window" data-testid="sms-count-window">
                  时间窗落库合计
                </el-radio-button>
              </el-radio-group>
              <div class="text-xs text-gray-500 mt-1 leading-5">
                默认推各工位监控面板上的 OK/NG（与金龙等插件面板同源）；可选改为调度时间窗内全部已结算周期合计。
              </div>
            </div>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-3">
              <div>
                <div class="text-gray-300 mb-2">发送形态</div>
                <el-radio-group
                  v-model="smsConfig.summary_send_mode"
                  data-testid="sms-summary-send-mode-group"
                >
                  <el-radio-button
                    value="merged_detail"
                    data-testid="sms-summary-send-merged"
                  >
                    一条分列多工位
                  </el-radio-button>
                  <el-radio-button
                    value="per_channel"
                    data-testid="sms-summary-send-per-channel"
                  >
                    每工位一条（兼容旧版）
                  </el-radio-button>
                </el-radio-group>
                <div class="text-xs text-gray-500 mt-1 leading-5">
                  默认合并发送：同一调度窗口、同一接收方只收到一条，正文包含各工位 OK、NG、合格率、NG率及合计。
                </div>
              </div>
              <div>
                <div class="text-gray-300 mb-2">参与工位</div>
                <el-select
                  v-model="smsConfig.summary_channel_ids"
                  data-testid="sms-summary-channel-select"
                  multiple
                  clearable
                  collapse-tags
                  collapse-tags-tooltip
                  placeholder="全部启用工位"
                  class="w-full"
                >
                  <el-option
                    v-for="channel in channelCount"
                    :key="channel - 1"
                    :label="`工位 ${channel}`"
                    :value="channel - 1"
                    :data-testid="`sms-summary-channel-option-${channel - 1}`"
                  />
                </el-select>
                <div class="text-xs text-gray-500 mt-1 leading-5">
                  留空表示全部启用工位；选中但本窗口无数据的工位仍以 0 展示。
                </div>
              </div>
            </div>
            <el-alert
              v-if="smsChannelTemplateWarning"
              data-testid="sms-summary-channel-warning"
              class="mt-3"
              type="warning"
              :closable="false"
              show-icon
            >
              <div class="text-xs leading-5">{{ smsChannelTemplateWarning }}</div>
            </el-alert>
            <div
              v-if="smsConfig.summary_schedule_mode === 'daily_shift'"
              data-testid="sms-shift-fields"
              class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-3"
            >
              <div>
                <div class="text-gray-300 mb-2">开始小时</div>
                <el-input-number
                  v-model="smsConfig.shift_start_hour"
                  data-testid="sms-shift-start-hour"
                  :min="0"
                  :max="23"
                  :step="1"
                  :precision="0"
                  class="w-full"
                />
              </div>
              <div>
                <div class="text-gray-300 mb-2">结束小时（到点发送）</div>
                <el-input-number
                  v-model="smsConfig.shift_end_hour"
                  data-testid="sms-shift-end-hour"
                  :min="0"
                  :max="23"
                  :step="1"
                  :precision="0"
                  class="w-full"
                />
              </div>
              <div class="flex items-center justify-between rounded border border-slate-700 px-4 py-3">
                <div>
                  <div class="text-gray-300">发送夜班窗</div>
                  <div class="text-xs text-gray-500">默认关：只发白天一条。</div>
                </div>
                <el-switch v-model="smsConfig.send_night_window" data-testid="sms-send-night-window" />
              </div>
            </div>
          </div>

          <el-alert type="warning" :closable="false" show-icon>
            <div class="text-xs leading-5">
              USB/AT、通用 HTTP、微信推送(WxPusher)、阿里云、腾讯云 五选一。
              该通道为系统级短信通道，NG 汇总通知与短信日报共用；
              云通道需工控机可上网；AT 通道必须使用独立 COM，勿选灯塔/蜂鸣器串口。
            </div>
          </el-alert>

          <div>
            <div class="text-gray-300 mb-2">通道类型（只能选择一个）</div>
            <el-radio-group
              v-model="smsConfig.provider"
              data-testid="sms-provider-group"
              @change="onSmsProviderChange"
            >
              <el-radio-button value="at_modem" data-testid="sms-provider-at">
                USB/AT 短信模块
              </el-radio-button>
              <el-radio-button value="generic_http" data-testid="sms-provider-http">
                云服务器短信
              </el-radio-button>
              <el-radio-button value="wxpusher" data-testid="sms-provider-wxpusher">
                微信推送(WxPusher)
              </el-radio-button>
              <el-radio-button value="aliyun" data-testid="sms-provider-aliyun">
                阿里云短信
              </el-radio-button>
              <el-radio-button value="tencent" data-testid="sms-provider-tencent">
                腾讯云短信
              </el-radio-button>
            </el-radio-group>
          </div>

          <div
            v-if="smsConfig.provider === 'at_modem'"
            data-testid="sms-at-fields"
            class="grid grid-cols-1 lg:grid-cols-2 gap-4"
          >
            <div>
              <div class="text-gray-300 mb-2">短信模块 COM</div>
              <div class="flex gap-2">
                <el-select
                  v-model="smsConfig.at_modem.port"
                  data-testid="sms-port-select"
                  placeholder="选择短信模块串口"
                  class="flex-1"
                  filterable
                >
                  <el-option
                    v-for="port in smsPortOptions"
                    :key="port.port"
                    :label="`${port.port} - ${port.description}`"
                    :value="port.port"
                  />
                </el-select>
                <el-button
                  data-testid="sms-refresh-ports"
                  :icon="Refresh"
                  :loading="smsLoadingPorts"
                  @click="refreshSmsPorts(true)"
                >刷新端口</el-button>
              </div>
              <div class="text-xs text-amber-400 mt-1">必须是短信模块独立 COM，勿选灯塔串口。</div>
            </div>

            <div>
              <div class="text-gray-300 mb-2">波特率</div>
              <el-select v-model="smsConfig.at_modem.baudrate" data-testid="sms-baudrate-select" class="w-full">
                <el-option :value="9600" label="9600" />
                <el-option :value="19200" label="19200" />
                <el-option :value="38400" label="38400" />
                <el-option :value="57600" label="57600" />
                <el-option :value="115200" label="115200（默认）" />
              </el-select>
            </div>

            <div>
              <div class="text-gray-300 mb-2">短信编码</div>
              <el-select v-model="smsConfig.at_modem.encoding" data-testid="sms-encoding-select" class="w-full">
                <el-option value="auto" label="自动（推荐）" />
                <el-option value="gsm" label="GSM（仅 ASCII）" />
                <el-option value="ucs2" label="UCS2（中文）" />
              </el-select>
            </div>

            <div>
              <div class="text-gray-300 mb-2">短信模板</div>
              <el-input
                v-model="smsConfig.at_modem.template"
                data-testid="sms-template"
                type="textarea"
                :rows="3"
                placeholder="请输入短信模板"
              />
              <div class="text-xs text-gray-500 mt-1">
                汇总字段：{device_name}、{time_range}、{ok_count}、{ng_count}
              </div>
            </div>
          </div>

          <div
            v-else-if="smsConfig.provider === 'generic_http'"
            data-testid="sms-http-fields"
            class="grid grid-cols-1 lg:grid-cols-2 gap-4"
          >
            <div class="lg:col-span-2">
              <div class="text-gray-300 mb-2">API URL</div>
              <el-input
                v-model="smsConfig.generic_http.api_url"
                data-testid="sms-http-api-url"
                placeholder="https://sms.example.com/v1/send"
              />
              <div class="text-xs text-amber-400 mt-1">云短信依赖外网，请先确认工控机可访问厂商 API。</div>
            </div>

            <div>
              <div class="text-gray-300 mb-2">请求方法</div>
              <el-select v-model="smsConfig.generic_http.request_method" data-testid="sms-http-method" class="w-full">
                <el-option value="POST" label="POST" />
                <el-option value="PUT" label="PUT" />
              </el-select>
            </div>

            <div>
              <div class="text-gray-300 mb-2">请求超时</div>
              <div class="flex items-center gap-2">
                <el-input-number
                  v-model="smsConfig.generic_http.timeout_seconds"
                  data-testid="sms-http-timeout"
                  :min="0.5"
                  :max="120"
                  :step="0.5"
                  class="w-full"
                />
                <span class="text-sm text-gray-400">秒</span>
              </div>
            </div>

            <div>
              <div class="text-gray-300 mb-2">Token</div>
              <el-input
                v-model="smsConfig.generic_http.token"
                data-testid="sms-http-token"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="Token 鉴权时填写"
              />
            </div>

            <div>
              <div class="text-gray-300 mb-2">Access Key</div>
              <el-input
                v-model="smsConfig.generic_http.access_key"
                data-testid="sms-http-access-key"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="AK/SK 鉴权时填写"
              />
            </div>

            <div>
              <div class="text-gray-300 mb-2">Access Secret</div>
              <el-input
                v-model="smsConfig.generic_http.access_secret"
                data-testid="sms-http-access-secret"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="必须与 Access Key 成对填写"
              />
            </div>

            <div>
              <div class="text-gray-300 mb-2">短信签名</div>
              <el-input v-model="smsConfig.generic_http.sign_name" data-testid="sms-http-sign-name" placeholder="厂商侧签名名称（可选）" />
            </div>

            <div>
              <div class="text-gray-300 mb-2">模板 ID</div>
              <el-input v-model="smsConfig.generic_http.template_id" data-testid="sms-http-template-id" placeholder="厂商侧模板 ID（可选）" />
              <div data-testid="sms-cloud-template-hint" class="text-xs text-amber-400 mt-1">
                建议模板变量：device_name / time_range / ok_count / ng_count。阿里云需申请新的汇总模板 CODE，旧即时 NG 模板不适用。
              </div>
            </div>

            <div class="flex items-center justify-between rounded border border-slate-700 px-4 py-3">
              <div>
                <div class="text-gray-300">校验 HTTPS 证书</div>
                <div class="text-xs text-gray-500">生产环境建议始终开启。</div>
              </div>
              <el-switch v-model="smsConfig.generic_http.verify_ssl" data-testid="sms-http-verify-ssl" />
            </div>

            <div class="lg:col-span-2">
              <div class="text-gray-300 mb-2">厂商字段映射（JSON）</div>
              <el-input
                v-model="smsFieldMappingText"
                data-testid="sms-http-field-mapping"
                type="textarea"
                :rows="4"
                placeholder='例如：{"phone_numbers":"mobiles","message":"content"}'
              />
              <div class="text-xs text-gray-500 mt-1">仅在厂商 JSON 字段名与默认值不同时调整。</div>
            </div>
          </div>

          <div
            v-else-if="smsConfig.provider === 'wxpusher'"
            data-testid="sms-wx-fields"
            class="grid grid-cols-1 lg:grid-cols-2 gap-4"
          >
            <div class="lg:col-span-2">
              <div class="text-gray-300 mb-2">appToken</div>
              <el-input
                v-model="smsConfig.wxpusher.app_token"
                data-testid="sms-wx-app-token"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="在 WxPusher 管理后台创建应用后获取"
              />
              <div data-testid="sms-wx-hint" class="text-xs text-amber-400 mt-1">
                需工控机可访问 WxPusher；微信扫码关注应用后获得 UID。官方文档：wxpusher.zjiecode.com
              </div>
            </div>

            <div>
              <div class="text-gray-300 mb-2">UID 列表</div>
              <el-input
                v-model="smsWxUidsText"
                data-testid="sms-wx-uids"
                type="textarea"
                :rows="3"
                placeholder="UID_xxxx，多个用逗号、分号或换行分隔"
              />
            </div>

            <div>
              <div class="text-gray-300 mb-2">TopicId 列表（可选）</div>
              <el-input
                v-model="smsWxTopicIdsText"
                data-testid="sms-wx-topic-ids"
                type="textarea"
                :rows="3"
                placeholder="主题群发用，例如：101,102"
              />
              <div class="text-xs text-gray-500 mt-1">UID 与 TopicId 至少填写一类。</div>
            </div>

            <div>
              <div class="text-gray-300 mb-2">内容类型</div>
              <el-select v-model="smsConfig.wxpusher.content_type" data-testid="sms-wx-content-type" class="w-full">
                <el-option :value="1" label="1 - 文本" />
                <el-option :value="2" label="2 - HTML" />
                <el-option :value="3" label="3 - Markdown" />
              </el-select>
            </div>

            <div>
              <div class="text-gray-300 mb-2">请求超时</div>
              <div class="flex items-center gap-2">
                <el-input-number
                  v-model="smsConfig.wxpusher.timeout_seconds"
                  data-testid="sms-wx-timeout"
                  :min="0.5"
                  :max="120"
                  :step="0.5"
                  class="w-full"
                />
                <span class="text-sm text-gray-400">秒</span>
              </div>
            </div>

            <div class="lg:col-span-2">
              <div class="text-gray-300 mb-2">消息摘要模板</div>
              <el-input
                v-model="smsConfig.wxpusher.summary_template"
                data-testid="sms-wx-summary-template"
                type="textarea"
                :rows="2"
                placeholder="微信会话列表显示的短摘要"
              />
              <div class="text-xs text-gray-500 mt-1">
                可用字段：{device_name}、{time_range}、{ok_count}、{ng_count}；最长约 100 字。
              </div>
            </div>

            <div class="lg:col-span-2">
              <div class="text-gray-300 mb-2">API URL</div>
              <el-input
                v-model="smsConfig.wxpusher.api_url"
                data-testid="sms-wx-api-url"
                placeholder="https://wxpusher.zjiecode.com/api/send/message"
              />
            </div>

            <div class="flex items-center justify-between rounded border border-slate-700 px-4 py-3">
              <div>
                <div class="text-gray-300">校验 HTTPS 证书</div>
                <div class="text-xs text-gray-500">生产环境建议始终开启。</div>
              </div>
              <el-switch v-model="smsConfig.wxpusher.verify_ssl" data-testid="sms-wx-verify-ssl" />
            </div>
          </div>

          <div
            v-else-if="smsConfig.provider === 'aliyun'"
            data-testid="sms-aliyun-fields"
            class="grid grid-cols-1 lg:grid-cols-2 gap-4"
          >
            <div>
              <div class="text-gray-300 mb-2">AccessKey ID</div>
              <el-input
                v-model="smsConfig.aliyun.access_key_id"
                data-testid="sms-aliyun-ak"
                placeholder="阿里云 RAM AccessKeyId"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">AccessKey Secret</div>
              <el-input
                v-model="smsConfig.aliyun.access_key_secret"
                data-testid="sms-aliyun-sk"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="阿里云 RAM AccessKeySecret"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">短信签名</div>
              <el-input
                v-model="smsConfig.aliyun.sign_name"
                data-testid="sms-aliyun-sign"
                placeholder="审核通过的签名，如：天军视觉"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">默认模板 Code</div>
              <el-input
                v-model="smsConfig.aliyun.template_code"
                data-testid="sms-aliyun-template"
                placeholder="如：SMS_123456789"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">Region</div>
              <el-input
                v-model="smsConfig.aliyun.region"
                data-testid="sms-aliyun-region"
                placeholder="cn-hangzhou"
              />
            </div>
            <div class="lg:col-span-2 text-xs text-amber-400" data-testid="sms-aliyun-hint">
              阿里云需新建并审核「多工位汇总」模板，旧「单工位即时 NG」模板不可复用。
              两工位完整变量：${time_range}、${device_name}、
              ${ch1_ok}、${ch1_ng}、${ch1_total}、${ch1_ok_rate}、${ch1_ng_rate}、
              ${ch2_ok}、${ch2_ng}、${ch2_total}、${ch2_ok_rate}、${ch2_ng_rate}、
              ${total_ok}、${total_ng}、${total}、${ok_rate}、${ng_rate}。
              超过两工位继续使用 ${chN_ok}/${chN_ng}/${chN_total}/${chN_ok_rate}/${chN_ng_rate}，
              申请模板时同时核对阿里云变量及正文长度限制。短信日报仍使用日报规则自己的变量映射与模板 Code。
            </div>
          </div>

          <div
            v-else-if="smsConfig.provider === 'tencent'"
            data-testid="sms-tencent-fields"
            class="grid grid-cols-1 lg:grid-cols-2 gap-4"
          >
            <div>
              <div class="text-gray-300 mb-2">SecretId</div>
              <el-input
                v-model="smsConfig.tencent.secret_id"
                data-testid="sms-tencent-id"
                placeholder="腾讯云 API SecretId"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">SecretKey</div>
              <el-input
                v-model="smsConfig.tencent.secret_key"
                data-testid="sms-tencent-key"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="腾讯云 API SecretKey"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">短信应用 SdkAppId</div>
              <el-input
                v-model="smsConfig.tencent.sdk_app_id"
                data-testid="sms-tencent-appid"
                placeholder="如：1400000000"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">短信签名</div>
              <el-input
                v-model="smsConfig.tencent.sign_name"
                data-testid="sms-tencent-sign"
                placeholder="审核通过的签名"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">默认模板 ID</div>
              <el-input
                v-model="smsConfig.tencent.template_id"
                data-testid="sms-tencent-template"
                placeholder="纯数字模板 ID"
              />
            </div>
            <div>
              <div class="text-gray-300 mb-2">Region</div>
              <el-input
                v-model="smsConfig.tencent.region"
                data-testid="sms-tencent-region"
                placeholder="ap-guangzhou"
              />
            </div>
            <div class="lg:col-span-2 text-xs text-amber-400" data-testid="sms-tencent-hint">
              腾讯云模板为位置变量 {1}{2}…：NG 汇总按 time_range、ok_count、ng_count 顺序对位；
              短信日报按规则「变量映射」的勾选顺序对位。
            </div>
          </div>

          <div v-if="smsConfig.provider !== 'wxpusher'">
            <div class="text-gray-300 mb-2">接收手机号（共用）</div>
            <el-input
              v-model="smsRecipientsText"
              data-testid="sms-recipients"
              type="textarea"
              :rows="3"
              placeholder="可填写多个号码，用逗号、分号或换行分隔"
            />
            <div class="text-xs text-gray-500 mt-1">号码仅用于发送配置；运行日志会自动脱敏。</div>
          </div>

          <el-collapse v-model="smsAdvancedSections" data-testid="sms-advanced-collapse">
            <el-collapse-item title="高级设置" name="advanced">
              <div
                data-testid="sms-legacy-immediate-note"
                class="mb-3 text-xs text-gray-500 leading-5"
              >
                汇总窗口由持久化水位去重；旧版即时 NG 阈值与冷却字段仅保留配置兼容，当前不参与发送。
              </div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-2">
                <div>
                  <div class="text-gray-300 mb-2">失败重试次数</div>
                  <el-input-number
                    v-model="smsConfig.retry_count"
                    data-testid="sms-retries"
                    :min="0"
                    :max="5"
                    :step="1"
                    :precision="0"
                    class="w-full"
                  />
                </div>
                <div>
                  <div class="text-gray-300 mb-2">重试退避秒数</div>
                  <el-input
                    v-model="smsRetryBackoffText"
                    data-testid="sms-retry-backoff"
                    placeholder="例如：1, 3, 5"
                  />
                </div>
                <div>
                  <div class="text-gray-300 mb-2">内存队列上限</div>
                  <el-input-number v-model="smsConfig.queue_size" data-testid="sms-queue-size" :min="1" :max="1000" :step="1" :precision="0" class="w-full" />
                </div>
                <div>
                  <div class="text-gray-300 mb-2">断网队列上限</div>
                  <el-input-number v-model="smsConfig.offline_queue_max" data-testid="sms-offline-max" :min="1" :max="10000" :step="1" :precision="0" class="w-full" />
                </div>
                <div>
                  <div class="text-gray-300 mb-2">断网消息有效期</div>
                  <div class="flex items-center gap-2">
                    <el-input-number v-model="smsConfig.offline_ttl_seconds" data-testid="sms-offline-ttl" :min="60" :max="2592000" :step="60" :precision="0" class="w-full" />
                    <span class="text-sm text-gray-400">秒</span>
                  </div>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>

          <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4 pt-2 border-t border-slate-700">
            <div data-testid="sms-test-snapshot-hint" class="text-xs text-gray-500 leading-5">
              “测试发送”使用当前未闭合窗口快照，不等于完整 12 小时窗口；后台 queued 回执不代表手机最终送达。
              总开关关闭时不发送汇总，现有检测与灯塔行为保持不变。
            </div>
            <div class="flex gap-2">
              <el-button
                data-testid="sms-test-send"
                :loading="smsTesting"
                :disabled="smsSaving"
                @click="sendSmsTest"
              >测试发送</el-button>
              <el-button
                data-testid="sms-save"
                type="primary"
                :loading="smsSaving"
                :disabled="smsTesting"
                @click="saveSmsConfig"
              >保存短信配置</el-button>
            </div>
          </div>
        </div>
      </el-card>
</template>

<script setup>
import { computed, ref, reactive, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { Refresh, Message } from '@element-plus/icons-vue';
import { getSmsConfig, getSmsPorts, testSms, updateSmsConfig } from '@/api/sms';

const props = defineProps({
  channelCount: {
    type: Number,
    required: true,
  },
});

// 短信是系统级独立通知通道，不复用灯塔 config/portList/selectedPort。
const createSmsDefaultConfig = () => ({
  enabled: false,
  provider: 'at_modem',
  at_modem: {
    port: '',
    baudrate: 115200,
    encoding: 'auto',
    template: '【天军AI视觉】{device_name} {time_range} OK{ok_count} NG{ng_count}',
  },
  generic_http: {
    api_url: '',
    request_method: 'POST',
    timeout_seconds: 10,
    token: '',
    access_key: '',
    access_secret: '',
    sign_name: '',
    template_id: '',
    verify_ssl: true,
    field_mapping: {},
  },
  wxpusher: {
    app_token: '',
    uids: [],
    topic_ids: [],
    content_type: 1,
    summary_template: '【天军AI视觉】{time_range} OK={ok_count} NG={ng_count}',
    api_url: 'https://wxpusher.zjiecode.com/api/send/message',
    timeout_seconds: 10,
    verify_ssl: true,
  },
  aliyun: {
    access_key_id: '',
    access_key_secret: '',
    sign_name: '',
    template_code: '',
    region: 'cn-hangzhou',
  },
  tencent: {
    secret_id: '',
    secret_key: '',
    sdk_app_id: '',
    sign_name: '',
    template_id: '',
    region: 'ap-guangzhou',
  },
  phone_numbers: [],
  retry_count: 3,
  retry_backoff_seconds: [1, 3, 5],
  ng_threshold: 5,
  cooldown_seconds: 60,
  queue_size: 100,
  offline_queue_max: 200,
  offline_ttl_seconds: 86400,
  summary_schedule_mode: 'rolling_12h',
  shift_start_hour: 8,
  shift_end_hour: 20,
  send_night_window: false,
  summary_count_source: 'panel',
  summary_send_mode: 'merged_detail',
  summary_channel_ids: [],
});
const smsConfig = reactive(createSmsDefaultConfig());
const smsRecipientsText = ref('');
const smsWxUidsText = ref('');
const smsWxTopicIdsText = ref('');
const smsRetryBackoffText = ref('1, 3, 5');
const smsFieldMappingText = ref('{}');
const smsPorts = ref([]);
const smsLoading = ref(false);
const smsLoadingPorts = ref(false);
const smsSaving = ref(false);
const smsTesting = ref(false);
const smsAdvancedSections = ref([]);
const smsSummaryNoticeTitle = computed(() => (
  smsConfig.summary_schedule_mode === 'daily_shift'
    ? `班次汇总：每天 ${smsConfig.shift_start_hour}:00～${smsConfig.shift_end_hour}:00`
    : '仅发送滚动 12 小时生产汇总'
));
const smsSummaryNoticeBody = computed(() => {
  const sendShape = smsConfig.summary_send_mode === 'per_channel'
    ? '每工位一条'
    : '一条内分列多工位';
  if (smsConfig.summary_schedule_mode === 'daily_shift') {
    return `按配置班次到点以${sendShape}发送；数字口径：${smsConfig.summary_count_source === 'window' ? '时间窗落库合计' : '监控面板当前会话'}；默认到 ${smsConfig.shift_end_hour}:00 发送白天窗${smsConfig.send_night_window ? '，并额外发送夜班窗' : '（夜班窗默认不发）'}。`;
  }
  return smsConfig.summary_count_source === 'window'
    ? `每次软件（后端）启动后以本次服务启动时间为起点重新开窗，每滚动 12 小时以${sendShape}汇总时间窗内已结算周期并发送；`
    : `每次软件（后端）启动后以本次服务启动时间为起点重新开窗，每滚动 12 小时以${sendShape}推送监控面板当前会话 OK/NG；`;
});
const smsChannelTemplateWarning = computed(() => {
  const selectedCount = Array.isArray(smsConfig.summary_channel_ids)
    ? smsConfig.summary_channel_ids.length
    : 0;
  const participatingCount = selectedCount || props.channelCount;
  if (smsConfig.summary_send_mode !== 'merged_detail' || participatingCount <= 2) {
    return '';
  }
  return `当前将合并 ${participatingCount} 个工位。阿里云模板需继续申请 chN_* 命名变量，并确认模板变量数量与短信正文长度。`;
});
const smsPortOptions = computed(() => {
  const options = Array.isArray(smsPorts.value) ? [...smsPorts.value] : [];
  const current = smsConfig.at_modem.port?.trim();
  if (current && !options.some((item) => item.port === current)) {
    options.unshift({ port: current, description: '当前保存配置（系统暂未枚举）', hwid: '' });
  }
  return options;
});

const formatApiError = (err, fallback) => {
  const responseData = err?.response?.data;
  if (typeof responseData?.message === 'string' && responseData.message) {
    return responseData.error_code
      ? `${responseData.message}（${responseData.error_code}）`
      : responseData.message;
  }
  const detail = responseData?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || JSON.stringify(item)).join('；');
  }
  if (detail) return JSON.stringify(detail);
  return fallback;
};

const applySmsConfig = (data = {}) => {
  const defaults = createSmsDefaultConfig();
  const atModem = {
    ...defaults.at_modem,
    ...(data.at_modem || {}),
  };
  if (!data.at_modem) {
    atModem.port = data.port ?? atModem.port;
    atModem.baudrate = data.baudrate ?? atModem.baudrate;
    atModem.encoding = data.encoding ?? atModem.encoding;
    atModem.template = data.template ?? atModem.template;
  }
  const genericHttp = {
    ...defaults.generic_http,
    ...(data.generic_http || {}),
    field_mapping: {
      ...defaults.generic_http.field_mapping,
      ...(data.generic_http?.field_mapping || {}),
    },
  };
  const wxpusher = {
    ...defaults.wxpusher,
    ...(data.wxpusher || {}),
    uids: Array.isArray(data.wxpusher?.uids) ? [...data.wxpusher.uids] : [...defaults.wxpusher.uids],
    topic_ids: Array.isArray(data.wxpusher?.topic_ids)
      ? [...data.wxpusher.topic_ids]
      : [...defaults.wxpusher.topic_ids],
  };
  const aliyun = { ...defaults.aliyun, ...(data.aliyun || {}) };
  const tencent = { ...defaults.tencent, ...(data.tencent || {}) };
  const phoneNumbers = Array.isArray(data.phone_numbers)
    ? data.phone_numbers
    : (Array.isArray(data.recipients) ? data.recipients : []);
  const retryBackoff = Array.isArray(data.retry_backoff_seconds)
    ? data.retry_backoff_seconds
    : defaults.retry_backoff_seconds;
  Object.assign(smsConfig, defaults, {
    enabled: data.enabled ?? defaults.enabled,
    provider: data.provider || defaults.provider,
    at_modem: atModem,
    generic_http: genericHttp,
    wxpusher,
    aliyun,
    tencent,
    phone_numbers: [...phoneNumbers],
    retry_count: data.retry_count ?? data.retries ?? defaults.retry_count,
    retry_backoff_seconds: [...retryBackoff],
    ng_threshold: data.ng_threshold ?? defaults.ng_threshold,
    cooldown_seconds: data.cooldown_seconds ?? defaults.cooldown_seconds,
    queue_size: data.queue_size ?? defaults.queue_size,
    offline_queue_max: data.offline_queue_max ?? defaults.offline_queue_max,
    offline_ttl_seconds: data.offline_ttl_seconds ?? defaults.offline_ttl_seconds,
    summary_schedule_mode: data.summary_schedule_mode === 'daily_shift'
      ? 'daily_shift'
      : 'rolling_12h',
    shift_start_hour: data.shift_start_hour ?? defaults.shift_start_hour,
    shift_end_hour: data.shift_end_hour ?? defaults.shift_end_hour,
    send_night_window: data.send_night_window ?? defaults.send_night_window,
    summary_count_source: data.summary_count_source === 'window' ? 'window' : 'panel',
    summary_send_mode: data.summary_send_mode === 'per_channel'
      ? 'per_channel'
      : 'merged_detail',
    summary_channel_ids: Array.isArray(data.summary_channel_ids)
      ? [...new Set(data.summary_channel_ids.map(Number))]
        .filter((channelId) => Number.isInteger(channelId) && channelId >= 0)
        .sort((left, right) => left - right)
      : [],
  });
  smsRecipientsText.value = phoneNumbers.join('\n');
  smsWxUidsText.value = wxpusher.uids.join('\n');
  smsWxTopicIdsText.value = wxpusher.topic_ids.join(', ');
  smsRetryBackoffText.value = retryBackoff.join(', ');
  smsFieldMappingText.value = JSON.stringify(genericHttp.field_mapping, null, 2);
};

const parseSmsRecipients = () => smsRecipientsText.value
  .split(/[,，;；\r\n]+/)
  .map((item) => item.trim())
  .filter(Boolean);

const parseSmsWxUids = () => smsWxUidsText.value
  .split(/[,，;；\r\n]+/)
  .map((item) => item.trim())
  .filter(Boolean);

const parseSmsWxTopicIds = () => {
  const raw = smsWxTopicIdsText.value
    .split(/[,，;；\r\n\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (raw.length === 0) return [];
  const values = raw.map((item) => Number(item));
  if (values.some((item) => !Number.isInteger(item) || item <= 0)) {
    throw new Error('TopicId 必须是正整数，多个用逗号分隔');
  }
  return values;
};

const parseSmsRetryBackoff = () => {
  const values = smsRetryBackoffText.value
    .split(/[,，;；\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number);
  if (values.length < 1 || values.length > 10 || values.some((item) => !Number.isFinite(item) || item < 0 || item > 300)) {
    throw new Error('重试退避秒数需填写 1~10 个 0~300 的数字');
  }
  return values;
};

const parseSmsFieldMapping = () => {
  const raw = smsFieldMappingText.value.trim();
  if (!raw) return {};
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('厂商字段映射必须是 JSON 对象');
  }
  if (Object.values(parsed).some((item) => typeof item !== 'string')) {
    throw new Error('厂商字段映射的值必须是字符串');
  }
  return parsed;
};

const loadSmsConfig = async () => {
  smsLoading.value = true;
  try {
    const res = await getSmsConfig();
    applySmsConfig(res.data);
  } catch (err) {
    ElMessage.error(formatApiError(err, '读取短信配置失败'));
  } finally {
    smsLoading.value = false;
  }
};

const refreshSmsPorts = async (showMessage = false) => {
  smsLoadingPorts.value = true;
  try {
    const res = await getSmsPorts();
    smsPorts.value = Array.isArray(res.data?.ports) ? res.data.ports : [];
    if (showMessage) {
      if (smsPorts.value.length > 0) {
        ElMessage.success(`检测到 ${smsPorts.value.length} 个短信模块候选串口`);
      } else {
        ElMessage.warning('未检测到短信模块串口，请检查 USB 4G 模块和驱动');
      }
    }
  } catch (err) {
    ElMessage.error(formatApiError(err, '获取短信串口失败'));
  } finally {
    smsLoadingPorts.value = false;
  }
};

const onSmsEnabledChange = (enabled) => {
  if (!enabled) return;
  if (smsConfig.provider === 'at_modem' && !smsConfig.at_modem.port?.trim()) {
    ElMessage.warning('AT 通道开启前请先选择短信模块独立 COM');
  } else if (smsConfig.provider === 'generic_http' && !smsConfig.generic_http.api_url?.trim()) {
    ElMessage.warning('云短信开启前请先填写 API URL');
  } else if (smsConfig.provider === 'wxpusher') {
    if (!smsConfig.wxpusher.app_token?.trim()) {
      ElMessage.warning('微信推送开启前请先填写 appToken');
    } else if (parseSmsWxUids().length === 0 && !smsWxTopicIdsText.value.trim()) {
      ElMessage.warning('微信推送开启前请至少填写 UID 或 TopicId');
    }
  } else if (smsConfig.provider === 'aliyun'
    && !(smsConfig.aliyun.access_key_id?.trim() && smsConfig.aliyun.access_key_secret?.trim())) {
    ElMessage.warning('阿里云短信开启前请先填写 AccessKey');
  } else if (smsConfig.provider === 'tencent'
    && !(smsConfig.tencent.secret_id?.trim() && smsConfig.tencent.secret_key?.trim())) {
    ElMessage.warning('腾讯云短信开启前请先填写 SecretId/SecretKey');
  } else if (parseSmsRecipients().length === 0) {
    ElMessage.warning('开启前请至少填写一个接收手机号');
  }
};

const onSmsProviderChange = (provider) => {
  if (provider === 'at_modem' && smsPorts.value.length === 0) {
    refreshSmsPorts(false);
  }
};

const validateSmsForm = () => {
  const httpConfig = smsConfig.generic_http;
  const wxConfig = smsConfig.wxpusher;
  if (!smsConfig.at_modem.template?.trim()) {
    ElMessage.warning('短信模板不能为空');
    return false;
  }
  if (Boolean(httpConfig.access_key?.trim()) !== Boolean(httpConfig.access_secret?.trim())) {
    ElMessage.warning('Access Key 与 Access Secret 必须成对填写');
    return false;
  }
  if (
    smsConfig.enabled
    && smsConfig.provider !== 'wxpusher'
    && parseSmsRecipients().length === 0
  ) {
    ElMessage.warning('开启 12 小时汇总短信前必须填写接收手机号');
    return false;
  }
  if (smsConfig.enabled && smsConfig.provider === 'at_modem' && !smsConfig.at_modem.port?.trim()) {
    ElMessage.warning('开启 AT 短信推送前必须选择短信模块独立 COM');
    return false;
  }
  if (smsConfig.enabled && smsConfig.provider === 'generic_http') {
    if (!httpConfig.api_url?.trim()) {
      ElMessage.warning('开启云短信前必须填写 API URL');
      return false;
    }
    if (!(httpConfig.token?.trim() || (httpConfig.access_key?.trim() && httpConfig.access_secret?.trim()))) {
      ElMessage.warning('开启云短信前必须填写 Token，或成对填写 Access Key/Secret');
      return false;
    }
  }
  if (smsConfig.enabled && smsConfig.provider === 'aliyun') {
    const aliyun = smsConfig.aliyun;
    if (!(aliyun.access_key_id?.trim() && aliyun.access_key_secret?.trim())) {
      ElMessage.warning('开启阿里云短信前必须填写 AccessKey ID/Secret');
      return false;
    }
    if (!aliyun.sign_name?.trim()) {
      ElMessage.warning('开启阿里云短信前必须填写审核过的签名');
      return false;
    }
    if (!aliyun.template_code?.trim()) {
      ElMessage.warning('开启阿里云短信前必须填写审核过的模板 Code');
      return false;
    }
  }
  if (smsConfig.enabled && smsConfig.provider === 'tencent') {
    const tencent = smsConfig.tencent;
    if (!(tencent.secret_id?.trim() && tencent.secret_key?.trim())) {
      ElMessage.warning('开启腾讯云短信前必须填写 SecretId/SecretKey');
      return false;
    }
    if (!tencent.sdk_app_id?.trim()) {
      ElMessage.warning('开启腾讯云短信前必须填写短信应用 SdkAppId');
      return false;
    }
    if (!tencent.sign_name?.trim()) {
      ElMessage.warning('开启腾讯云短信前必须填写审核过的签名');
      return false;
    }
    if (!tencent.template_id?.trim()) {
      ElMessage.warning('开启腾讯云短信前必须填写审核过的模板 ID');
      return false;
    }
  }
  if (smsConfig.summary_schedule_mode === 'daily_shift') {
    const startHour = Number(smsConfig.shift_start_hour);
    const endHour = Number(smsConfig.shift_end_hour);
    if (!Number.isInteger(startHour) || startHour < 0 || startHour > 23
      || !Number.isInteger(endHour) || endHour < 0 || endHour > 23) {
      ElMessage.warning('班次小时必须是 0~23 的整数');
      return false;
    }
    if (startHour === endHour) {
      ElMessage.warning('班次开始与结束小时不能相同');
      return false;
    }
    if (!smsConfig.send_night_window && startHour >= endHour) {
      ElMessage.warning('仅白天班次时，开始小时必须早于结束小时（如 8 到 20）');
      return false;
    }
  }
  if (smsConfig.enabled && smsConfig.provider === 'wxpusher') {
    if (!wxConfig.app_token?.trim()) {
      ElMessage.warning('开启微信推送前必须填写 appToken');
      return false;
    }
    try {
      const uids = parseSmsWxUids();
      const topicIds = parseSmsWxTopicIds();
      if (uids.length === 0 && topicIds.length === 0) {
        ElMessage.warning('开启微信推送前必须填写 UID 或 TopicId');
        return false;
      }
    } catch (err) {
      ElMessage.warning(err.message || 'WxPusher 目标配置格式错误');
      return false;
    }
    if (!wxConfig.summary_template?.trim()) {
      ElMessage.warning('微信推送摘要模板不能为空');
      return false;
    }
    if (!wxConfig.api_url?.trim()) {
      ElMessage.warning('微信推送 API URL 不能为空');
      return false;
    }
  }
  try {
    parseSmsRetryBackoff();
    if (smsConfig.provider === 'generic_http') {
      parseSmsFieldMapping();
    }
    if (smsConfig.provider === 'wxpusher') {
      parseSmsWxUids();
      parseSmsWxTopicIds();
    }
  } catch (err) {
    ElMessage.warning(err.message || '短信高级配置格式错误');
    return false;
  }
  return true;
};

const smsConfigsMatch = (saved, readback) => {
  const fields = [
    'enabled', 'provider', 'at_modem', 'generic_http', 'wxpusher', 'aliyun', 'tencent', 'phone_numbers',
    'retry_count', 'retry_backoff_seconds', 'ng_threshold', 'cooldown_seconds', 'queue_size',
    'offline_queue_max', 'offline_ttl_seconds',
    'summary_schedule_mode', 'shift_start_hour', 'shift_end_hour', 'send_night_window',
    'summary_count_source', 'summary_send_mode', 'summary_channel_ids',
  ];
  return fields.every((field) => JSON.stringify(saved?.[field]) === JSON.stringify(readback?.[field]));
};

const buildSmsPayload = () => ({
  enabled: smsConfig.enabled,
  provider: smsConfig.provider,
  at_modem: {
    port: smsConfig.at_modem.port.trim(),
    baudrate: smsConfig.at_modem.baudrate,
    encoding: smsConfig.at_modem.encoding,
    template: smsConfig.at_modem.template.trim(),
  },
  generic_http: {
    api_url: smsConfig.generic_http.api_url.trim(),
    request_method: smsConfig.generic_http.request_method,
    timeout_seconds: smsConfig.generic_http.timeout_seconds,
    token: smsConfig.generic_http.token.trim(),
    access_key: smsConfig.generic_http.access_key.trim(),
    access_secret: smsConfig.generic_http.access_secret.trim(),
    sign_name: smsConfig.generic_http.sign_name.trim(),
    template_id: smsConfig.generic_http.template_id.trim(),
    verify_ssl: smsConfig.generic_http.verify_ssl,
    field_mapping: smsConfig.provider === 'generic_http' ? parseSmsFieldMapping() : (smsConfig.generic_http.field_mapping || {}),
  },
  wxpusher: {
    app_token: smsConfig.wxpusher.app_token.trim(),
    uids: smsConfig.provider === 'wxpusher'
      ? parseSmsWxUids()
      : [...(smsConfig.wxpusher.uids || [])],
    topic_ids: smsConfig.provider === 'wxpusher'
      ? parseSmsWxTopicIds()
      : [...(smsConfig.wxpusher.topic_ids || [])],
    content_type: smsConfig.wxpusher.content_type,
    summary_template: smsConfig.wxpusher.summary_template.trim(),
    api_url: smsConfig.wxpusher.api_url.trim(),
    timeout_seconds: smsConfig.wxpusher.timeout_seconds,
    verify_ssl: smsConfig.wxpusher.verify_ssl,
  },
  aliyun: {
    access_key_id: smsConfig.aliyun.access_key_id.trim(),
    access_key_secret: smsConfig.aliyun.access_key_secret.trim(),
    sign_name: smsConfig.aliyun.sign_name.trim(),
    template_code: smsConfig.aliyun.template_code.trim(),
    region: smsConfig.aliyun.region.trim() || 'cn-hangzhou',
  },
  tencent: {
    secret_id: smsConfig.tencent.secret_id.trim(),
    secret_key: smsConfig.tencent.secret_key.trim(),
    sdk_app_id: smsConfig.tencent.sdk_app_id.trim(),
    sign_name: smsConfig.tencent.sign_name.trim(),
    template_id: smsConfig.tencent.template_id.trim(),
    region: smsConfig.tencent.region.trim() || 'ap-guangzhou',
  },
  phone_numbers: smsConfig.provider === 'wxpusher' ? [] : parseSmsRecipients(),
  retry_count: smsConfig.retry_count,
  retry_backoff_seconds: parseSmsRetryBackoff(),
  ng_threshold: smsConfig.ng_threshold,
  cooldown_seconds: smsConfig.cooldown_seconds,
  queue_size: smsConfig.queue_size,
  offline_queue_max: smsConfig.offline_queue_max,
  offline_ttl_seconds: smsConfig.offline_ttl_seconds,
  summary_schedule_mode: smsConfig.summary_schedule_mode,
  shift_start_hour: smsConfig.shift_start_hour,
  shift_end_hour: smsConfig.shift_end_hour,
  send_night_window: smsConfig.send_night_window,
  summary_count_source: smsConfig.summary_count_source,
  summary_send_mode: smsConfig.summary_send_mode,
  summary_channel_ids: [...smsConfig.summary_channel_ids],
});

const saveSmsConfig = async () => {
  if (!validateSmsForm()) return;
  smsSaving.value = true;
  try {
    const payload = buildSmsPayload();
    const saved = await updateSmsConfig(payload);
    const readback = await getSmsConfig();
    if (!smsConfigsMatch(saved.data, readback.data)) {
      throw new Error('短信配置保存后回读不一致');
    }
    applySmsConfig(readback.data);
    ElMessage.success('短信配置已保存并回读确认');
  } catch (err) {
    ElMessage.error(formatApiError(err, err?.message || '保存短信配置失败'));
  } finally {
    smsSaving.value = false;
  }
};

const sendSmsTest = async () => {
  smsTesting.value = true;
  try {
    const res = await testSms({ phone_numbers: parseSmsRecipients() });
    const receipt = res.data || {};
    if (!receipt.success && !receipt.queued) {
      const reason = receipt.message || receipt.error_code || '当前窗口快照未能入队';
      ElMessage.error(`当前窗口快照测试失败：${reason}`);
      return;
    }
    const status = receipt.status || (receipt.queued ? 'queued' : 'unknown');
    ElMessage.success(
      `当前未闭合窗口快照：${receipt.message || '已进入后台队列'}（${status}；非完整 12 小时窗口，不代表最终送达）`,
    );
  } catch (err) {
    ElMessage.error(`当前窗口快照测试失败：${formatApiError(err, '发送失败')}`);
  } finally {
    smsTesting.value = false;
  }
};

onMounted(async () => {
  await loadSmsConfig();
  if (smsConfig.provider === 'at_modem') {
    refreshSmsPorts(false);
  }
});
</script>
