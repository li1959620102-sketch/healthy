# AI 智参平台 · 上下文记忆（会话接续快照）

> 用途：新会话开头读这一份文件，即可完整接续本项目，无需重新翻 15 份文档。
> 生成时间：2026-09-10　|　工作区：`D:\AI_health`
> 配套：`.workbuddy/memory/MEMORY.md`（精简长期记忆）、`.workbuddy/memory/2026-09-09.md`、`2026-09-10.md`（逐日流水）

---

## 一、一句话定位

AI 智参（药食同源）健康平台，技术栈 FastAPI + MySQL + Milvus + MinIO + Ollama(bge-m3)。
由原 NRS2002 营养筛查 RAG 工程**借壳新建**。当前处在 **编码与自测阶段**：第 0 批、第一批已完成，**第二批待开工**。

## 二、当前进度（截至 2026-09-10）

| 阶段 | 状态 | 交付物 |
|---|---|---|
| 需求分析 | ✅ | `AI智参平台_需求分析.md`（源：`AI 智参平台需求文档.docx` V1.2） |
| 需求学习会方案 | ✅ | `AI智参平台_需求学习会方案.md` |
| 需求修订 V1.3 | ✅ | `AI智参平台_需求修订清单_V1.3.md`（Blocker 归零） |
| 需求评审 V2.0 | ✅ | `AI智参平台_需求评审报告.md`（结论：通过 Go，残留 Critical 1 + Minor 7） |
| 接口/数据库设计 V1.1 | ✅ | `设计_01_接口设计.md`、`设计_02_数据库设计.md`、`设计_03_UI交互与页面结构.md` |
| 交互原型 | ✅ | `prototype/index.html`（**功能范围权威基线**） |
| 设计评审 | ✅ | `设计评审_01_接口与数据库.md`、`设计评审_02_交互原型.md`（均"有条件通过"） |
| 测试准备 | ✅ | `测试_00_测试计划.md`、`测试_01_测试用例集.md`（216 条）、`测试用例清单.csv` |
| 编码 · 第 0 批 | ✅ | 统一响应 / 错误码 / 鉴权基线 / `/ready` 探针 |
| 编码 · 第一批 | ✅ | A 认证、B 用户、K 知识库、G 智能查询(SSE)、I 看板骨架、17 张表、合规拒答 |
| 编码 · 第二批 A | ✅ | D 模板、评分引擎（配置化）、E 测评、F 报告（SSE 真流式 + 降级 basic） |
| 编码 · 第二批 B（按原型补齐） | ✅ | C 营养师、H 咨询、J 管理、P 运营大屏、L 食养计划、M 智能荐菜、N 智能体中心/场景、O 舌象（辨识留桩待 S4） |
| **编码收口 + 单元/集成测试** | ✅ | `app/core/rules.py` 规则单一事实源、Redis 客户端、`tests/` 114 条（112 通过 / 2 跳过）；修 4 个真实缺陷（见 `AI智参平台_测试_02_单元与集成测试报告.md`） |
| **时效类规则 + 错误码同步** | ✅ | 游客配额（测评 `10004` / 查询 `60002`）、咨询超时懒清理、认领期限常量、草稿过期作废；设计_01 §1.3 与用例集错误码已同步；`tests/` 增至 **119 条（117 通过 / 2 跳过）**，新增 5 条时效用例 |
| **基础设施真连（Redis + MinIO）** | ✅ | 专属容器 `ai-health-redis`(6380) / `ai-health-minio`(9000) 由 compose 纳管；MinIO 去懒加载（导入期建连建桶）；`/ready` 将两者列为必需；`tests/` 增至 **124 条（124 通过 / 0 跳过）**，新增 5 条存储用例 |
| **原型基线切换** | ✅ | 权威基线改为 **`prototype/index_v2.html`**（用户提供），`prototype/index.html` 作废；差异清单见 `AI智参平台_后端与原型图一致性核对报告.md` |
| **第 4 批 P0 + P1 部分（按新原型）** | ✅ | 打卡 + 通知（2 表 6 接口）；五场景/智能体口径按原型重写（7 智能体 / 5 场景）；首页·风险·上下文·工作台 4 个聚合接口；客户表补 3 列；`tests/` 增至 **136 条（136 通过 / 0 跳过）** |
| **第 5 批 P1 余 + 第 6 批 P2（按新原型）** | ✅ | 食养周/月计划、采购清单、导出客户报表、Text2SQL（行级权限+图表+解读+导出）、报告 PDF 真实现；宴请/旅行/OCR/大屏方案/智能体监控/数字人（**留桩+交互**）；新增 9 表约 30 接口，`tests/` 增至 **148 条（148 通过 / 0 跳过）** |
| 编码 · 收尾 | ⬜ | S2/S3 复测、注销清理 cron、越权统一 404、S4/S5 达标后替换留桩实现 |

