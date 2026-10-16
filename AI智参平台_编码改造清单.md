# AI 智参平台 ·**&#x20;编码与自测阶段 ·&#x20;**&#x4EE3;码对账与改造清单

> 阶段：编码与自测（开工前核查）　|　日期：2026-09-09  
> 参照：《设计_01_接口设计》《设计_02_数据库设计》《设计评审_01（3 项 Blocker）》《需求修订清单 V1.3》  
> 范围：只对现状代码做核查，未改动任何文件。结论均附 `文件:行号` 证据。

---

## 0. 一句话结论

**现有代码是 NRS2002 营养筛查 RAG 系统的"骨架 + 知识库雏形"，与 AI 智参平台不是小修关系，而是"借壳新建"**：

- 11 组接口里 **C（营养师）/ D（模板）/ F（报告）/ H（咨询）/ J（管理）5 组完全空白**，需从零写；
- **E（测评）、G（智能查询）、K（知识库）3 组有代码但语义/路径/结构都是 NRS2002 那一套，需改或替换**；
- **A（认证）、B（用户）有雏形但路径与结构不符**，需改 + 补（短信、refresh、注销、导出）；
- **I（看板）的 text2sql/text2echarts 是通用分析智能体，与设计"个人营养看板"语义不同，待定**；
- 数据库**只有 3 张旧表**，设计要的 11 张新表 + 2 张扩展列全缺；
- 设计评审 **3 项 Blocker（B1 SSE 鉴权 / B2 降级码 / B3 报告流）目前零基础**，SSE 和降级码都没开始。

**建议先定 4 条架构基线（见 §2），再按"设计准入三批"分批开工，不要模块级并行乱写。**

> **⚠️ 2026-09-10（续）范围基线变更**：经用户确认，**本平台交互原型图（`prototype/index.html`，标题"AI智参（药食同源）平台 - 全量交互原型"）取代《设计_01/02/设计评审》成为功能范围的权威基线**。原设计文档只覆盖原型的一部分（用户体系/测评/报告/营养师RAG/看板），**原型还多出 6 类功能**：①体质测评+舌象体质测评（图片识别）②食养食疗计划 ③智能荐菜智能体 ④智能体中心 ⑤数字人助手（小智）⑥五大健康场景 + 运营可视化大屏视角。以下 §3.5、§5、§8 已按原型扩展；§0/§1/§2/§3.1–3.4（代码层发现）不受范围来源影响，仍然有效。
>
> **两项技术待验证（必须 Spike 后才能排期，不能默认"能做"）**：舌象图片识别、数字人渲染——两者在需求评审中均被判定为"二期/技术未验证"，现因原型纳入一期，须在开工前补 Spike（见 §5 第 0.5 批 / §8）。

---


## 1. 现状快照（核查证据）

| 项     | 现状                                                                                                           | 证据                                                |
| ----- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------- |
| 已挂载路由 | 7 个：`/file` `/chunks` `/rag` `/text2sql` `/query` `/user` `/assessment`                                      | `main.py:58-64`                                   |
| 路由风格  | 类注册式 `BaseRouter._register_routes()`                                                                         | `app/routers/base_router.py`                      |
| 统一响应  | `ApiResponse{status,message,data,page}`，**无 `code` / 无 `request_id`**                                        | `app/schemas/common.py:6`                         |
| 错误码   | **无全局异常处理器**，无 1xxxx–9xxxx 分段                                                                                | 全仓无 `add_exception_handler`                       |
| 鉴权    | JWT 自研 HMAC，**有效期 7 天**，无 refresh、无游客、无 RBAC 依赖                                                              | `app/utils/security.py:20,73`                     |
| 数据库表  | 仅 `sys_user` / `knowledge_files` / `user_health_risk_assessment`                                             | `app/utils/db.py:33-34` 仅 import 2 个              |
| 向量库   | 代码默认 Milvus（`config.py:58` 集合 `nrs2002`），但根目录有 `chroma.sqlite3` + 空 `app/chromadb/`                          | 选型混乱                                              |
| 悬空模块  | `nutritionist_agent_service.py` import `app.chat.nutritionist_tools`、`app.manager.memory_manager`，**两包均不存在** | `app/services/nutritionist_agent_service.py:9,11` |
| 健康检查  | 只有 `/api/health`，**缺 `/ready`**                                                                              | `main.py:77`                                      |
| CORS  | `allow_origins=["*"]` 且 `allow_credentials=True`（浏览器不规范，会失效）                                                 | `main.py:71`                                      |

---

## 2. 四件必须先定的架构基线（不动工就别写业务代码）

| #    | 基线                                                                                                            | 要改的文件                                                       | 原因                                                       |
| ---- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------- |
| B0-1 | **统一响应结构** 改为 `{code, message, data, request_id}`，加全局异常处理器统一吐错误码                                              | `app/schemas/common.py`、`main.py` 加 exception handler       | B2 降级码、M3 越权 404、§4.4「错误码可见」全依赖它；现在 `request_id` 全仓 0 出现 |
| B0-2 | **错误码分段** 落地 1xxxx–9xxxx（含 50002→改 `code=0+state` 的降级方案）                                                      | 新建 `app/core/errors.py` + 全局 handler                        | 设计 §1.3，测试用例 SEC/M 全依赖                                   |
| B0-3 | **鉴权基线**：JWT 2h + refresh 7d + 游客 `guest_token` + `Depends` 注入的 `current_user`/`optional_user`/`require_role` | `app/utils/security.py`、`app/core/deps.py`、`auth_helper.py` | A/B/D/G/E 全组依赖；当前 `optional_user` 从没被 `Depends` 调用       |
| B0-4 | **确定向量库选型**（Milvus 还是 Chroma），并释放 `/api/v1/rag` 前缀冲突                                                          | `app/config.py`、`main.py`                                   | 设计 G 组智能查询要挂 `/api/v1/chat`，现被 NRS2002 占用                |

> 这 4 条不做，后面每个模块都得各自造轮子，且会对不上测试用例。

---

## 3. 增 / 删 / 改 / 查 清单


### 3.1 删（NRS2002 旧代码，属于返工风险，建议尽快清）

| 编号     | 对象                      | 位置                                                                                                                                                                                                                                                  | 说明                                                           |
| ------ | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| DEL-01 | NRS2002 测评路由            | `app/routers/assessment_router.py`                                                                                                                                                                                                                  | 写 `user_health_risk_assessment`，评分 0–7 分制，与模板化测评无关           |
| DEL-02 | NRS2002 RAG 路由          | `app/routers/chatbot_query_router.py`                                                                                                                                                                                                               | `/systemRagQuery` 是 NRS2002 评分检索，`collection_name:"nrs2002"` |
| DEL-03 | NRS2002 模型与 DAO/Service | `app/models/mysql/health_assessment.py`、`app/dao/health_assessment_dao.py`、`app/services/health_assessment_service.py`                                                                                                                              | "按 NRS2002 总分判定风险等级"，与药食同源无关                                 |
| DEL-04 | 悬空模块                    | `app/services/nutritionist_agent_service.py`                                                                                                                                                                                                        | import 不存在的 `app.chat`/`app/manager`，导入即报错                   |
| DEL-05 | 硬编码 NRS2002             | `app/services/rag_query_service.py`、`rag_query_rerank_service.py`（`_get_nrs2002_prompt`）、`app/config.py:61` `MILVUS_DEFAULT_COLLECTION="nrs2002"`、`app/designer/strategy/chunk/minio_metadata_strategy.py`、`app/services/knowledge_file_service.py` | 全仓散落的 `business_scene:"NRS2002"`                             |
| DEL-06 | 旧资料/实验                  | 根 `nrs2002_full.md`、`user_health_risk_assessment.sql`、`app/test/`（langchain/dify/milvus 实验）                                                                                                                                                         | 不属于新平台                                                       |
| DEL-07 | Vanna 训练样例              | `app/utils/vanna_engine.py`、`app/config/training_data.json`                                                                                                                                                                                         | 硬编码 `user_health_risk_assessment` 的 SQL 样例，属旧分析智能体           |

> 建议：先**建 `app/legacy_nrs2002/` 目录整体搬走**（保留可回溯），不要直接 `rm`，等新功能跑通再删。


### 3.2 改（现有但不符设计，需改造）

