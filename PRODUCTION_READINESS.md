# AI 智参平台生产化说明

## 项目口径

- 项目经历时间：2024-10 至 2025-04（仅在该时间确属真实经历时使用）。
- 负责模块：营养师智能体，包括 RAG 检索、多轮问答、健康上下文和人工咨询衔接。
- 智能体目录：6 个正式智能体，代码默认全部启用；采购清单属于食养业务功能，未接真实供应商前不得包装成独立智能体或宣称已投入生产。
- 数据口径：`synthetic` 为容量与压测数据，`deidentified_real` 为经授权脱敏数据，`production` 才是线上业务数据。

## 数据可信规则

1. 合成账号统一使用 `syn_` 用户名前缀和 `SYN` 用户 ID 前缀。
2. 每次生成写入 `dataset_provenance`，登记时间范围、参数、数量与校验摘要。
3. 任何报表必须同时展示 `source_type`，禁止把合成数据称为真实用户数据。
4. 真实数据导入前必须完成授权、脱敏、最小化使用和留存周期评审。

## 推荐的容量验证基线

- 10,000 个合成用户；
- 50,000 次测评和 50,000 份报告；
- 50,000 个会话、200,000 条消息；
- 100,000 条打卡；
- 15,000 份食养计划和 45,000 条食谱明细；
- 时间分布覆盖 2024-10-01 至 2025-04-30。

预览命令：

```bash
python scripts/generate_synthetic_data.py
```

确认数据库为专用压测库后再执行：

```bash
python scripts/generate_synthetic_data.py --apply
```

## 上线证据

项目只有在具备以下证据后才能称为已上线：域名与 HTTPS、部署流水线记录、版本号、监控与告警、备份恢复演练、发布与回滚记录、持续冒烟结果。上线时间必须使用实际发布日期，不得倒填历史。

## 发布流程

1. 在专用生产数据库执行 `python scripts/apply_dataset_provenance_migration.py`。
2. 复制 `.env.example` 到服务器的密钥管理系统，禁止提交真实密钥。
3. 运行 `docker compose -f docker-compose.production.yml config` 校验必填变量。
4. 运行 `docker compose -f docker-compose.production.yml up -d --build`。
5. 将 `SMOKE_BASE_URL` 指向实际 HTTPS 域名后运行 `python -m pytest tests/smoke -q`。
6. 保存镜像版本、发布时间、冒烟结果、回滚版本和负责人，形成可核验发布记录。

生产编排只启动无状态 API；MySQL、Redis、MinIO、Milvus 应使用有备份和监控的独立服务。`/api/v1/ready` 中任一依赖失败时，实例不得接收新流量。
