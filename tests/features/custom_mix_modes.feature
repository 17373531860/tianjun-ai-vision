# language: zh-CN
功能: 自定义模式混合子状态机 (基于模式 × 混合模式 四组合)
  作为包装线客户
  我希望自定义模式在基础模式 (顺序/检测) 之外再混合逐件或跟踪计数
  使得 "每个动作都做了" 和 "每个物品都放了" 在同一个周期里同时被校验

  背景:
    假设 后端处于测试模式 (RUNTIME_MODE=test)
    并且 通道 0 的混合模式测试环境是干净的

  场景大纲: 步骤正确且物品达标时判合格
    假设 一个基于 "<based_on>" 并混合 "<mixed_with>" 的自定义项目已载入通道 0
    当 我跑一个物品达标的混合周期剧本
    那么 合格计数应增加 1 且不良计数不变

    例子:
      | based_on   | mixed_with |
      | sequential | per_item   |
      | sequential | tracking   |
      | detection  | per_item   |
      | detection  | tracking   |

  场景大纲: 步骤正确但物品不达标时周期降级为不良
    假设 一个基于 "<based_on>" 并混合 "<mixed_with>" 的自定义项目已载入通道 0
    当 我跑一个物品不达标的混合周期剧本
    那么 不良计数应增加 1 且合格计数不变

    例子:
      | based_on   | mixed_with |
      | sequential | per_item   |
      | sequential | tracking   |
      | detection  | per_item   |
      | detection  | tracking   |

  场景: 不混合时同样的剧本不受物品影响 (零差异对照)
    假设 一个基于 "sequential" 但未混合的自定义项目已载入通道 0
    当 我跑一个物品不达标的混合周期剧本
    那么 合格计数应增加 1 且不良计数不变
