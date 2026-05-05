# language: zh-CN
功能: 系统显示字段 + License 缓存 + 检测结果暴露
  作为客户工程师
  我希望前端 store 的品牌/工厂等字段持久化到后端
  并且实时规则触发时也能读到 License 信息
  以便所有导出场景都能正确拿到这些显示信息

  场景: PUT 系统显示字段后能 GET 回来
    当 我 PUT /api/v1/system/display 字段 brand_name="天钧" factory_name="东莞工厂"
    那么 响应状态应为 200
    并且 GET /api/v1/system/display 应返回 brand_name="天钧" factory_name="东莞工厂"

  场景: PUT License 缓存后能 GET 回来
    当 我 PUT /api/v1/system/license-cache 字段 customer="客户A" machine_id="MID-001"
    那么 响应状态应为 200
    并且 GET /api/v1/system/license-cache 应返回 customer="客户A" machine_id="MID-001"

  场景: PUT License 缓存空对象应返回 400
    当 我 PUT /api/v1/system/license-cache 字段为空
    那么 响应状态应为 400