## 三、已锁定的硬决策（写码前必读，违背即返工）

1. **范围基线**：功能范围以 `prototype/index.html` 为准，设计文档只作补充（原型多出：体质/舌象测评、食养计划、智能荐菜、智能体中心、数字人小智、五大场景 + 运营大屏）。
2. **架构基线**：函数式路由 `@router.post`（中文 summary/tags）；向量库 **Milvus**；NRS2002 旧码整体入 `legacy_nrs2002/`（51 个文件，不删）；text2sql/text2echarts **保留为独立通用分析智能体**（不并入看板）。
3. **响应规范**：`{code, message, data, request_id}`，HTTP 一律 200，错误码分 1xxxx–9xxxx。
4. **SSE 鉴权**：60s 短票 `POST /chat/stream-ticket` + `GET /chat/query/stream?ticket=`（浏览器 EventSource 不能带 header）。
5. **降级语义**：`code=0` + `data.state=basic` + `degraded=true`；**禁止**用 50002（会触发前端统一错误弹窗）。
6. **技术红线**：绝不让 LLM 算分（规则引擎算分，LLM 只写分析文本）；绝不让 LLM 直连数据库；趋势归因用规则计算（维度分差值取最大项）。
7. **合规**：高危意图命中即拒答（用药/剂量/诊断/报告解读/孕妇婴幼儿），命中不调模型；报告必须带免责声明与来源引用。

## 四、当前阻塞（4 项外部依赖，不是代码问题）

| 编号 | 阻塞物 | 责任方 | 卡住什么 |
|---|---|---|---|
| **C1** | 评分权重与等级阈值的**具体数值** | 营养师顾问 | **已解耦，不再阻塞**：权重/阈值/版本读 `system_config`，引擎已用占位默认值跑通；顾问给数后执行一条 UPSERT 即可（见 §四） |
| **CHK-06** | 数据分析指标白名单清单 | 产品 | text2sql 权限边界 |
| **CHK-08** | 运营 / 医师 / 专家 角色定位 | 产品 | RBAC（当前 operator 角色已预置但未正式确认） |
| **S4 / S5** | 舌象图片识别 / 数字人 TTS 技术验证 | 需 Spike 实测 | 舌象测评、数字人助手能否进一期 |

**可绕行的部分（重要）**：改造清单 CHK-05 原文建议 —— 权重数值未定时，`assessment_report` 评分引擎**先按"加权和公式 + 阈值表配置"写，数值后填**。即：把权重抽到 `system_config`（带 `scoring_version`），先用占位默认值跑通链路，顾问返数后只改配置不改代码。**已完成**：D 模板 / E 测评 / F 报告 / C 营养师 / H 咨询 / J 管理 / P 运营大屏 / L 食养 / M 荐菜 / N 智能体中心 均已落地，只剩舌象 AI 辨识与数字人必须等 S4/S5。

## 五、下一步候选（按推荐度排序）

1. **第二批收尾**：舌象 AI 辨识（S4 达标后接）、数字人小智前端（S5）、游客限流、越权 404 统一。
2. **补验收标准文档**：评审 §十一标注 P3「验收标准完全空白」是**最大盲区**，且完全不依赖外部输入，可随时补。
3. **实现多轮对话上下文记忆**：`chat_session`/`chat_message` 表已有，但会话级记忆管理（历史轮次裁剪、摘要压缩、长期画像）尚未实现；旧代码曾有 `app.manager.memory_manager` 悬空引用。
4. **S2/S3 + S1 生产复测**：**S1 已于 2026-09-10 跑完，判定「未通过（部分达标）」**，详见 `AI智参平台_Spike_S1_报告时延实测报告.md`：
   - ✅ 评分骨架 P95 = **75ms**（标准 ≤2s）；规则算分 P95 = 21ms
   - ❌ 首字节 TTFB 热态 **14.2s**（标准 ≤1.5s）；完整生成 **23.5s**（标准 ≤10s）—— 本机 **CPU + qwen3:0.6b**
   - 顺带修掉一个真实缺陷：报告流原是"攒完再吐"的 **假流式**，已改真流式 + 改用 ChatOllama 让 `<arg_key:6124c78e>` 生效（49.4s → 23.5s）
   - **当前最该做的：拿这份报告去跟甲方回调 A1 指标**（建议 ≤0.5s 出评分 / ≤3s 首字节 / ≤30s 流式完成），生产 GPU 环境需重跑定稿
   - S2（RAG 准确率 50 条）、S3（并发 30TPS）未跑

