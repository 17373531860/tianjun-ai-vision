# language: zh-CN
功能: 自定义导出系统
  作为客户工程师
  我希望能创建/管理导出模板，并把检测数据按客户要求的任意格式导出
  以便对接客户的内部 ERP/MES 系统

  场景: 字段注册表能列出所有可用字段
    当 我请求 GET /api/v1/export/fields
    那么 响应状态应为 200
    并且 响应应包含字段组列表
    并且 字段组应至少包含 cycle 和 system

  场景: 创建用户自定义模板
    当 我创建模板 名称="测试TXT" format=txt scope=both content="hello {{ system.app.version }}"
    那么 响应状态应为 200
    并且 模板 id 应被分配
    并且 模板 is_system 应为 false

  场景: 列出模板能拿到自建模板
    假设 已有一个自建模板 名称="列表测试TXT" format=txt
    当 我请求 GET /api/v1/export/templates
    那么 响应状态应为 200
    并且 模板列表应包含名称="列表测试TXT"

  场景: 删除自建模板成功
    假设 已有一个自建模板 名称="待删除模板" format=txt
    当 我删除该模板
    那么 响应状态应为 200
    并且 该模板再次 GET 应返回 404

  场景: 系统预设模板不可被删除
    假设 系统预设模板已 seed
    当 我尝试删除 builtin_id="builtin_cycle_simple_txt" 对应的系统模板
    那么 响应状态应为 400

  场景: 用 system 上下文渲染 txt 预览
    当 我用 template_content="version={{ app.version }}" 调用 POST /api/v1/export/preview
    那么 响应状态应为 200
    并且 rendered 字段应包含 "version="
    并且 error 字段应为 null

  场景: license_payload 注入能被模板引用
    当 我用 template_content="customer={{ license.customer }}" 和 license_payload={"customer":"客户A"} 调用 preview
    那么 响应状态应为 200
    并且 rendered 字段应等于 "customer=客户A"
