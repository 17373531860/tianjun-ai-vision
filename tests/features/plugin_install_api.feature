# language: zh-CN
功能: 插件管理 API
  作为现场交付人员
  我希望通过后端 API 安装、激活、停用和卸载已签名插件
  以便 Settings 插件管理页可以进入真实产品闭环

  背景:
    假设 后端插件测试密钥已配置

  场景: 上传安装已签名插件成功
    假设 我准备了客户码为 "internal-test" 的已签名插件包
    当 我通过 API 上传安装该插件包
    那么 插件安装 API 应返回成功
    并且 插件列表应包含客户码 "internal-test"

  场景: 非法插件包会被拒绝
    假设 我准备了一个非法插件包
    当 我通过 API 上传安装该插件包
    那么 插件安装 API 应返回错误码 "PLUGIN_PACKAGE_INVALID"

  场景: 插件客户码与授权不匹配会被拒绝
    假设 当前 License 客户码为 "other-customer"
    并且 我准备了客户码为 "internal-test" 的已签名插件包
    当 我通过 API 上传安装该插件包
    那么 插件安装 API 应返回错误码 "PLUGIN_CUSTOMER_MISMATCH"

  场景: 单 active 插件激活策略
    假设 我已通过 API 安装客户码为 "internal-test" 的插件
    并且 我已通过 API 安装客户码为 "second-test" 的插件
    当 我激活客户码为 "internal-test" 的插件
    并且 我激活客户码为 "second-test" 的插件
    那么 插件列表的 active 客户码应为 "second-test"

  场景: 停用 active 插件
    假设 我已通过 API 安装客户码为 "internal-test" 的插件
    并且 我激活客户码为 "internal-test" 的插件
    当 我停用客户码为 "internal-test" 的插件
    那么 插件列表不应有 active 客户码

  场景: 卸载插件保留业务数据语义
    假设 我已通过 API 安装客户码为 "internal-test" 的插件
    当 我卸载客户码为 "internal-test" 的插件
    那么 插件列表不应包含客户码 "internal-test"