## 六、已落地代码清单（第一批 + 第二批，改代码前先确认不重复造轮子）

- `app/routers/`：`auth_router.py`(A)、`user_router.py`(B)、`knowledge_router.py`(K)、`chat_router.py`(G)、`dashboard_router.py`(I)
- `app/services/chat_query_service.py`：懒加载 RAG + 拒答优先 + 优雅降级
- `app/models/mysql/platform.py`：**17 张表**（含 `dietitian_customer.active_key` 生成列、TongueImage、DietPlan、DataDeletionTask）
- `app/models/mysql/user.py`、`knowledge_file.py`：扩展列 + 索引；`migrations/001_extend_legacy_tables.sql`
- `app/schemas/platform.py`、`app/core/errors.py`、`responses.py`、`deps.py`、`refusal.py`
- `main.py`：request_id 中间件 + 4 个全局异常处理器 + `/api/v1/ready`

**遗留（非阻塞）**：短信 `/auth/sms/send` 为占位；`DELETE /me` 只建清理任务，15 日 cron 未实现；`minio_client.py` 已改懒加载但未做 MinIO 副本清理。

### 第二批新增（2026-09-10）

- `app/services/scoring_engine.py`：规则评分引擎（权重三层回退 + 百分制阈值 + 建议生成）
- `app/dao/assessment_dao.py`：Template/Record/Report/Annotation 四个 DAO + `session_scope()` 事务
- `app/services/assessment_service.py`：发起 / 草稿 / 提交（幂等+算分）/ 作废 / 列表 / 游客认领 / 重生成
- `app/services/report_service.py`：LLM 只写分析文本，重试 3 次（2/4/8s）→ 降级 `state=basic`
- `app/routers/template_router.py`、`assessment_router.py`（含报告 SSE 流）、`report_router.py`
- `app/core/deps.py` 新增 `resolve_sse_user()`；`app/core/errors.py` 新增 50004 / 50005
- `main.py` 挂载三个新 router（`/api/v1/templates` `/assessments` `/reports`）

**本轮修复的既有缺陷**：`auth_router.py` 的 `ErrCode.USER_CONFLICT` 不存在（注册冲突会 500，已改 `USER_EXISTS`）；幂等键全局唯一导致跨记录撞键 1062（提交前改为先全局查键）。

### 编码收口 + 测试（2026-09-10，119 条：117 通过 / 2 跳过）

- `app/core/rules.py`：**业务规则单一事实源**（手机号/密码/验证码/身高/体重/年龄/BMI/答案文本/必答项/权重/分页 + 时效常量）。改校验只改这里。
- `app/utils/redis_client.py`：Redis 懒加载 + 5s 熔断 + 优雅降级；`/ready` 将其列为 `optional`。**本机 Redis 不可用（6379 是失效转发口），相关用例自动 SKIP**。
- `pytest.ini` + `tests/unit/`（89 条，0.4s）+ `tests/integration/`（30 条，连真实 MySQL）。
- **修 7 个真实缺陷**：① 参数校验失败因 `ctx` 带 ValueError 对象而 500；② `ErrCode.DB_ERROR` 从未定义（4 处引用）；③ 并发提交撞 `uk_record` 产生 1062/500（BV-29 不通过）→ 加行锁 + 撞键兜底；④ `scoring_engine` 类级可变状态并发串味；⑤ 过期草稿作废被 `session_scope` 异常回滚吞掉 → 作废后显式 `s.commit()` 再抛错；⑥ `consultation_dao` 缺 `timedelta` 导入；⑦ 断网导致部分 `Edit` 静默丢失（**教训：关键编辑后必须 grep 核验落盘**）。
- **错误码已同步**：设计_01 §1.3 重写为现网分段；用例集 6 处编号更新（`20001→10001`、`30001→20002`、`20003→10005`、BV-01 `10001→90001`、BV-22 `20002→10002`）。
- 未覆盖：BV-26（注销告警，需定时任务）、BV-28（需 MinIO）、BV-09/10。