| 编号     | 模块     | 现状 → 改为                                                                                                                           | 位置                                                        |                                                   |                                       |
| ------ | ------ | --------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------- | ------------------------------------- |
| MOD-01 | 用户路由   | \`POST /api/v1/user/register                                                                                                      | login                                                     | me`→ 改为`auth`/`users`分组、带`code/request_id\`、支持手机号 | `app/routers/user_router.py:59,68,77` |
| MOD-02 | 统一响应   | `ApiResponse.status` → `code`；补 `request_id` 全链路                                                                                  | `app/schemas/common.py`                                   |                                                   |                                       |
| MOD-03 | 知识库路由  | `/file/uploadFile`(POST)、`/search`(POST) → `POST /knowledge/docs`、`GET /knowledge/docs`(列表+向量化状态)、补 `DELETE /knowledge/docs/{id}` | `app/routers/file_handle_router.py:43,53`                 |                                                   |                                       |
| MOD-04 | 知识库模型  | `knowledge_files` 补 `category/source/doc_version/embed_status/embedded_at/chunk_count` + 索引                                       | `app/models/mysql/knowledge_file.py:11`                   |                                                   |                                       |
| MOD-05 | 用户模型   | `sys_user` 扩 `phone_cipher/phone_hash/email_cipher/wechat_openid/last_active_at/deleted_at` + 唯一/二级索引                             | `app/models/mysql/user.py:10`                             |                                                   |                                       |
| MOD-06 | 智能查询路由 | `/rag/systemRagQuery`(同步) → `POST /chat/query`(**SSE 流式**) + 会话类接口                                                                | `app/routers/chatbot_query_router.py`                     |                                                   |                                       |
| MOD-07 | 鉴权     | token 7d → 2h + refresh 7d；补游客 `guest_token`；`Depends` 式 RBAC                                                                     | `app/utils/security.py`、`auth_helper.py`                  |                                                   |                                       |
| MOD-08 | 建表入口   | `init_tables()` 仅 import 2 个 model → 注册全部新 model                                                                                  | `app/utils/db.py:33-34`                                   |                                                   |                                       |
| MOD-09 | 看板路由   | `text2sql`/`text2echarts` 是通用分析智能体，需决定保留为独立能力还是改造为个人营养看板（见 §3.4 查-04）                                                             | `app/routers/text2sql_router.py`、`text2echarts_router.py` |                                                   |                                       |
| MOD-10 | CORS   | `allow_origins=["*"]+credentials` → 明确 origins 白名单                                                                                | `main.py:71`                                              |                                                   |                                       |
| MOD-11 | 健康检查   | 补 `/ready`（探测 MySQL/向量库/模型）                                                                                                       | `main.py` 增                                               |                                                   |                                       |


### 3.3 增（全新模块 / 表 / 接口）

**数据库（新建 model，共 11 张 + 2 张扩展）—— `app/models/mysql/` 下**

| 表                                       | 设计章节  | 关键字段                                                                                                              |
| --------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------- |
| `user_profile`                          | §3.2  | 身高/体重/疾病史/饮食偏好（快照源）                                                                                               |
| `dietitian_profile`                     | §3.3  | 资质/擅长领域/审核状态                                                                                                      |
| `dietitian_customer`                    | §3.4  | **`active_key` 生成列 + 唯一索引**（解 B2 绑定机制）                                                                            |
| `assessment_template`                   | §3.5  | 题目 JSON、维度权重、`status`(草稿→待审核→已发布→已下线)、版本                                                                          |
| `assessment_record`                     | §3.6  | `guest_token`、`idempotency_key`、`template_version`、`profile_snapshot`、`status`、乐观锁 `version`                      |
| `assessment_report`                     | §3.7  | **`score/level/dimension_scores/suggestions` 独立列**、`state`(generating/basic/complete)、`scoring_version`、引用 doc_id |
| `report_annotation`                     | §3.8  | 营养师批注                                                                                                             |
| `chat_session` / `chat_message`         | §3.9  | 会话/消息/转人工关联                                                                                                       |
| `consultation` / `consultation_message` | §3.10 | 1v1 咨询状态机、超时                                                                                                      |
| `operation_log`                         | §3.11 | 操作审计                                                                                                              |
| `system_config`                         | §3.12 | 智能体参数、阈值、权重（带 `scoring_version`）                                                                                  |
| `data_deletion_task`                    | 评审 M5 | 注销 15 日清理（覆盖 MySQL+向量库+MinIO）                                                                                     |

**接口（新建 router）—— `app/routers/` 下**

- A 认证：`auth_router`（短信/注册/登录/oauth/refresh/logout）— NEW
- C 营养师：`nutritionist_router`（申请/公开信息/搜索/邀请码/绑定/解绑/客户列表/批注）— NEW
- D 模板：`template_router`（CRUD/版本/审核/上下线）— NEW
- E 测评：`assessment_router` 重写（发起/草稿 PATCH/submit 幂等/cancel/regenerate/guest/claim）— NEW（替换 NRS2002）
- F 报告：`report_router` + **`GET /assessments/{id}/report/stream`(SSE)** — NEW
- G 智能查询：`chat_router`（query SSE/会话/转人工）— NEW（替换 NRS2002 rag）
- H 咨询：`consultation_router` — NEW
- I 看板：`dashboard_router` — NEW（或改造 text2sql/text2echarts）
- J 管理：`admin_router` — NEW
- 基础设施：`POST /chat/stream-ticket`（解 B1）、`/ready` — NEW

### 3.4 查（待确认 / 风险 / 需排查项）

| 编号     | 事项                                      | 为什么查                                         | 建议                                                  |
| ------ | --------------------------------------- | -------------------------------------------- | --------------------------------------------------- |
| CHK-01 | 向量库选型 Milvus vs Chroma                  | 代码两处打架，影响 G 组与知识库全部实现                        | 先定，再写 SSE/RAG                                       |
| CHK-02 | 路由风格：类注册式 vs 设计要求的函数式                   | 设计 §4.2 写函数式 + `Depends`，现有是类注册              | 二选一，统一到一种；函数式更贴合 Swagger 中文要求                       |
| CHK-03 | `nutritionist_agent_service.py` 是否曾计划启用 | 悬空 import 会让人误以为"营养师智能体已就绪"                  | 确认后删除或补全 `app/chat`、`app/manager`                   |
| CHK-04 | text2sql/text2echarts 归谁                | 设计 I 组是"个人营养看板"，现有是"通用 NL→SQL 分析智能体"，两者不是一回事 | 产品确认：保留为独立智能体，还是并入 I 组                              |
| CHK-05 | 评分权重数值（C1）是否已定                          | 评分类代码与用例都依赖它                                 | 没定则 `assessment_report` 评分引擎先按"加权和公式 + 阈值表配置"写，数值后填 |
| CHK-06 | 指标白名单内容（M6）                             | 数据分析智能体只能查预定义指标                              | 需先有指标清单才能写 `text2sql` 的权限边界                         |
| CHK-07 | 三项 Spike 是否跑过                           | A1/A2/A4 性能若无实测，接口阈值可能不可达                    | 开发前先跑 S1/S2/S3（见需求 V1.3 §七）                         |
| CHK-08 | 运营 / 医师 / 专家 角色定位                       | 原型出现 运营（大屏）与 医师/专家，设计 RBAC 只有 用户/营养师/管理员     | 运营=独立角色；医师/专家先按"营养师资质子类"处理，待产品确认是否独立建角色             |

---


### 3.5 增（原型专属功能，按"以原型为准"补充）

> 下表为原型有、原设计文档未覆盖的功能。表/接口均 NEW，需同步扩展《设计_01 接口》与《设计_02 库》。

| 原型功能                         | 归属接口组           | 新增内容                                                                                                                                           | 技术注意                    |
| ---------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| **体质测评 / 舌象体质测评**            | E 测评（扩展）        | `assessment_template` 增 `type`(risk/nutrition/constitution/tongue)；`assessment_record` 关联 `tongue_image_id`；NEW `tongue_image`（舌图存 MinIO + 状态） | 舌象=图片识别，**需 S4 Spike**  |
| **食养食疗计划 / 食养计划**            | NEW F2 食养计划     | NEW `diet_plan` / `diet_plan_recipe`（方案+食谱，关联报告/体质）；NEW `plan_router`（生成/查看/调整）                                                                | 通常基于报告+体质生成，依赖 F/E      |
| **智能荐菜智能体**                  | G 智能查询（扩展）或 NEW | 复用 RAG + 食材库；NEW `recommend_router`（`POST /recommend/dishes` SSE）或并入 `/chat` 的 agent 路由                                                        | 依赖知识库 K 的"食材库"语料        |
| **智能体中心（页面）**                | NEW 智能体中心       | NEW `agent_center_router`（`GET /agents` 列出 4+ 智能体，读 `system_config`/agent 定义）                                                                  | 配置驱动，前端列表               |
| **数字人助手 / 小智**               | 交互层（前端为主）       | 后端复用 `/chat/query` SSE；可选 TTS 语音流接口 `GET /chat/tts`；数字人渲染在前端/第三方                                                                               | **需 S5 Spike（TTS/数字人）** |
| **五大健康场景**（控糖/慢病/高血压/孕期/痰湿…） | 首页/配置           | `system_config` 增 `scene` 配置；首页场景化入口；场景→测评/计划联动                                                                                                | 多为配置+路由，工作量小            |
| **健康档案（页面）**                 | B 用户（扩展）        | 即 `user_profile` 的可视化页，复用 B 组                                                                                                                  | —                       |
| **运营可视化大屏**                  | J 管理（扩展）        | 运营视角读 `operation_log` + 统计聚合；新增大屏接口 `GET /admin/dashboard/ops`                                                                                 | 依赖统计聚合，非实时              |

**角色扩展**：原型出现 运营（运营可视化大屏）、医师/专家（疑似营养师资质子类）。建议：①**运营**作为独立角色加入 RBAC（admin 下的运营视角）；②**医师/专家**先按"营养师资质子类"处理，不明确建独立角色，待产品确认（CHK-08）。

---

## 4. 设计评审 3 项 Blocker 的现状与必改点

| Blocker       | 现状                                              | 必改点（附到哪个改造项）                                                                                                                       |
| ------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **B1 SSE 鉴权** | 流式接口**完全不存在**；`StreamingResponse` 仅被 import 未使用 | 1) 新建 `POST /chat/stream-ticket` 发 60s 短票；2) SSE 用 `?ticket=` 或短票；3) G 组与报告流都走这套（MOD-06 / 增 报告流 / 增 stream-ticket）                 |
| **B2 降级响应码**  | 无 `code/request_id`，`50002` 未定义，无全局异常           | 1) 落地 B0-1/B0-2 统一响应+错误码；2) 报告降级改 `code=0`+`data.state=basic`+`degraded=true`（对应 `assessment_report.state` 枚举）；3) 前端按 `code=0` 判成功 |
| **B3 报告流式通道** | `GET /assessments/{id}/report/stream` **不存在**   | 新建 SSE 接口，事件类型与 `/chat/query` 一致（start/delta/reference/done/error）                                                                 |

> 这 3 项必须在"提测准入"（测试计划 §5.1 G1）前关闭，否则冒烟用例 SM-13/SM-22 等直接 fail。

---

## 5. 建议执行顺序（以原型为范围基线，重排）

| 批次                            | 内容                                                                       | 改造项                                                |
| ----------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------- |
| **第 0 批（基线，约 2 天）**           | 统一响应+错误码+全局异常、鉴权基线（含 运营 角色）、向量库选型、释放 `/rag` 前缀                           | B0-1~B0-4、CHK-08                                   |
| **第 0.5 批（技术验证，先于第二批）**       | **S4 舌象识别 Spike**、**S5 数字人/TTS Spike**；达标才进第二批对应模块，不达标则原型相关功能降级/延期       | 新增 S4/S5                                           |
| **第一批（✅ 已完成 2026-09-10）**     | 用户体系与权限(A/B)、知识库 K（含食材库语料）、数据模型与建表、智能查询 G、个人数据分析看板 I、智能体中心骨架、五大场景配置、健康档案 | MOD-01~11、增 A/B/K/库表、§3.5 智能体中心/场景/档案、解 CHK-04     |
| **第二批（✅ 已全部落地，见 §7.3 / §7.4）** | 测评 E（含体质/舌象类型）、报告 F、营养师 C、模板 D、咨询 H、管理 J（含运营大屏）、食养计划 F2、智能荐菜、智能体中心 N             | 增 C/D/E/F/H/J/L/M/N/O + §3.5 食养/荐菜/舌象、解 B1/B2/B3、CHK-05/06 |
| **第三批（交互增强）**                 | 数字人助手（小智）前端渲染 + TTS 流（依赖 S5）                                             | §3.5 数字人                                           |
| **收尾**                        | 三项原 Spike 复测(S1/S2/S3) + S4/S5 收口、注销清理任务、越权 404、游客限流                     | CHK-07、M1~M7                                       |

---

## 6. 需你拍板的 4 个问题（已于 2026-09-10 全部确认）

| # | 问题                       | **你的决策**                          | 落地影响                                                                  |
| - | ------------------------ | --------------------------------- | --------------------------------------------------------------------- |
| 1 | NRS2002 旧代码怎么处理          | **整体搬 `legacy_nrs2002/`**（不删，可回溯） | 已执行：7 个文件 + `app/test/` + 2 个根资料已迁移，`main.py` 已摘挂载（见 §3.1）            |
| 2 | 路由风格                     | **函数式 `@router.post`**（设计要求）      | 新建 A/C/D/E/F/G/H/I/J/K 路由统一用函数式 + `Depends`，`base_router.py` 类注册式后续清理 |
| 3 | text2sql/text2echarts 归谁 | **保留为独立通用分析智能体**                  | I 组与通用分析智能体并行存在；`vanna_engine.py`/`training_data.json` 保留             |
| 4 | 向量库选型                    | **Milvus**                        | G 组与知识库 K 统一用 Milvus；根目录 `chroma.sqlite3`/`app/chromadb/` 后续清理        |

## 7. 已锁定基线决策（开工依据）

> 4 项决策已确认，原 §2「四件必须先定的架构基线」中 B0-4 的"向量库选型 / 释放 `/rag` 前缀"已定：  
> **Milvus + 函数式路由 + text2sql 独立 + 旧码入 legacy**。据此更新执行顺序：

- **B0-4 闭合**：向量库 = Milvus；`/api/v1/rag` 前缀释放，G 组智能查询改用设计约定的 `/api/v1/chat`（旧 NRS2002 `/rag` 已随 `chatbot_query_router` 迁入 legacy）。
- **路由规范**：所有新增路由一律函数式（`@router.post("/x", summary="中文", response_model=...)`），`tags` 用中文分组，便于 Swagger 全中文。
- **保留能力**：`text2sql_router` / `text2echarts_router` 作为独立通用分析智能体保留，待第二批与"个人营养看板"明确边界（CHK-04 已决：保留独立）。
- **旧码处置**：NRS2002 专属代码已全部入 `legacy_nrs2002/`，待新功能跑通、评审通过后择期删除，不阻塞当前开发。

**下一步**：按 §5 顺序，先执行第 0 批（B0-1~B0-3 统一响应/错误码/鉴权基线），随即第一批（用户体系/知识库/建表/智能查询/看板骨架）。

## 7.1 第 0 批基线代码执行记录（2026-09-10 已完成）

> 已由开发直接落地，非仅规划。验证方式：用项目运行环境 `ai_py311` 做导入与令牌往返冒烟测试通过；`main` 整体导入未跑通仅因当前环境 MinIO 未启动（`minio_client.py` 模块级连库），属既有环境问题，非本次改动引入。

| 基线项 | 落地文件 | 说明 |
| --- | --- | --- |
| B0-1 统一响应 | `app/schemas/common.py` 重写 `ApiResponse`；`app/core/responses.py` 新增 `success()/error()` | 结构 `{code,message,data,request_id}`；保留 `status=` 兼容旧调用（200→code 0） |
| B0-2 错误码 | `app/core/errors.py` 新增 `ErrCode` 分段 1xxxx–9xxxx + `BizError`；`main.py` 加 4 个全局异常处理器 | HTTP 层统一返回 200 + `code`；`REPORT_GEN_FAILED=50002` 仅用于真异常，降级走 `code=0+state=basic` |
| B0-3 鉴权 | `app/utils/security.py` 新增 access(2h)/refresh(7d)/guest(30min) 令牌；`app/core/deps.py` 新增 `get_current_user`/`get_optional_user`/`require_role`；登录改返回双令牌 | 角色含 `user/nutritionist/operator/admin`；`operator` 仍待 CHK-08 正式确认 |
| B0-4 向量库 | 已锁定 Milvus；`/rag` 前缀释放（旧 NRS 已迁 legacy） | 无需改代码，状态闭合 |
| 探针 | `main.py` 新增 `GET /api/v1/ready`（MySQL/Milvus 就绪检查）+ `request_id` 中间件 | 供容器探针与运营大屏调用 |
| 迁移断点修复 | `app/utils/db.py` 的 `init_tables` 移除已迁走的 `health_assessment` 导入 | 否则服务启动必报 ImportError |
| 旧响应清理 | `text2sql_router.py`×2、`file_handler.py`×1 的 `status=` 改为 `code=` | 与统一响应对齐 |

**遗留（非阻塞）**：`minio_client.py` 模块级建连导致无 MinIO 时 `import main` 失败——既有设计，不在第 0 批范围；建议后续改为懒加载（M 类待办，可记入收尾批）。

---


## 7.2 第一批业务代码执行记录（2026-09-10 已完成）

> 第一批不依赖任何未闭环黄色项（CHK-05/06/08、Spike S4/S5），已直接落地并通过端到端冒烟（TestClient，ai_py311）。验证 8/8 全绿：注册/登录/刷新/游客/stream-ticket/SSE(正确票)/SSE(拒答)/看板骨架；SSE 实测可直连 Milvus+Ollama 流式返回（50 事件，start→reference→delta×N→done）。

| 组 | 落地文件 | 说明 |
| --- | --- | --- |
| A 认证 | `app/routers/auth_router.py`（新）：`/register` `/login` `/token/refresh` `/logout` `/guest` `/sms/send`(占位) `/stream-ticket` | 双令牌 + 60s SSE 短票（解 B1）；登录失败走 `code=10006` |
| B 用户 | `app/routers/user_router.py`（新）：`/me` GET、`/me/profile` PUT、`/me/password` PUT、`/me/phone/verify`(占位)、`DELETE /me`(软删+15日清理任务)、`/me/data-export`(占位) | 依赖 `UserDAO.get_by_user_id` |
| K 知识库 | `app/routers/knowledge_router.py`（新）：`/docs` POST 上传+向量化、`/docs/{id}/embed`、`GET /docs` 列表、`DELETE /docs/{id}`，全 `require_role(admin)` | 复用 `KnowledgeFileService` |
| G 智能查询 | `app/routers/chat_router.py`（新）`POST /chat/query`(SSE) + `GET /chat/query/stream?ticket=`(EventSource)；`app/services/chat_query_service.py`（新，懒加载 RAG + 拒答优先 + 优雅降级） | 设计评审 B1/B2 闭环：短票鉴权 + `code=0` 降级态 |
| I 看板 | `app/routers/dashboard_router.py`（新）：`/personal/trend` `/personal/dimensions` `/personal/insight`，无数据返回空结构+note | 数据来源 E/F（第二批） |
| 数据模型 | `app/models/mysql/platform.py`（新，17 表）：UserProfile/DietitianProfile/DietitianCustomer(`active_key`生成列)/AssessmentTemplate/AssessmentRecord/AssessmentReport/ReportAnnotation/ChatSession/ChatMessage/Consultation/ConsultationMessage/OperationLog/SystemConfig/TongueImage/DietPlan/DietPlanRecipe/DataDeletionTask | `Computed` 生成列 + ENUM + JSON + VARBINARY 密文均落地；`init_tables()` 已建全部 17 表 |
| 扩展列 | `app/models/mysql/user.py`（扩 sys_user 8 列+3 唯一+2 索引）、`app/models/mysql/knowledge_file.py`（扩 6 列+2 索引） | `migrations/001_extend_legacy_tables.sql` 已写 ALTER（create_all 不 ALTER 已有表） |
| Schemas | `app/schemas/platform.py`（新）：注册/登录/令牌/档案/文档/查询/看板请求响应模型 | 与统一响应对齐 |
| 合规 | `app/core/refusal.py`（新）：`check_refusal` 命中即不调模型（用药/诊断/孕婴/报告解读） | 设计合规 D4 |

**本轮修复的缺陷**：`app/core/errors.py` 新增 `AUTH_FAILED = 10006`（原 `auth_router.py`/`user_router.py` 引用但不存在，登录/改密异常路径会抛 `AttributeError`→500，已修复并通过冒烟）。

**遗留（非阻塞）**：
- `user_router.py` 软删 `DELETE /me` 写入 `data_deletion_task` 后由收尾批 cron 执行 15 日清理（MySQL+向量库+MinIO）；当前仅建任务。
- 短信 `/auth/sms/send` 为占位（无网关），验证码登录待服务商接入（第二批）。
- `file_handler.py` 顶部 `MinioClient()` 已改懒加载（修复了 `import main` 在无 MinIO 时失败的旧问题）。

---

## 7.3 第二批（D 模板 / 评分引擎 / E 测评 / F 报告）执行记录（2026-09-10 已完成）

> 落地依据：CHK-05 原文建议「权重未定时评分引擎先按 加权和公式 + 阈值表配置 写，数值后填」。
> 因此 **C1 数值未到位不构成阻塞**，D/E/F 已整体落地并通过端到端验证。

| 模块 | 落地文件 | 说明 |
| --- | --- | --- |
| 评分引擎 | `app/services/scoring_engine.py`（新） | 纯规则算分（红线：不让 LLM 算分）。权重三层回退：system_config → 模板自带 weight → 等权；阈值百分制三档；输出 score/level/dimension_scores/suggestions |
| D/E/F DAO | `app/dao/assessment_dao.py`（新） | Template/Record/Report/Annotation 四个 DAO + `session_scope()` 跨表事务 |
| E 测评服务 | `app/services/assessment_service.py`（新） | 发起（锁模板版本 + 画像快照）/ 草稿 / 提交（幂等 + 算分）/ 作废 / 列表 / 游客认领 / 重生成 |
| F 报告服务 | `app/services/report_service.py`（新） | LLM 只写分析文本；重试 3 次（2/4/8s）→ 降级 `state=basic`；引用复用 G 组检索 |
| D 路由 | `app/routers/template_router.py`（新） | 创建/列表/我的/详情/版本/编辑(新版本)/提交审核/审核/下线 |
| E 路由 | `app/routers/assessment_router.py`（新） | 含 `GET /assessments/{id}/report/stream`（解设计评审 B3） |
| F 路由 | `app/routers/report_router.py`（新） | 报告详情 / 批注 / PDF 占位 |
| 错误码 | `app/core/errors.py` | 新增 `ASSESSMENT_CONFLICT=50004`、`REPORT_NOT_FOUND=50005` |
| 鉴权 | `app/core/deps.py` | 新增 `resolve_sse_user(request)`（短票/Bearer 通用，供报告流复用） |
| Schema | `app/schemas/platform.py` | 追加 D/E/F 请求模型 |
| 挂载 | `main.py` | 三个 router 挂到 `/api/v1` |

**C1 解耦机制（关键，顾问给数后只改配置不改代码）**：权重/阈值/版本全部读 `system_config`，回退链为 config → 模板自带 → 等权：

```sql
-- 营养师顾问产出数值后执行即可，无需改代码、无需重启发布流程
INSERT INTO system_config (config_key, config_value, description) VALUES
  ('scoring.weights',    '{"default":{"basic":0.4,"diet":0.35,"risk":0.25}}', '维度权重（可按模板编码覆盖）'),
  ('scoring.thresholds', '[{"min":80,"level":"excellent","name":"优秀"},{"min":60,"level":"good","name":"良好"},{"min":0,"level":"needs_improvement","name":"待改善"}]', '等级阈值'),
  ('scoring.version',    'v1.0', '评分版本，入库 assessment_report 便于追溯')
