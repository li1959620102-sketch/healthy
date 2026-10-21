# AI 智参平台 Agent Runtime 评估基线

评估类型：本地代码合同评估与故障注入，不代表线上客户生产数据验收。

## 合同评估

评估集覆盖主编排 Agent 和六个子 Agent，检查每个 Agent 是否有注册工具，工具风险策略是否与任务要求一致。

| Agent | 场景 | 核心工具 | 结果 |
| --- | --- | --- | --- |
| orchestrator | 规划健康任务 | agent.delegate | 通过 |
| assessment | 发布健康报告 | health_report.publish | 通过，需审批 |
| diet | 生成食养计划 | diet_plan.generate | 通过 |
| dish | 推荐低敏菜单 | recipe.search | 通过 |
| steward | 分析打卡 | checkin.read | 通过 |
| dietitian | 检索饮食知识 | knowledge.search | 通过 |
| data | 只读统计 | sql.execute_readonly | 通过 |

当前本地合同评估结果：7/7，通过率 100%。

## 故障恢复评估

通过工具执行器注入一次临时故障：

```text
第一次工具调用失败
→ 识别为可重试错误
→ 退避等待
→ 第二次调用成功
```

当前本地故障恢复结果：重试 2 次后成功。

## 线上验收前仍需补充

- 真实模型 usage/token 采集；
- 真实 MySQL、Milvus、Ollama 故障演练；
- 进程重启后的任务恢复；
- 30 TPS 并发下 Agent 成功率与 P95；
- 人工审批队列的真实操作记录；
- 生产环境成本和告警阈值。