### 时效类规则（2026-09-10，新增，`tests/integration/test_temporal_rules.py` 5 条全绿）

- **BV-21 游客配额**：测评——`RecordDAO.count_guest_completed()` + `start_assessment`，达标返 `10004`；查询——`chat_router._guest_hash()/_guest_quota_used()`，达标返 `60002`（计数落在 `chat_session.guest_token`）。
- **BV-24 草稿有效期**：`save_draft`/`submit` 里若 `expire_at < now` 则 `void` + 提交 + 抛 `90001`。
- **BV-25 咨询超时**：`ConsultationDAO.expire_stale()` 懒清理，`ConsultationService._sweep_timeout()` 在 list_mine / pending_queue / messages / send 四处调用（**无需定时任务**）。
- **BV-27 认领期限**：`claim_guest` 的硬编码 `days=7` 改为 `rules.CLAIM_WINDOW_DAYS`。
- **坑**：`session_scope()` 内先 `void` 再 `raise` 会被回滚 → 必须 `s.commit()` 再抛错。

### 第二批 B · 按原型补齐七组（2026-09-10，36/36 冒烟全绿）

- `app/dao/dietitian_dao.py`、`app/services/dietitian_service.py`、`app/routers/nutritionist_router.py`（C 营养师）
- `app/dao/consultation_dao.py`、`app/services/consultation_service.py`、`app/routers/consultation_router.py`（H 咨询）
- `app/dao/admin_dao.py`、`app/services/admin_service.py`、`app/routers/admin_router.py`（J 管理 + P 运营大屏）
- `app/dao/plan_dao.py`、`app/services/plan_service.py`、`app/routers/plan_router.py`（L 食养）、`recommend_router.py`（M 荐菜）、`agent_router.py`（N 智能体中心/场景）、`tongue_router.py`（O 舌象）
- `main.py` 挂载七个新 router；`app/schemas/platform.py` 追加对应请求模型
- **修复**：`plan_service.create_plan`/`update` 在 `with session_scope()` 内调 `detail()`（新开 session），事务未提交 → 读到旧状态 `generating`。已把 `return` 移到 `with` 之后。**通用坑：跨 session 的读必须在写事务提交之后。**
- **关键设计**：食养食谱骨架走规则库（确定性），LLM 只写 ≤250 字总述；智能体目录/五大场景配置驱动（`agent.catalog` / `scene.config`）；舌象仅存储 + 状态管理，AI 辨识留桩待 S4。

## 七、环境事实（踩过的坑，别再踩）

- Python 必须用 `D:\conda\envs\ai_py311\python.exe`（base 环境缺 jieba，起不来）。
- 该环境 `jwt==1.4.0` **不是 PyJWT**，无 encode/decode → 项目自签 HMAC-SHA256 JWT。
- 8018 端口被旧服务常驻占用，测试用 8019。
- 本机开 Clash，urllib 访问 127.0.0.1 会被代理拦成 502 → 测试需绕代理。
- Bash 起的后台进程随命令结束被回收 → 接口测试一律用 `fastapi.testclient.TestClient`。
- **Docker 端口转发会"空接"**：容器停止后 `com.docker.backend` 仍占着宿主端口，TCP 能握手但立即 FIN → 看着像"服务在跑但不回应"。**判断服务可用必须发真实协议包**（Redis 用 `PING`、MinIO 用 `bucket_exists`），不能只看端口 LISTENING。
- **本机其它项目占用的端口**：`6379`（talent 项目的 Redis，带 `requirepass`）、`9100`（Milvus 内部 MinIO）→ 本项目专用 `6380` / `9000`，定义见 `docker-compose.infra.yml`。
- **Redis / MinIO 现为必备依赖**（2026-09-10 起不再跳过/懒加载）：`ai-health-redis`(6380) + `ai-health-minio`(9000) 由 compose 纳管；`MinioClient` 导入期即建连建桶（fail-fast），`/ready` 把两者列为必需项。
- 一键起停：`docker compose -f docker-compose.infra.yml up -d` / `down`。密钥只放 `.env`（已入 `.gitignore`），仓库内只有脱敏的 `.env.example`。

## 八、协作约定

- 用户是小白：命令少而准，一步到位，不反复试探；动手前说明动什么、落在哪、是否可逆。
- 汇报只讲 **delta**（本次新增/变更），不重复已交付内容，避免大段表格。
- 报错重试时交代"为什么又试了一次"；若零改动，明确说"零改动"并给验证依据。