ON DUPLICATE KEY UPDATE config_value=VALUES(config_value);
```

返回值带 `config_source{weights,thresholds}`，标明当前用的是配置还是兜底值，便于 C1 闭环后核对。

**本轮修复的既有缺陷**：
- `auth_router.py` 引用了不存在的 `ErrCode.USER_CONFLICT` → 注册冲突路径会 AttributeError→500，已改为 `USER_EXISTS(20002)`（与第一批修的 `AUTH_FAILED` 同类问题）。
- 幂等键：`assessment_record.uk_idempotency` 是全局唯一，同键被其他记录占用时写入会撞 1062→500。提交前改为**先全局查键**：命中已完成记录则返回首次结果，被其他记录占用则提示更换键。

**验证结果（TestClient 端到端，30/30 全绿）**：
模板创建→提交审核→审核通过→编辑生成 v2→版本列表=2→已发布列表可见；发起测评锁定版本=1→草稿 2/2→提交算分 75.0（basic100×0.5 + diet50×0.5）→等级 good→维度分结构化→幂等重复提交返回首次报告→报告详情 state=generating→SSE 流（start/delta/done 齐全）→终态 complete 且分析文本入库→越权他人报告返回 90002→不存在模板 40001→普通用户建模板 10005。测试数据已清理。

**遗留（第二批未开工 / 待办）**：
- **报告生成时延：S1 Spike 已实测（2026-09-10），判定「未通过（部分达标）」** —— 详见 `AI智参平台_Spike_S1_报告时延实测报告.md`：
  - ✅ 规则算分 P95=21ms、端到端提交（评分骨架）P95=**75ms**（标准 ≤2s）
  - ❌ 首字节 TTFB 热态 **14.2s**（标准 ≤1.5s）、完整生成 **23.5s**（标准 ≤10s），环境为本机 CPU + qwen3:0.6b
  - **本轮修复（49.4s → 23.5s，降 52%）**：① 报告流由"攒完再吐"改**真流式**（原实现先 await 全部文本再分块 yield，TTFB≈总时长，前端白屏 49s）；② `OllamaLLM`→`ChatOllama`，让 `<arg_key:6124c78e>` 生效（qwen3 thinking token 对 completion 接口不受控）；③ 参考片段 4×300→3×120 字、正文限 400 字；④ 检索改 `asyncio.to_thread` 不阻塞事件循环；⑤ 生成中断时保留已生成片段（原会清空）降级 basic
  - **测量口径坑**：`TestClient`(httpx) 会缓冲流式响应，导致端到端 TTFB 读数失真（显示为≈总时长）；服务端埋点确认流式正常。测流式时延要打服务端埋点，别只看客户端
  - **待办**：回调 A1 指标（建议 ≤0.5s 出评分 / ≤3s 首字节 / ≤30s 流式完成）+ 生产 GPU 环境复测
- ~~营养师 C / 咨询 H / 管理 J（含运营大屏）/ 食养 F2 / 智能荐菜 未开工~~ → **已于 §7.4 落地**。
- 游客认领只处理 `assessment_record`；`chat_session`（含 guest_token 字段）与咨询未纳入认领，当前 `claimed_consultations` 恒为 0。
- 草稿乐观锁：用 `last_saved_at` 条件更新实现（未新增 version 列），前端需传 `If-Match` 才启用并发保护，默认不校验。
- `GET /reports/{id}/pdf` 为占位接口（二期）；模板未提供删除接口（只可下线）。
- 舌象 E（S4）/ 数字人（S5）仍须 Spike 后才能排期。

---

## 7.4 第二批续（按原型图补齐 C/H/J/P/L/M/N/O 七组）执行记录（2026-09-10 已完成）

> 落地依据：`prototype/index.html` 为**功能范围权威基线**（§8 原型功能地图）。本次按原型把后端剩余 7 组接口补齐，编码以原型页面 + 设计文档为准。

| 组 | 模块 | 落地文件（均为新增） | 要点 |
| --- | --- | --- | --- |
| C | 营养师 | `dao/dietitian_dao.py`、`services/dietitian_service.py`、`routers/nutritionist_router.py` | 资质申请/管理端审核/公开搜索/邀请码生成/用户凭码绑定/客户列表（支持筛待改善）/客户报告查看 |
| H | 咨询 | `dao/consultation_dao.py`、`services/consultation_service.py`、`routers/consultation_router.py` | 发起咨询（已绑定营养师则自动指派，否则 pending 转人工）/消息列表/回复/结束/满意度评价/越权拦截 |
| J | 管理 | `dao/admin_dao.py`、`services/admin_service.py`、`routers/admin_router.py` | 用户列表与禁用/资质审核/`system_config` 读写/操作日志（敏感配置变更自动留痕 `config_change`）/运营概览 |
| P | 运营大屏 | 同上（`admin_router` 内） | `GET /admin/dashboard/ops`：等级分布 + 近 7 日测评趋势（长度恒为 7）+ 关键计数 |
| L | 食养计划 | `dao/plan_dao.py`、`services/plan_service.py`、`routers/plan_router.py` | **食谱骨架由规则库生成**（确定性、可复现），LLM 只写 ≤250 字「方案总述」；失败降级 `state=basic` 但食谱照常返回；忌口食材过滤 |
| M | 智能荐菜 | 同上 | RAG 检索食材/菜谱语料 + 规则过滤忌口（不让 LLM 自由发挥）；食材库检索（依赖 K 组语料） |
| N | 智能体中心 | `routers/agent_router.py`、`routers/recommend_router.py` | 智能体目录/详情 + 五大场景，均**配置驱动**（`agent.catalog` / `scene.config`，缺省用内置定义） |
| O | 舌象 | 同上（`plan_service` 内） | 图片上传对象存储 + 状态管理（`status=uploaded`）；**AI 辨识留桩待 S4 Spike**，返回 `recognizable=false` 并注明 |

**红线遵守**：食谱配餐与体质判定走规则库；LLM 仅生成自然语言总述/分析文本；所有 LLM 调用失败均降级而非报错。

**本轮修复的缺陷（真实 bug，非脚本问题）**：
- `plan_service.create_plan` / `update` 在 `with session_scope()` **内部**调用 `detail()`，而 `detail()` 会**新开 session** 读库——此时事务尚未提交，读到的是旧状态 `state=generating`。已把 `return` 移到 `with` 之后（先提交再读）。**通用坑：跨 session 的读必须放在写事务提交之后。**
- 排查确认 `admin_service.stats`、`consultation/dietitian` 的 `_brief` 均为**同 session** 读取，不受影响。

**验证结果（TestClient 端到端，36/36 全绿）**：资质提交→管理员待审可见→审核通过→搜索/公开信息→邀请码→用户绑定→客户列表→客户报告；发起咨询→自动指派→消息→回复→结束→评价→无关用户 90002；用户列表→普通用户禁入 10005→写配置→读回→变更留痕→运营概览→大屏聚合；生成食养方案（2 天 6 餐）→忌口过滤→状态 `complete`→列表→详情；荐菜→食材检索；智能体目录/详情→五大场景；舌象上传→详情。测试数据已清理。

**遗留（非阻塞）**：
- 荐菜/食材检索依赖 K 组已灌入的食材语料，未灌库时返回空 + note 提示。
- 舌象 AI 辨识（S4）、数字人（S5）仍待 Spike。
- 个性化总述属 LLM 生成，受 S1 时延约束（见 §7.3 遗留）。

---

## 7.5 编码收口 + 单元/集成测试（2026-09-10 已完成）

> 用户指定的三步：① 后端编码实现 ② 单元测试（边界值） ③ 集成测试（连 DB/Redis）。
> 完整报告见 `AI智参平台_测试_02_单元与集成测试报告.md`。**结果：112 通过 / 2 跳过 / 0 失败，跑完数据库零残留。**

| 项 | 落地 |
| --- | --- |
| 规则单一事实源 | `app/core/rules.py`（新）：手机号/密码/验证码/身高/体重/年龄/BMI/答案文本/必答项/权重/分页纯函数 + 时效常量 |
| Redis 可选依赖 | `app/utils/redis_client.py`（新）：懒加载 + 5s 熔断 + 优雅降级；`/ready` 列为 `optional`，不参与就绪判定 |
| 测试脚手架 | `pytest.ini`、`tests/conftest.py`、`tests/unit/`（89 条）、`tests/integration/`（25 条） |
| 校验口径对齐 | 密码 6→**8–64**；身高 30–250→**50–250**；体重 2–400→**2–300**；手机号 11 位合法号段；分页归一化（page<1→1、size≤100） |
| 幂等 30s 窗口 | `assessment_service.submit` 由「永久幂等」改为 30s 窗口（以 `submitted_at` 为基准），超窗口按新提交处理 |

**本轮修复的 4 个真实缺陷**（均由测试暴露，非测试脚本问题）：
1. **参数校验错误会 500**：`RequestValidationError.errors()` 的 `ctx` 携带 `field_validator` 抛出的原始 `ValueError` 对象，序列化时 `TypeError: not JSON serializable` → `main.py` 新增 `_sanitize_validation_errors()` 降级为字符串。
2. **`ErrCode.DB_ERROR` 从未定义**：被 4 处引用（user_router 3 / knowledge_router 1）→ 走到即 AttributeError 500 → 补 `DB_ERROR = 90003`，并全项目扫描确认 20 个引用均有定义。
3. **并发提交产生重复报告（BV-29 不通过）**：10 并发各自判定「报告不存在」→ 并发 INSERT 撞 `assessment_report.uk_record` → 1062/500。修复：新增 `RecordDAO.get_for_update()` 行锁串行化 + INSERT 撞键兜底复用已有报告。
4. **`scoring_engine` 类级可变状态**：`_last_weight_source` 在并发下串味，导致 `config_source` 错报 → 改为 `get_weights()` 返回 `(weights, source)`。

**需同步处置的文档冲突**：设计_01 §1.3 与用例集仍用旧错误码编号（`20001 未登录` 等），实现为第 0 批新编号（`10001 未登录`）；已确认**以实现为准**，建议同步更新文档。SM-03「注册返回令牌」与实现（注册只返回用户信息、令牌走登录）亦不一致，未改。

**未覆盖（本轮已补 3 条）**：BV-26 注销告警（需定时任务）/ BV-28 文件上传（依赖 MinIO）/ BV-09/10 题目数与单页题量。BV-21/24/25/27 已于 §7.6 补齐。

---

## 7.6 时效类规则实现 + 错误码文档同步（2026-09-10 已完成）

> 用户指示：① 同步设计_01 §1.3 与用例集的错误码；② 实现游客配额 / 咨询超时 / 认领期限等时效业务规则。
> **结果：新增 5 条集成用例全绿，全量 117 通过 / 2 跳过 / 0 失败，跑完数据库零残留。**

### 7.6.1 错误码同步（以实现为准，`app/core/errors.py` 为唯一事实源）

| 文档 | 旧编号（已废止） | 新编号（现网） |
| --- | --- | --- |
| 设计_01 §1.3 表 | 1xxxx 通用 / 2xxxx 认证 / 3xxxx 用户 / 5xxxx 报告 | 重排为 1xxxx 认证授权 / 2xxxx 用户 / 3xxxx 营养师 / 4xxxx 模板 / 5xxxx 测评报告 / 6xxxx 智能体 / 8xxxx 管理 / 9xxxx 系统 |
| 用例集 SM-06 未登录 | `20001` | `10001` |
| 用例集 IT-A-04 已注册 | `30001` | `20002` |
| 用例集 IT-A-08 登出旧 token | `20001/20002` | `10001/10003` |
| 用例集 IT-A-09 越权 | HTTP 403 + `20003` | `10005`（HTTP 200 + code） |
| 用例集 BV-01 手机号非法 | `10001` | `90001` |
| 用例集 BV-22 短票过期 | `20002` | `10002` |

同时修正设计_01 §1.3 的过时说明：报告「降级」不再是 `50002`，而是 `code=0 + data.state="basic"`（评审 B2）。

### 7.6.2 时效类规则实现

| 用例 | 规则 | 落地位置 |
| --- | --- | --- |
| BV-21 游客测评配额 | 游客已完成测评数 ≥ 1 时拒绝发起并引导注册（`10004`） | `RecordDAO.count_guest_completed()` + `AssessmentService.start_assessment` |
| BV-21 游客查询配额 | 游客会话数 ≥ 5 时拒绝查询并引导注册（`60002`） | `chat_router._guest_hash()` / `_guest_quota_used()` + `_stream_response` |
| BV-24 草稿有效期 | 超过 30 天的草稿访问时自动作废并拒绝（`90001`） | `RecordDAO.void()` + `save_draft` / `submit`（**先 `s.commit()` 再抛错**） |
| BV-25 咨询超时 | 读取时把超 30 分钟未接入的 pending 咨询懒置为 `timeout`（无需定时任务） | `ConsultationDAO.expire_stale()` + `ConsultationService._sweep_timeout()`（list_mine / pending_queue / messages / send） |
| BV-27 认领期限 | 认领窗口由硬编码 `7` 改为常量 `rules.CLAIM_WINDOW_DAYS` | `AssessmentService.claim_guest` |

### 7.6.3 本轮修复的真实缺陷

1. **事务回滚吞掉作废**：`session_scope()` 内 `RecordDAO.void(record_id, session=s)` 后直接 `raise`，会被上下文的 `except` 回滚 → 草稿状态仍是 `in_progress`。修复：作废后显式 `s.commit()` 再抛错。
2. **`consultation_dao` 缺 `timedelta` 导入**：`expire_stale` 用到但未导入 → NameError。
3. **断网窗口导致部分编辑静默丢失**：本轮网络中断期间，多份 `Edit` 报成功但未落盘（`_stream_response` 签名、配额检查等）。**教训：关键编辑后必须回读/grep 核验，不能只信工具回执。**

---

## 7.7 基础设施落地：Redis 与 MinIO 真连（2026-09-10 已完成）

> 用户要求：Redis「不能跳过，必须连，也必须测」；MinIO「不能做懒加载，必须启动」。
> **结果：全量 124 通过 / 0 跳过 / 0 失败；MinIO 桶内零残留。**

### 7.7.1 根因（不是"没装"）

| 现象 | 真相 |
| --- | --- |
| Redis「能握手但不回应」 | Docker 端口转发器（`com.docker.backend` PID 14252）在容器停止后仍占 `0.0.0.0:6379`，TCP 握手后立即 FIN；且 `.env` 的 `REDIS_URL` 指向不可达的 `10.10.10.243:6379` |
| MinIO「未启动」 | `9100` 上其实是 **Milvus 内部对象存储**（`milvus-minio`），不是应用专用 MinIO；混用会与 Milvus 段数据同生共死 |

### 7.7.2 专属容器（compose 纳管）

| 服务 | 容器 | 宿主端口 | 说明 |
| --- | --- | --- | --- |
| Redis | `ai-health-redis` | `6380 → 6379` | `--appendonly yes`；避开被 talent 项目占用的 6379 |
| MinIO | `ai-health-minio` | `9000 → 9000` / `9001 → 9001` | 桶 `vanna-training-data` 启动期自动创建 |

起停：`docker compose -f docker-compose.infra.yml up -d` / `down`。

### 7.7.3 代码与配置改动

| 改动 | 文件 |
| --- | --- |
| **MinIO 去懒加载**：导入期建立单例 + 建桶，失败即抛错（fail-fast）；新增 `build_minio_client()` / `get_minio()` / `health()` | `app/utils/minio_client.py` |
| 上传工具改用启动期单例（移除 `_minio_client = None` 延迟初始化） | `app/utils/file_handler.py` |
| 舌图上传改用 `get_minio()` 单例 | `app/services/plan_service.py` |
| `/ready` 将 Redis / MinIO 列为**必需项**，移除 `optional` 字段 | `main.py` |
| 配置修正：`REDIS_PORT=6380`、`REDIS_URL=redis://127.0.0.1:6380/10`、`MINIO_ENDPOINT=127.0.0.1:9000` | `.env` |
| 环境探测新增 MinIO（真实 `bucket_exists`，因为端口连通 ≠ 可用） | `tests/integration/env_probe.py`、`conftest.py`（新增 `minio_required`） |
| 新增存储真连用例 5 条 | `tests/integration/test_storage.py` |
| 一键起停定义 | `docker-compose.infra.yml`（新增） |
| 密钥不入库 + 脱敏模板 | `.gitignore`（新增）、`.env.example`（新增） |

