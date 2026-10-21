# 发布与维护记录

## 项目周期

- 业务项目周期：2024-10-01 至 2025-04-30。
- 本文件描述代码能力与发布要求；具体线上发布批次必须以实际 CI/CD 记录为准。

## 当前版本能力

- 6 个正式启用智能体。
- 营养师智能体的 RAG、多轮对话、健康上下文、流式输出和人工转接。
- 测评、报告、食养计划、打卡、咨询、对象存储和数据分析链路。
- 生产安全基线、来源审计、容器化部署和冒烟测试。

## 发布门禁

发布前必须确认：

1. 生产 `SECRET_KEY` 为独立强密钥。
2. `ENV=production`、`SMS_DEMO_MODE=false`、`INIT_DEMO_USER=false`。
3. MySQL、Redis、MinIO、Milvus 均通过就绪探针。
4. 数据库迁移已记录，备份和恢复演练有结果。
5. `tests/smoke` 指向真实 HTTPS 域名并全部通过。
6. 生产数据统计标注来源，不把 synthetic 数据写入真实业务报表。

## 本轮交付资产（2026-10-17）

- 生产依赖已覆盖 APScheduler、LangGraph、faster-whisper、CTranslate2、PyAV 与 edge-tts。
- `Dockerfile` 已打包前端静态目录；容器健康检查统一使用 `/api/v1/ready`；ASR 权重由生产 compose 以只读宿主机目录挂载。构建支持通过 `PIP_INDEX_URL` 与 `PIP_DEFAULT_TIMEOUT` 切换稳定 PyPI 镜像并延长超时，未更改依赖版本。
- 新增 `scripts/backup_database.sh`、`scripts/restore_database.sh`、`scripts/release.sh`、`scripts/rollback.sh`。恢复必须显式设置 `RESTORE_CONFIRM`，发布与回滚均通过 `/api/v1/version` 校验实际版本。
- 新增 `deploy/nginx/ai-health.conf`，包含 HTTP 到 HTTPS 跳转、TLS 1.2/1.3、反向代理头和 SSE 禁用缓冲配置。
- 新增 `.github/workflows/ci.yml`：依赖安装、Python 编译检查、生产 Compose 校验、单元测试；配置 `STAGING_BASE_URL` 后自动运行真实 staging 冒烟。
- 本地验证：139 项单元测试通过；全量回归 476 项，0 失败、0 错误、13 项环境性跳过。

以上是可审计的代码与流水线资产，不等同于已经完成真实域名 HTTPS、生产备份恢复演练或生产发布；这些项目必须在目标环境执行后补入发布记录。