### 7.7.4 验证

- `/ready` → `checks.redis=ok`、`checks.minio=ok`、`ready=True`。
- Redis 封装层 `PING/SET/GET/INCR/JSON/DELETE` 全部真连通过。
- MinIO：客户端上传 → 预签名 URL → 下载 → 删除往返；舌象上传端点对象真实落桶且字节一致。
- 原先 2 条 Redis SKIP 用例**转为真连通过**；全量 **124 passed / 0 skipped**，MySQL 与 MinIO 零残留。

---

## 7.8 原型基线切换 + 第 4 批（P0）与部分 P1 落地（2026-09-10 已完成）

> 用户决策：① 以 `原型图.html`（收入为 `prototype/index_v2.html`）为**唯一基线**，原 `prototype/index.html` 作废；② OCR / 旅行一期做"**留桩+交互**"（自助购买已于 2026-10-15 整体移除）；③ 数字人 / 舌象辨识留桩待 S5/S4。
> **结果：全量 136 通过 / 0 跳过 / 0 失败；MySQL 与 MinIO 零残留。**

### 7.8.1 基线切换

| 动作 | 落点 |
| --- | --- |
| 新原型收入仓库 | `prototype/index_v2.html`（10 屏 / 7 智能体） |
| 旧原型作废 | `prototype/index.html`（见 `prototype/README.md`） |
| 差异清单 | `AI智参平台_后端与原型图一致性核对报告.md`（A 类 14 缺 / B 类 11 形态不同） |

### 7.8.2 第 4 批 P0：打卡 + 通知（已完成）

| 项 | 落地 |
| --- | --- |
| 新表 | `checkin_record`（唯一键 user+date+type → 幂等）、`notification` |
| 打卡接口 | `POST /checkins`（幂等，拒未来日期）、`GET /checkins`、`GET /checkins/stats`（**执行率分母固定 7**，与原型 5/7=71% 一致；含连续天数） |
| 通知接口 | `GET /notifications`（含 `unread` 供顶栏红点）、`POST /notifications/{id}/read`（幂等）、`POST /notifications/read-all` |
| 越权防护 | 标记他人消息统一 `90002`（评审 M3 防枚举） |
| 内部推送 | `notification_service.push()` 供其它模块调用（最佳努力，失败不阻断主流程） |

### 7.8.3 P1 已落地部分：口径对齐 + 聚合接口

| 项 | 原型 | 落地 |
| --- | --- | --- |
| B1 五大场景 | 健康饮食管家/慢病管理专家/社交宴请助手/妇幼健康守护/旅行美食智荐 | `plan_service.DEFAULT_SCENES` 按原型重写（含 icon 语义键） |
| B2 智能体目录 | 原型第 7 张卡 | **「1 个主智能体 + 6 个子智能体」**：主智能体 `orchestrator`（调度中枢，不直接产出内容）+ `assessment`/`diet`/`dish`/`steward`/`dietitian`/`data` 六个子智能体（`role=sub`、`parent=orchestrator`）。新增 `POST /agents/dispatch`（规则引擎调度方案）。<br>*2026-10-15 修订：原型第 7 张卡由「自助购买智能体」改为主智能体，自助购买模块整体移除；原型、文档、代码三边对齐。* |
| B10 首页 KPI | 健康综合指数/本周打卡/待关注风险/食养计划周次 | 新增 `GET /dashboard/personal/overview` |
| B5 风险提醒 | 结构化高/中/低关注列表 | 新增 `GET /dashboard/personal/risks`（维度分档：<60 高、60-79 中、≥80 良好） |
| 个人数据分析 | AI 解读 | `GET /dashboard/personal/insight` 由占位改为返回最近报告的 `analysis_text` |
| B6 健康上下文 | 体质/重点风险/忌口/AI 建议 | 新增 `GET /users/me/health-context` |
| B9 工作台 KPI | 管辖客户/高风险待跟进/今日咨询/满意度 | 新增 `GET /nutritionists/me/overview` |
| B3 客户表格 | 客户/标签/最新测评/风险/执行率/最近咨询 | `customers()` 补 `tags`、`execution_rate`、`last_consult_at` |

### 7.8.4 尚未落地（后续批次）

| 批次 | 内容 |
| --- | --- |
| 第 5 批 P1（余） | 食养周/月计划切换、采购清单、导出客户报表、Text2SQL 行级权限+图表+智能报告+导出、报告 PDF 真实现 |
| 第 6 批 P2 | 社交宴请助手、旅行美食智荐、OCR 体检报告解析（**留桩+交互**）、大屏拖拽配置与截图、智能体运行监控<br>*2026-10-15 修订：自助购买模块已整体移除。* |
| 待外部输入 | 舌象 AI 辨识（S4）、数字人小智（S5）、场景判级权重（C1） |

---

## 7.9 第 5 批（P1 余）+ 第 6 批（P2）落地（2026-09-10 已完成）

> **结果：全量 148 通过 / 0 跳过 / 0 失败；MySQL 与 MinIO 零残留。** 新增 9 张表、约 30 个接口、12 条用例。

### 7.9.1 第 5 批 P1 余

| 项 | 落地 |
| --- | --- |
| 食养周/月计划 | `diet_plan` 新增 `period`（迁移 `migrations/002_add_plan_period.sql`）；`POST /diet-plans {period: week\|month}`，周默认 7 天 / 月默认 30 天（上限 31） |
| 采购清单 | `GET /diet-plans/{id}/shopping-list`：按食材聚合去重并剔除忌口（原型「采购清单 18 项」） |
| 导出客户报表 | `GET /nutritionists/me/customers/export?fmt=csv\|xlsx`：真产出文件 → MinIO → 签名 URL |
| Text2SQL 改造 | 新增 `app/services/analytics_service.py`：**自然语言 → 受控模板 → 角色级行权限 → 只跑安全 SELECT**；`/analytics/text2sql/{catalog,query,history,export}`；行权限：普通用户 `user_id = :uid`、营养师按管辖客户子查询、管理员 `1=1`；历史入 `text2sql_query` 便于审计 |
| 报告 PDF | `GET /reports/{id}/pdf` 由占位改**真实现**：`reportlab`（内置中文字体 STSong-Light）渲染评分/维度/建议/免责声明 → MinIO → 签名 URL |

> **红线落实**：Text2SQL **不让 LLM 生成或执行 SQL** —— 用受控模板 + 参数绑定，不匹配的提问明确拒绝（`90001`），从根上消除注入与越权。

### 7.9.2 第 6 批 P2（留桩 + 交互）

| 项 | 接口 | 留桩说明 |
| --- | --- | --- |
| 社交宴请助手 | `POST/GET/PUT /banquets`（含宾客慢病/过敏/体质、逐菜风险标注、协同编辑） | 荐菜走规则库（确定性）；无外部依赖，**功能完整** |
| 旅行美食智荐 | `POST/GET /travel-plans` | `advice.enabled=false` + 通用出行建议；目的地餐厅/POI 待接入 |
| OCR 体检报告 | `POST/GET /ocr-reports` | 已存文件 + 建任务；`recognizable=false` + `indicators=[]` + 明确 note，OCR 引擎待接入 |
| 大屏方案 | `GET/PUT /admin/dashboard/ops/config`、`POST .../snapshot` | 布局/刷新周期/主题/异常高亮可存；**截图需无头浏览器，`enabled=false`** |
| 智能体运行监控 | `GET /analytics/agents/monitor` | 7 个智能体（1 主 + 6 子）状态汇总；当前 `running=7` / `pending=0`（自助购买模块移除后已无「待接第三方」智能体） |
| 数字人小智 | `GET /digital-human/config`、`POST /session`、`POST /voice-command` | 文本对话复用 `/chat/query`（**可用**）；`asr/tts/avatar=false` 待 S5 |

> **留桩原则**：能力层**绝不假装成功** —— 未接入就返回 `enabled=false` / `recognizable=false` + 明确原因，前端可完整走通交互，二期只换实现不改契约。

### 7.9.3 本轮修复的真实缺陷

1. **会话外访问 ORM 属性（第 2 次踩同一坑）**：`shopping_list` 在 `with session_scope()` 提交后读取 `plan.avoid_foods` / `recipe.ingredients` → `DetachedInstanceError`。**规范：在 session 内一次性取成纯 Python 值，再出作用域计算。**
2. **测试清理遗漏**：Text2SQL 历史与 MinIO 产物（`reports/` `exports/` `ocr/`）未登记清理 → 跑完 12 行 + 12 个对象残留。已补 `_rm_minio()` 与逐表清理。

---

## 7.10 设计文档同步（2026-09-10 已完成）

> 目标：把《设计_01 接口设计》《设计_02 数据库设计》从"设计期口径"升到"与实现一字不差"，避免再次出现**文档与实现两张皮**（上一轮错误码就是这么来的）。

| 文档 | 版本 | 改动 |
| --- | --- | --- |
| `AI智参平台_设计_01_接口设计.md` | V1.0 → **V1.2** | ① 新增 §二 **Q–X 组**（打卡/通知/数据分析/宴请/旅行/OCR/自助购买/数字人）；② A–P 组补齐实现新增端点（`/auth/guest`、`/auth/stream-ticket`、`/users/me/health-context`、营养师 `overview`/`export`/`unbind`、`/consultations/pending`+`accept`、`dashboard/overview`+`risks`、大屏 `config`+`snapshot`、`shopping-list`、`/scenes`）；③ G 组标注会话接口一期仅落库；④ §五 覆盖检查表重写；⑤ §六 关闭 I4（PDF 已定 reportlab），新增 I5（遗留路由下线）/I6（配额引导） |
| `AI智参平台_设计_02_数据库设计.md` | V1.0 → **V1.2** | ① §2 ER 图补 11 表关系；② 新增 §3.17–3.24 **11 张表 DDL**（**从实库 `SHOW CREATE TABLE` 导出，保证与实现一字不差**）；③ `diet_plan` 补 `period` 列；④ §4 状态机补 6 张表；⑤ §7 索引补 7 行；⑥ §8 新增 D6/D7/D8 |

**校验口径**：文档所述表数（33）、接口组（A–X 共 24 组）、端点总数（**122**，2026-09-10 下线 11 条遗留后）均来自运行时实测（`main.app.routes` / `SHOW CREATE TABLE`），非手工统计。

---

## 7.11 后端体检与 P0 清零（2026-09-10 已完成）

> 触发：用户要求「先对后端做全面检查再决定是否开前端」。7 层运行时实测的完整报告见 **`AI智参平台_后端全面体检报告.md`**（含 P1/P2 待办与可复现命令）。

### 7.11.1 体检结论

| 层 | 结果 |
| --- | --- |
| 编译 / 导入 | ✅ 0 错误 |
| 接口 | 133 端点 → **下线后 122**，0 缺中文 summary / tags |
| 数据库 | 33 表；唯一键齐全；迁移已应用 |
| 自动化测试 | ✅ 148 / 148（0 失败 0 错误 0 跳过） |
| 五个依赖实连 | ✅ 全通 |
| 红线·LLM 不算分 | ✅ 合规 |
| 红线·LLM 不直连库 | ❌→✅（遗留模块已下线） |
| 鉴权覆盖 | ⚠️→✅（遗留 22 条已下线） |

### 7.11.2 P0 清零（本次实际改动）

| # | 问题 | 处置 | 落点 |
| --- | --- | --- | --- |
| P0-1 | 遗留 4 个 router 零鉴权 + LLM 生成 SQL 直接执行 | **下线**：取消 import 与挂载（源码保留 + 恢复说明） | `main.py`（原 5-8 / 86-89 行） |
| P0-2 | `demo`/`123456` 默认账号 | `config.py` 默认 `"true"`→`"false"`；`.env`/`.env.example` 显式关闭；**删除库中账号** | `app/config.py:34`、`.env`、`.env.example`、`sys_user`+`user_profile` |
| P0-3 | 遗留端点重复注册（11 组） | 随 P0-1 一并消除 | `main.py` |

**验证**：遗留前缀全部 404；重复注册 11 组 → **0**；Duplicate operationId 警告 11 → **0**；`Vanna` 不再初始化；`/ready` 四项全 ok；全量 **148 passed**；`INIT_DEMO_USER=False` 且 demo 不再重建。
**连带收益**：全量测试 **25.9s → 9.9s**。

### 7.11.3 保留待办（用户决定暂不动）

P1-1 Milvus 集合仍指向遗留 `nrs2002`（需建平台专属集合并隔离检索）｜P1-3 遗留表/视图纳管｜P1-4 存量脏数据（14 条孤儿报告 + 4 条 `smoke_u` 记录）｜P2-1 裸 except ×5｜P2-2 MinIO 桶名遗留｜P2-3 死配置 `MILVUS_URI`。

---

## 7.12 批次 4 余项 + P2 技术债清理（2026-10-16 已完成）

> 承接《全栈体检报告 2026-10-15》的 P2 清单与批次 4 未开工项。本轮**只做收口**：
> 不加新能力组、不动原型范围，把"已声明但没落地"和"删一次就完事"的债一次清完，
> 并给每条债补**可执行守卫**（见 §7.12.4）——否则债清完还会回流。

### 7.12.1 批次 4-1 邮箱注册（原"未开工"项）

原先只有账号 / 手机号两条注册与登录通道，邮箱是原型与需求里写着、代码里没有的那一条。

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 验证码通道抽象 | `app/services/verify_code_service.py`（新） | 把「生成 → 60s 限频 → 带 TTL 缓存 → 网关发送 → 校验计次 → 一次性失效」抽成与通道无关的 `VerifyCodeService`，短信 / 邮箱各自注入参数；**避免把 SMS 那段复制一遍**（抄一遍就是两套限频口径，迟早对不上） |
| 短信侧改为薄配置层 | `app/services/sms_service.py`（重写） | 只保留配置与实例化；模块级 `settings` / `sms_gateway` 名字**刻意保留**，因为既有用例在 monkeypatch 它们——改名会让 EX-01 之类的用例静默失效 |
| 邮箱网关 | `app/utils/mailer.py`（新） | 与 `sms.py` 同契约：`is_configured()` / `missing_config()` / `send_code()`；区分 `SMTPAuthenticationError`（配置错，不能答"稍后重试"）与其他发送失败 |
| 邮箱通道实例 | `app/services/email_code_service.py`（新） | 独立密钥派生标签 `email-hash-v1` / `email-aes-v1`，与手机号的 `phone-hash-v1` / `phone-aes-v1` **分开**：写错字段会在解密阶段直接暴露，而不是"看起来能跑" |
| 加密与检索 | `app/utils/security.py` | 新增 `email_hash()` / `encrypt_email()` / `decrypt_email()` / `mask_email()`，把密钥派生收敛到共享的 `_derive_keys(hmac_label, aes_label)` |
| 邮箱归一化 | `app/core/rules.py::normalize_email` + `check_email` | 先 `strip().lower()` 再哈希，否则 `Alice@Example.com` 与 `alice@example.com` 会建成两个账号 |
| 注册 / 登录 | `app/routers/auth_router.py`、`app/services/user_service.py` | 注册三通道**互斥**；登录优先级 `username > phone > email` |
| 错误码 | `app/core/errors.py` | `EMAIL_RATE_LIMITED=60005`、`EMAIL_SEND_FAILED=60006` |
| 生产守卫 | `app/config.py::validate_runtime_settings` | `ENV=production` 且 `MAIL_DEMO_MODE=true` → **拒绝启动**（与短信演示模式同一口径：演示码绝不能进生产） |
| 前端 | `frontend/index.html`、`assets/app.js`、`assets/api.js` | 登录/注册页加邮箱签页；"记住账号"改为**按内容**路由（一个输入框三种账号形态） |

用例：`tests/integration/test_email_auth.py`（21 条）。测试邮箱统一用 RFC 2606 保留域 `.invalid`——
它永不解析、永不投递，即使误配了真实 SMTP 也不会把验证码发给陌生人。

### 7.12.2 批次 4-2 AI 咨询辅助建议（原"未开工"项）

| 改动 | 位置 | 说明 |
| --- | --- | --- |
| 非流式补全 | `app/services/chat_query_service.py::complete` | 返回 `Optional[str]`，`None` 表示**模型不可用**（不是空串）——逼调用方如实降级，而不是渲染一个空白建议 |
| 新列 | `migrations/007_add_message_ai_assisted.sql`（新） | `consultation_message.ai_assisted`：既有表加列，必须手工执行（`create_all` 只建新表） |
| 建议生成 | `app/services/consultation_service.py::ai_suggestion` | 组装「客户健康档案 + 咨询对话 + 知识库片段 + 营养师补充要点」；高风险诉求**在调用模型之前**整类拒答 |
| 来源标记 | `send()` / `messages()` | `ai_assisted` **只对营养师发送生效**；客户消息即使传 `true` 也落 0（防串标污染 `ai_ratio`） |
| 权限 | `app/routers/consultation_router.py` | `require_approved_dietitian()`：仅有 role 不够，须 `audit_status='approved'` |
| 前端 | 营养师工作台「咨询接待」区 | 待接入队列 / 我的咨询 / 消息流 / AI 生成建议 / 发送 |
| **修掉的真实缺陷** | `consultation_service.has_open` | 原写 `("pending","in_progress")`——`in_progress` 是 `assessment_status` 的取值，`consultation_status` 的合法域是 `pending/active/closed/timeout`。两个枚举**撞同一个中文词"进行中"**，导致"已接入"的咨询被判成"没有未结束的咨询"，用户可重复发起 |

用例：`tests/integration/test_ai_suggestion_cases.py`（13 条）。
草稿**不落消息表**是硬约束：否则看板上会出现"客户没收到、系统却记了一条"的幽灵消息。

### 7.12.3 批次 4-3 运营指标口径（原"未开工"项）

补 `dau` / `mau` / `month_assessment` / `ai_ratio` / `template_usage` 五个指标
（定义见《设计_01》§五，实现见 `app/dao/admin_dao.py`）。三条口径选择值得单独记：

1. **活跃不只认登录**：`last_login_at` 只反映"最后一次登录"，而 access token 2 小时 + 前端自动续期
   意味着"登录一次用一下午"的人不会再写登录记录。故活跃 = 登录 ∪ 提交测评 ∪ 发起咨询 ∪ 发起对话
   的 `UNION`（自带去重）。
2. **`ai_ratio` 分母只含营养师消息**：把客户消息算进分母会把比例压扁。
3. **模板使用率分母只含"有归属"的记录**：`template_id` 为空的记录混进分母会让所有占比加起来不到 1。

前端：`frontend/index.html` + `assets/app.js::loadOps/renderOpsTemplates`、`frontend/admin.html` 概览页
（含逐模板占比条与汇总口径）。**无数据一律显示 `—`，不补零**。

用例：`tests/integration/test_ops_metrics_cases.py`（7 条）。

### 7.12.4 P2 技术债清理

| 编号 | 原问题 | 本轮处置 |
| --- | --- | --- |
| P2-A | 知识库 service/DAO 5 个**零入口死代码**（`query_knowledge` / `semantic_search` / `query_by_category` / `count_by_category` / `add_single_record`） | **删除**。删前逐项验证：无任何调用点、无 `getattr` 动态派发、无文档引用。它们不止没人用，而且引用的是**不存在的字段**（`chunk_text`）、兜底表达式是 `id != ''`，`add_single_record` 还不写 `text`——真接上也是坏的 |
| P2-1 | 裸 `except:` ×5 + `print()` ×21 | **核实为已解决**：5 处裸 `except` 全在 `legacy_nrs2002/`（按项目红线隔离保留、不删不修），`app/` 内 `print()` 实为 **0 处**（此前统计把 `fingerprint(` 的字符串误当 `print(`）。新增守卫用例防止回流 |
| P2-2 | 遗留目录 `app/adapter` / `app/designer` / `app/exceptions` + 6 个空目录 | `adapter` / `designer` 与空目录**已不存在**；`app/exceptions` **仍在被使用**（`user_service` / `main.py` / 单元测试），故**保留**——它承担的是"框架层异常 → 统一响应"的转换，与 `app/core/errors.py` 的业务错误码是两层职责，不是重复实现 |
| P2-3 | 2 个遗留视图 + 1 张遗留表 | **仅盘点，未删除**（见 §7.12.5） |
| P2-4 | 前端 96 处 inline `onclick`、无打包 | **维持现状**：前端"原生无构建、同源托管"是已锁定的架构基线（见 §2），为此引入打包链会推翻该基线，收益不抵风险。记为已知取舍 |
| P2-5 | 死配置与 `.env` 键不一致 | 删掉 7 个**全仓零消费**的键：`MILVUS_URI`（体检报告点名）与一批 Django 残留 `API_HOST` / `API_PORT` / `API_KEY` / `MEDIA_URL` / `MEDIA_ROOT` / `DATA_UPLOAD_MAX_MEMORY_SIZE`；顺带清掉重复的 `BASE_DIR` 赋值与已无用的 `Optional` 导入。`.env` 与 `.env.example` 键集合**已一致**（体验报告里"缺 `SECRET_KEY`/`CORS_ORIGINS`/`SMS_DEMO_MODE`"已不复现）。另外**接上了 `REDIS_MAX_CONNECTIONS`**：该键一直写在 `.env` 里却没人读，连接池上限实际由驱动默认值决定，`.env` 里的数字是一句空话 |

### 7.12.5 未决：遗留视图 / 表与存量残留（需拍板后才动）

盘点结果（**只读，未做任何删除**）：

| 对象 | 类型 | 行数 | 代码引用 |
| --- | --- | --- | --- |
| `high_risk_patients` | VIEW | 28 | 无（仅 `purge_test_residue.py` 明确**跳过**） |
| `latest_assessments` | VIEW | 46 | 无（同上） |
| `user_health_risk_assessment` | TABLE | 46 | 无（同上） |

三者都是 NRS2002 旧系统对象，与平台数据无关。`scripts/purge_test_residue.py` 早就把它们列入
`LEGACY_OBJECTS` 并主动跳过——**已被有意排除，不是漏网**。

另查出**存量测试残留**（全部产自 2026-10-16 当天的回归/探测，无一条早于当日）：

```
悬空用户数据  56 行 / 51 个用户（chat_session 1、operation_log 27、report_annotation 28）
孤儿子行     199 行（chat_message 74、consultation_message 41、批注 56、日志 27、会话 1）
测试残留账号   0 个
```

处置方式已具备：`scripts/purge_test_residue.py`（默认预演，`--apply` 才写库）。
**但因涉及 DROP/DELETE 且属不可逆操作，本轮未执行，留待明确确认。**

---

## 7.13 主智能体调度中枢：从"只出方案"到"真分派"（2026-10-16）

### 7.13.1 为什么要做

原型把主智能体写成"识别需求意图 → 规划执行链路 → 分派六个子智能体 → 汇总结果"。
落地时发现**只有前半截**：`POST /agents/dispatch` 按关键词出调度方案说明，
但没有任何地方去**调用**这些子智能体。更要紧的是，问答接口的 `agent` 参数
（`dietitian/assessment/data/dish`）**只被用作答案缓存的 key**，
不改变检索行为、不触发任何子智能体逻辑——也就是说"主智能体调度"实际是不存在的。

这在面试里是致命的：只要追问"意图识别用的什么模型、误判率多少、怎么评测的"就穿。
所以这一轮把它做成**真的**。

### 7.13.2 做了什么

| 层 | 内容 | 位置 |
| --- | --- | --- |
| 意图识别 | 确定性规则分类器：`critical` 显式指令词 > `strong` 领域专有词 > `weak` 通用词，过阈值入围，同分按业务链路 `order` 仲裁；输出主意图 + 置信度 + 区分度 + 命中证据 | `app/services/intent_service.py` + `app/core/rules.py::INTENT_RULES` |
| 高危前置 | 在**分类阶段**就拦，plan=[]、steps=[]，一次子智能体都不调用；**复用 `refusal.py` 同一套规则**，不另写第二套关键词 | `intent_service.classify()` |
| 规划 | 按业务链路顺序（评估→方案→菜品→提醒→答疑→统计）产出**可执行序列**，不是按分数排 | `intent_service.plan_agents()` |
| 分派执行 | 六个子智能体各有真实执行器，**同步阻塞调用一律 `asyncio.to_thread`** | `app/services/orchestrator_service.py` |
| 汇总 | 只做**结构化拼接**，不经模型二次生成；引用去重；失败如实标 `ok=false` | `orchestrator_service._aggregate()` |
| 接口 | `POST /agents/route`（只识别）/ `POST /agents/orchestrate`（完整编排） | `app/routers/agent_router.py` |
| 前端 | 智能体中心新增「编排试跑」面板，分四段展示识别/规划/分派/汇总 | `frontend/index.html` + `assets/app.js` |

### 7.13.3 一个关键设计：指标名不是意图，是基线

评测集暴露出：用户说「高血压人群的早餐怎么吃」，真正想问的是营养知识，
但因为句子里有「高血压」被判成健康评估。

问题出在——**指标名描述的是"用户是谁"，不是"用户要什么"**。
修法：把指标词从 `strong` 挪到 `INTENT_BASELINE_TERMS`，命中时
健康评估**进入执行链路**（先建基线）但**不参与主意图打分**；
只有当没有任何其他意图过阈值时才作为兜底。

这条是整个意图模块里最值得说的一句设计判断：**基线数据不该冒充用户意图。**

### 7.13.4 评测集抓出并修掉的真实缺陷

建评测集的过程本身就是产出——它在半天里抓出了 6 个真实缺陷：

| 缺陷 | 影响面 | 处置 |
| --- | --- | --- |
| 「这个药能不能停」「我需要吃点什么药」等漏网 | **合规**：用药决策类问题没被拦 | `refusal.py` 补 12 个多字组合词（不能用裸"药"字，会误伤山药/药膳/芍药） |
| 「孩子反复发烧要去查什么科」漏网 | **合规**：就医引导等于诊断入口 | `diagnosis` 补"查什么科/挂什么科/看哪个科"等 |
| 「多少」单独出现被判成数据分析 | 误判：问热量被分派去跑 Text2SQL | `INTENT_MIN_SCORE` 阈值 + 弱词权重，使单弱词不过阈 |
| 「我该吃多少降压药」被分派给 Text2SQL | **合规**：高危绕过了路由层 | 高危前置到分类阶段，复用拒答规则 |
| 「糖尿病主食怎么选」被判成问答之外的意图 | 误伤：核心场景被分错 | 指标词降级为基线信号 |
| 空输入时 `primary=""` | 调用方拿不到明确兜底 | 空输入显式设为 fallback 意图 |

### 7.13.5 评测结论（如实记录，不美化）

| 数据集 | 样本 | 准确率 | 高危召回 | 兜底率 |
| --- | --- | --- | --- | --- |
| 调参集 `DATASET` | 68 | **100.0%** | 100.0% | 17.6% |
| 留出集第一批（已污染，仅存档） | 25 | 68.0% | 87.5% | 44.0% |
| **留出集第二批（当前有效）** | 21 | **57.1%** | 83.3% | 52.4% |

**调参集 100%、留出集 57%——这个落差本身就是最有价值的结论。**
关键词表覆盖的是"我写过的说法"，不是"用户的说法"。

由此得出两条生产口径（未决 TODO）：

1. **规则不能单独作为合规闸门**：留出集高危召回 83.3%，约六分之一真实高危表述会漏。
   正确形态是规则做第一道（快、确定、可解释），后面叠一道独立机制兜底。
2. **词表要靠线上真实问句回流扩充**，而不是设计时拍脑袋。目前没有回流机制，属已知缺口。

第一批判定后改了 `refusal.py`，那一批随之失效，故又写了第二批留出集。
**纪律是留出集只跑一次、不许回头调词表去凑**——这条比数字本身更重要。

### 7.13.6 为什么不用大模型做意图分类

规则法在留出集上明显更差，那为什么不用 LLM？因为路由层要的是**确定性**而不是智能：

| 约束 | 规则分类 | LLM 分类 |
| --- | --- | --- |
| 同一问句结果一致 | ✅ 必然 | ❌ 同句可能两次不同分支 |
| 误判率可量化 | ✅ 混淆矩阵 | ❌ 每次跑都不同，"率"无从定义 |
| 命中原因可解释 | ✅ 摊开给用户看 | ❌ 给不出稳定理由 |
| 每次问答额外成本 | 0 | 一次模型调用 |

结论不是"规则更好"，而是**路由层该要的东西和生成层不一样**。
需要理解语义的地方交给模型，路由不碰。

### 7.13.7 用例

`tests/integration/test_intent_routing_cases.py`（20 条）。**它锁的是确定性与合规闸门，不是准确率**——
把调参集那个 100% 写成断言就是自己骗自己。真正不变的三件事：
同问句同结果、高危优先于一切、失败不伪造。

---

## 8. 原型功能地图（权威基线 · 编码对照表）

> ⚠️ **基线变更（2026-09-10）**：功能范围权威基线已切换为 **`prototype/index_v2.html`**（用户提供的 `原型图.html` 收入）。
> 原 `prototype/index.html` 经用户确认**系误用，已作废**（详见 `prototype/README.md`）。
> 差异与改造计划见 `AI智参平台_后端与原型图一致性核对报告.md`；下表为 **v2** 口径。

> 来源：`prototype/index_v2.html`（10 屏 / 7 大智能体）。下表逐屏/逐智能体映射到接口组与数据库表，**编码时以本表 + 原型页面为准**，原设计文档仅作补充。

| 原型页面 / 智能体               | 视角  | 接口组                 | 数据库表                                                       | 是否新增(相对原设计)    |
| ------------------------ | --- | ------------------- | ---------------------------------------------------------- | -------------- |
| 健康首页                     | 用户  | I（首页聚合）             | —                                                          | 原设计未单列         |
| 健康风险测评（含营养问卷/游客试测）       | 用户  | D+E                 | assessment_template / assessment_record                    | 已有             |
| 体质测评 / 舌象体质测评            | 用户  | E（扩 type）           | assessment_template(扩) / tongue_image(NEW)                 | **新增·图片识别**    |
| 营养师智能体（对话）               | 用户  | G                   | chat_session / chat_message                                | 已有             |
| 食养食疗计划 / 食养计划            | 用户  | F2(NEW)             | diet_plan / diet_plan_recipe(NEW)                          | **新增**         |
| 五大健康场景（健康饮食管家/慢病管理专家/社交宴请助手/妇幼健康守护/旅行美食智荐） | 用户  | 配置+路由               | system_config(扩 scene)                                     | **新增·配置**      |
| 个人数据分析（雷达/柱状/异常高亮）       | 用户  | I                   | assessment_report 聚合                                       | 已有             |
| 智能体中心                    | 用户  | 智能体中心(NEW)          | system_config / agent 定义                                   | **新增·页面**      |
| 智能报告 / 综合报告              | 用户  | F                   | assessment_report                                          | 已有             |
| 健康档案                     | 用户  | B                   | user_profile                                               | 已有(可视化)        |
| 智能荐菜智能体                  | 用户  | G(扩)/recommend(NEW) | 复用食材库(K)                                                   | **新增·智能体**     |
| 数字人助手 / 小智               | 用户  | 交互层(复用 G + TTS)     | —                                                          | **新增·交互(需S5)** |
| 营养师工作台（客户/批注/资质）         | 营养师 | C                   | dietitian_profile / dietitian_customer / report_annotation | 已有             |
| 管理员后台                    | 管理员 | J                   | operation_log / system_config                              | 已有             |
| 运营可视化大屏                  | 运营  | J(扩)                | operation_log 聚合                                           | **新增·角色/视角**   |

**角色/视角（原型出现）**：普通用户 · 营养师 · 管理员 · 运营（大屏）· 医师/专家（疑似营养师资质子类，CHK-08 待确认）。

---

## 9. 设计文档待同步扩展（下一步）

> 改造清单已按原型扩展，且《设计_01 接口设计》《设计_02 数据库设计》**已同步升 V1.1**（见下方），不再与清单冲突：

1. **设计_01（V1.1 已完成）**：补 L 食养计划、M 智能荐菜、N 智能体中心、O 舌象图片、P 运营大屏 共 5 组接口；`/chat/query` 增加 `agent` 路由参数；§4.1 tags、§5 覆盖检查同步；`operator` 角色标注待 CHK-08。
2. **设计_02（V1.1 已完成）**：新增 `tongue_image` / `diet_plan` / `diet_plan_recipe` 三张表；`assessment_template` 扩 `type` 枚举（risk/nutrition/constitution/tongue）；`system_config` 增 `agent.catalog` / `scene.config`；§2 ER 图同步。
3. **设计评审_01（待办）**：对新增接口/表重跑 Blocker 检查（尤其 SSE 鉴权 B1、舌象图片存储合规），建议在开工第二批前完成。

---

*本清单为编码与自测阶段"开工前核查"交付物。2026-09-10 已据用户 4 项决策完成旧码迁移（§3.1）并锁定基线（§7）；同日经用户确认"以原型图为准"扩展范围（§0 注 / §3.5 / §5 / §8）。据此排期开工。*
