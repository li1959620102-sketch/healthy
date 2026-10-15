# AI 智参平台 · 后端全面体检报告

> 日期：2026-09-10　|　范围：`D:\AI_health` 后端全部
> 方式：**运行时实测**（编译/导入/路由枚举/依赖发真实协议包/库表对照/全量测试），非人工浏览代码
> 结论摘要：**功能面完整、测试全绿；发现 3 个 P0 安全/合规问题（均在遗留模块）、4 个 P1、若干 P2。不阻塞前端开发，但上线前必须处理 P0。**

---

## 〇、一眼看结论

| 维度 | 结果 |
| --- | --- |
| 编译 / 导入 | ✅ 全部通过 |
| 接口规模 | **122 个端点**（体检时 133，其中 11 条遗留已下线） |
| 数据规模 | **33 张表**（新平台 30 + 遗留 3） |
| 自动化测试 | ✅ **148 / 148 通过，0 失败 0 错误 0 跳过**（修复后 9.9s） |
| 依赖实连 | ✅ MySQL / Redis / MinIO / Milvus / Ollama **全部真连成功** |
| 技术红线·LLM 不算分 | ✅ 完全合规 |
| 技术红线·LLM 不直连库 | ✅ **已合规**（违规的遗留模块已于 2026-09-10 下线，见 P0-1） |
| 鉴权覆盖 | ✅ **已修复**：遗留 22 条端点全部下线，其余 113 条均有鉴权 |
| 数据库清洁度 | ⚠️ 14 条孤儿报告（根因已修，存量按用户决定暂不清）+ 4 条手工冒烟残留 |

**问题分级**：P0 × 3、P1 × 4、P2 × 4。
**处置进度（2026-09-10 第二次决策后）**：**P0 全部已修复 ✅**；P1-2（测试泄漏）已修复；其余 P1/P2 按用户决定保留待办。

---

## 〇之一、处置记录（2026-09-10 用户选择「A：先清 P0」）

用户先决定「先不动、只留待办」，复查后改为 **先清 P0（第 1~3 项）**，脏数据继续不动。**本次实际改动如下（已完成并验证）**：

| # | 改动 | 文件 | 验证结果 |
| --- | --- | --- | --- |
| 1 | **下线 4 个遗留 router**（取消 import 与挂载，源码保留 + 恢复说明） | `main.py`（原 5-8 行、86-89 行） | 4 个前缀全部 404；**重复注册 11 组 → 0**；**Duplicate operationId 警告 11 条 → 0**；**Vanna 不再初始化** |
| 2 | **关闭默认演示账号** | `app/config.py:34`（默认 `"true"` → `"false"`）、`.env`、`.env.example` | 运行时 `INIT_DEMO_USER=False`；调用 `ensure_default_user()` 后 demo 数量仍为 0 |
| 3 | **删除 demo 账号**（先留档完整行） | 库 `sys_user` + `user_profile` | 各删 1 行，复查残留 0；该账号无其它关联数据（仅 1 行 profile） |

**连带收益**：全量测试从 **25.9s → 9.9s**（不再初始化 Vanna / ChromaDB / 训练线程）。

**回归验证（P0 修复后）**：
- `compileall` ✅ 0 错误
- `/ready` → `code=0`，`checks={mysql:ok, milvus:ok, redis:ok, minio:ok}`，`ready=true` ✅
- 遗留前缀探测：`/api/v1/{text2sql,file,chunks,query}/*` **全部 404** ✅（POST 返回 405 是前端静态目录挂载兜底所致，非路由残留 —— 已用路由表确认）
- 全量测试：**148 passed / 0 failed / 0 error / 0 skipped** ✅

**未改动**：脏数据（14 条孤儿报告 + 4 条 `smoke_u` 冒烟记录）按决定**保持不动**。

---

## 〇之二、其余待办清单（P1/P2，用户决定：暂不动）

> 以下**尚未处理**，留作待办，每项均含确切位置与具体改法。

### 建议尽快完成（P1）

| # | 待办 | 确切位置 | 具体改法 |
| --- | --- | --- | --- |
| 5 | **Milvus 集合隔离** | `app/config.py:64` + `.env:18` | 新建平台专属集合（如 `ai_health_kb_v1`），改 `MILVUS_DEFAULT_COLLECTION`；检索侧按 agent/知识域选集合 |
| 6 | **清理存量脏数据** | 库 + MinIO | 删 14 条孤儿报告、4 条 `smoke_u` 手工冒烟记录（清单见 §五） |
| 7 | **遗留表/视图纳管** | 设计_02 | 把 `user_health_risk_assessment`、`high_risk_patients`、`latest_assessments` 登记为「遗留只读，待下线」 |
| **11** | **⚠️ 营养师角色命名不一致 → 工作台整体不可用** | `app/core/deps.py:22` vs `app/services/user_service.py:42` | `ROLE_DIETITIAN="nutritionist"`，而注册白名单是 `("user","dietitian","admin")`，且**无任何代码路径**产出 `nutritionist` → 12+ 个 `require_role(ROLE_DIETITIAN)` 端点对真实账号永久 10005。**改法二选一**：① 把 `deps.py` 常量改为 `"dietitian"`（与之配套的 `dietitian_service` 已同时容忍两种值，改动集中、风险最低）；② 注册白名单加 `"nutritionist"`，并让管理员审核通过时把 `sys_user.role` 升为 `nutritionist`。**建议 ①**。详见 §二 P1-5 |

### 可延后（P2）

| # | 待办 | 确切位置 |
| --- | --- | --- |
| 8 | 修复 5 处裸 `except:` | `rag_query_rerank_service.py`×3、`adapter/universal_client.py`×2（均遗留） |
| 9 | MinIO 桶名去遗留化 | `.env:39` `MINIO_BUCKET="vanna-training-data"` → 平台语义命名 |
| 10 | 移除死配置 | `app/config.py:61` `MILVUS_URI`（定义但从未使用） |

### ✅ 已完成（本轮）

| # | 事项 | 说明 |
| --- | --- | --- |
| P0-1 | **下线 4 个遗留 router** | `main.py` 取消 import 与挂载（源码保留、含恢复说明）；遗留前缀全部 404 |
| P0-2 | **关闭并删除演示账号** | `config.py` 默认改 `false`；`.env`/`.env.example` 显式关闭；库中 demo 账号已删且不再重建 |
| P0-3 | **路由重复注册** | 随 P0-1 一并消除：重复注册 11 组 → **0**，Duplicate operationId 警告 11 条 → **0** |
| — | **测试泄漏修复** | `tests/integration/test_temporal_rules.py::test_bv21_guest_assessment_quota` 两处 `cleanup.append` 调换顺序；已验证孤儿数不再增长 |

---

## 一、P0 问题（✅ 全部已于 2026-09-10 修复）

> 以下保留原始发现记录，供追溯。**当前状态：三项均已修复并验证**（见 §〇之一）。

### P0-1 ✅ 已修复 ｜ 遗留模块零鉴权，且让 LLM 直连数据库

**位置**：`app/routers/text2sql_router.py`、`text2echarts_router.py`、`file_handle_router.py`、`chunk_router.py`
**挂载**：`main.py:86-89`

```python
# text2sql_router.py:152-158
generated_sql = vn.generate_sql(request.question)   # LLM 生成 SQL
if request.auto_execute and generated_sql:          # 默认 True
    result = vn.execute_sql(generated_sql)          # 直接执行
```

**两个问题叠加**：
1. **零鉴权**：这 4 个 router **内部一个 `Depends()` 都没有**。任何人可访问：
   - `POST /api/v1/file/uploadFile`、`/file/addVectorData`、`/file/search`、`/chunks/parseFile`
   - `POST /api/v1/text2sql/query`、`train`、`train_upload`、`GET /ask`、`/tables`
   - `POST /api/v1/query/generate`
2. **违反技术红线**：LLM 生成 SQL 后**直接执行**，且 `auto_execute` 默认 `True`、`/ask` 强制 `True`；`user_id` 过滤参数是可选且不强制 → **无行级权限**。

**证据**：鉴权审计显示这 22 条（去重后 11 个端点）无任何鉴权依赖；且 `text2sql_query` 表无审计记录。

**建议处置**（三选一，推荐 A）：
- **A（推荐）**：收尾批次**下线这 4 个 router**（新能力已由 `/api/v1/analytics/**` 受控模板版承接，功能不缺失）
- **B**：保留但**加 `require_role(ROLE_ADMIN)`** 并强制 `auto_execute=False`
- **C**：用网关/中间件统一拦截 `/api/v1/{file,chunks,text2sql,query}` 前缀

---

### P0-2 ✅ 已修复 ｜ 默认演示账号 `demo` / `123456` 自动启用

**位置**：`app/services/user_service.py:26-27`、`main.py:74`
**配置**：`app/config.py:34` → `INIT_DEMO_USER` 默认 **`"true"`**；`.env` 与 `.env.example` **均未关闭**

**实测**：库中**确实存在**该账号，且为 `active`：

```
UDEMO0000001 | demo | role=user | status=active | 创建于 2026-09-09 20:00:12
```

**风险**：部署到生产会自带一个弱口令可登录账号。
**建议**：`.env` 显式 `INIT_DEMO_USER=false`；生产环境删除该账号；`config` 默认值改为 `false`。

---

### P0-3 ✅ 已修复（随 P0-1 一并解决）｜ 遗留端点重复注册（同一端点被注册两次）

**位置**：`main.py:86-89`
**原因**：`XxxRouter.__init__` 内部已执行 `self.router = self._register_routes()`，而 `main.py` 又调用了一次 `._register_routes()` → **每个遗留端点被注册两遍**。

**证据**：生成 OpenAPI 时 FastAPI 抛 **11 条 `Duplicate Operation ID` 警告**；实际 `/api` 端点条目 144 条 vs 去重后 133 条。

**影响**：路由表膨胀；OpenAPI 文档 operationId 冲突（后注册者覆盖前者）；代码维护易误判。
**建议**：`main.py` 改为 `app.include_router(FileHandleRouter().get_router(), prefix=...)`（用已注册好的实例），或移除 `__init__` 里的注册调用。**随 P0-1 一起下线更省事。**

---

## 二、P1 问题

### P1-1 检索集合仍指向遗留 `nrs2002`，知识库未隔离

**位置**：`app/config.py:64` → `MILVUS_DEFAULT_COLLECTION` 默认 `"nrs2002"`；`.env:18` 同样为 `nrs2002`
**使用处**：`app/services/chat_query_service.py:28`（智能查询的向量检索）

**实测 Milvus 集合**：`nrs2002`(6) / `nrs2002_legacy_m0_20260909`(6) / `test`(3) / `talent_vectors`(14)
→ **没有任何 AI 智参平台的知识库集合**；且 `talent_vectors` 属**另一个项目**。

**风险**：
1. 平台检索与 NRS2002 遗留数据**混在一起**，会污染召回结果
2. 管理员把文档上传到新集合（如 `ai_health_kb`）后，**检索侧仍读 `nrs2002` → 检索不到新知识**
3. 官方要求「多知识库按意图隔离检索」无法满足

**建议**：新建平台专属集合（如 `ai_health_kb_v1`），`.env` 改 `MILVUS_DEFAULT_COLLECTION`；检索侧支持按 agent/知识域选集合（代码注释里已预留该思路）。

### P1-2 测试清理顺序错误 → 每轮泄漏 1 条孤儿报告（**已修复并验证**）

**位置**：`tests/integration/test_temporal_rules.py::test_bv21_guest_assessment_quota`
**根因**：`conftest` 的 `cleanup` fixture 是**逆序执行**，而该用例先登记"删报告"、后登记"删记录"，导致实际执行时**先删了记录**，删报告的子查询（按 `guest_token` 找 `record_id`）返回空 → 报告永远删不掉。

**证据（可复现）**：连续 4 轮全量测试，孤儿报告数 **10 → 11 → 15**，时间戳与每轮测试一一对应；14 条孤儿 `user_id` 全为 `NULL`（游客报告）。

**修复**：调换两处 `cleanup.append` 的登记顺序。**已验证：跑该用例前后孤儿数均为 14，不再增长。**

> ⚠️ **连带纠正**：此前多次声明的「跑完零残留」**不准确** —— 残留复查用 `where user_id like 'IT%'` 统计，而游客报告 `user_id` 为 `NULL`，被过滤掉。已补查全量与 `IS NULL` 口径。

### P1-3 遗留表/视图未纳管

实库 33 表中，3 个不属于新平台模型：`user_health_risk_assessment`（NRS2002 历史表）、`high_risk_patients`、`latest_assessments`（视图）。
**建议**：在设计_02 中登记为「遗留只读，待下线」，避免后续误用。

### P1-4 孤儿数据存量未清

截至体检结束：**孤儿报告 14 条**（均为游客流程测试残留，`user_id IS NULL`）+ **4 条 `smoke_u` 手工冒烟记录**（2026-09-10 11:43–11:46，属早期手工验证遗留，非自动化测试产生）。
**建议**：确认后清理（见 §五 待你拍板）。

### P1-5 ⚠️ 营养师角色命名不一致 → 营养师工作台对真实账号整体不可用（2026-09-10 前端联调新发现）

**现象**：用 `POST /api/v1/auth/register` 且 `role="dietitian"` 注册账号后登录，token 里 `role=dietitian`，但访问 `/api/v1/nutritionists/me/overview`、`/me/customers`、`/me` 一律返回 `10005 无权限访问该资源`（实测）。

**根因**：角色字符串两套口径、互不相通。

| 位置 | 取值 | 说明 |
| --- | --- | --- |
| `app/core/deps.py:22` `ROLE_DIETITIAN` | **`"nutritionist"`** | 被 **12+ 个** `require_role(ROLE_DIETITIAN, …)` 守卫引用（nutritionist / consultation / report / template / admin 路由） |
| `app/services/user_service.py:42` 注册白名单 | `("user", "dietitian", "admin")` | 不接受 `"nutritionist"` |
| `app/schemas/platform.py:24` 字段说明 | `user / dietitian / admin` | 同上 |
| `sys_user.role` 列注释 | `user/dietitian/admin` | 同上 |
| `dietitian_service.py` 各方法内联判断 | `("nutritionist","dietitian","admin")` | **两边都容忍**（说明历史上就存在两套叫法） |

→ 注册只能造出 `dietitian`；`dietitian` 又过不了 `require_role("nutritionist")`；而**全代码库没有任何路径把 `sys_user.role` 写成 `nutritionist`**（`UserService.register` 白名单不含它；`AdminService.audit_nutritionist` 只改 `dietitian_profile.audit_status`，不动 `sys_user.role`）。**结论：营养师工作台/咨询接待/模板管理/客户报表等所有营养师端点，当前没有任何合法账号能访问。**

**影响面**：前端「营养师专业工作台」屏（本次刚接真数据）在真实环境必然显示「需要营养师账号登录」；`consultation` / `report` / `template` 中依赖 `ROLE_DIETITIAN` 的端点同样不可达。

**改法（二选一，建议 ①）**：
- **①（推荐，改动集中、风险最低）** 把 `app/core/deps.py:22` 改为 `ROLE_DIETITIAN = "dietitian"`。理由：与之配套的 `dietitian_service` 已同时容忍 `nutritionist`/`dietitian` 两种值，改常量即全链路打通；本机库中当前无任何 `role='nutritionist'` 存量行（实测 3 个账号全为 `user`），无迁移负担。
- **②** 注册白名单加入 `"nutritionist"`，并在 `AdminService.audit_nutritionist` 审核通过时把 `sys_user.role` 升为 `nutritionist`（更贴合「先申请→审核→授权」的业务流，但要改两处 + 补测试）。

**验证方式**：改后用一个 `role="dietitian"`（或 `nutritionist`，视方案）账号登录，`GET /api/v1/nutritionists/me/overview` 应返回 `code=0`。
**本轮处置**：**未改**（属后端角色语义决策，按用户「先不动」惯例留待办）。前端联调时以测试态临时置 `role='nutritionist'` 验证渲染，验证后已还原、无关数据已清。

---

## 三、P2 问题

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| P2-1 | 裸 `except:`（吞 `KeyboardInterrupt`/`SystemExit`） | `rag_query_rerank_service.py`×3、`adapter/universal_client.py`×2 | 均在**遗留模块**；新代码 0 处 |
| P2-2 | MinIO 桶名遗留 | `.env:39` `MINIO_BUCKET="vanna-training-data"` | 是 Vanna 遗留命名，与平台语义不符 |
| P2-3 | 死配置 `MILVUS_URI` | `app/config.py:61` | 定义后**从未被任何代码引用**，`.env` 也未定义 → 恒为 `None` |
| P2-4 | `assessment_report.user_id` 在游客流程为 `NULL` | `assessment_service.py:234` | 登录用户正常写入（实测 `smoke_u` 正确）；游客无 `user_id` 属预期，但导致按 `user_id` 的清理/筛选需额外用 `guest_token` 兜底 |

---

## 四、通过的检查项（无问题）

| 检查 | 结果 |
| --- | --- |
| 全量编译 `compileall app main.py` | ✅ 0 错误 |
| `import main` | ✅ 149 routes |
| OpenAPI 生成 | ✅ **122 操作**（`--junitxml` 口径），**0 缺中文 summary、0 缺 tags**（项目硬性要求满足） |
| 路由重复注册 | ✅ **0 组**（修复前 11 组） |
| `ErrCode` 未定义引用扫描 | ✅ **0 处**（此前修过，未回归） |
| SQL 字符串拼接/注入扫描 | ✅ **0 处**（`text(f"...")`、`%`-format、字符串 `+` 均无） |
| 硬编码密钥扫描 | ✅ 仅 1 处（`DEFAULT_PASSWORD`，见 P0-2） |
| 技术红线·**LLM 不算分** | ✅ `app/services/scoring_engine.py` **零 LLM 依赖**（只 import `rules`），算分纯确定性规则 |
| 留桩一致性 | ✅ 7 处能力（舌象辨识/数字人语音/OCR/旅行/自助购买/大屏截图/自助购买智能体）**全部**返回 `enabled`/`recognizable`/`checkout_enabled` = `false` + 明确 note，**无一处伪装成功** |
| MySQL | ✅ 8.0.27，33 表，连接正常 |
| Redis | ✅ `health=ok`（6380 专属容器） |
| MinIO | ✅ `health=True`，桶 `vanna-training-data`，对象数 0 |
| Ollama | ✅ 3 模型：`qwen3:0.6b` / `bge-m3:latest` / `nomic-embed-text:latest` |
| 迁移应用 | ✅ `diet_plan.period` 已存在（`002_add_plan_period.sql`） |
| 关键唯一键 | ✅ `assessment_report.uk_record` / `checkin_record.uk_checkin_user_date_type` / `assessment_record.uk_idempotency` / `diet_plan.uk_report` 齐全 |
| 孤儿数据（除报告） | ✅ 记录无模板 / 食谱无方案 / 打卡无方案 / 宾客无菜单 / 明细无清单 / OCR指标无报告 / 咨询消息无咨询 / 批注无报告 **全部为 0** |
| 全量测试 | ✅ **148 passed / 0 failed / 0 error / 0 skipped**（JUnit XML 确定性计数） |
| 残留（模板/用户/新11表/MinIO） | ✅ 全部 0 |

---

## 五、诚实边界（体检查不到的部分）

1. **S2 RAG 准确率（50 条）** —— **未复测**，无法判断检索质量
2. **S3 并发 30 TPS** —— **未复测**，无法判断承载能力
3. **S1 报告生成时延** —— 本机 CPU 实测：评分骨架 P95 **75ms** ✅ / 首字节 **14.2s** ❌ / 完整 **23.5s** ❌；**生产 GPU 未到位，结论不可用于拍板**
4. **6 项留桩能力** —— 按约定未实现（舌象辨识/数字人语音/OCR/旅行智荐/自助购买/大屏截图）
5. **测试覆盖** —— 216 条用例中 **已覆盖 49 / 部分 14 / 未覆盖 153**（未覆盖主要是 UI、前后端联调、异常降级、安全越权）
6. **前端** —— 一行未写；本文所有「功能可用」均指**接口层**

---

## 六、结论：现在能开前端吗？

**能。但建议先花小代价清掉 P0，再开前端。**

**为什么不阻塞前端**：P0-1/P0-3 都在**遗留 router**（`/file`、`/chunks`、`/text2sql`、`/query`），前端 10 屏**一个都用不到**；P0-2 是配置项。前端要用的 A–X 共 24 组接口**全部正常且已从文档同步**（设计_01 V1.2）。

**建议顺序**：
1. **清 3 个 P0**（预计最小改动）：
   - 下线/加鉴权 4 个遗留 router（P0-1 + P0-3 一并解决）
   - `.env` 关掉 `INIT_DEMO_USER` 并删除 `demo` 账号（P0-2）
2. **清存量脏数据**（P1-4）：14 条孤儿报告 + 4 条冒烟记录
3. **开前端**（home / assessment / diet / nutrition 四屏先行）
4. 上线前再处理 P1-1（Milvus 集合隔离）与 P2

### 已决事项（2026-09-10）

- 遗留 router 处置：**已执行「方案 A：下线」** —— 解 P0-1 + P0-3（见 §〇之一）
- `demo` 账号：**已关闭开关并删除账号** —— 解 P0-2（见 §〇之一）
- 存量脏数据清理：**暂不动**（保留待办，见 §〇之二 第 6 项）
- 其余 P1/P2：**暂不动**（保留待办，见 §〇之二）

> **当前结论**：**3 个 P0 已全部清零**，后端已达到"可开前端、且对外暴露前无已知 P0 阻塞"的状态。
> 剩余待办均不阻塞开发：P1-1（Milvus 集合隔离）影响知识库检索效果、P2 若干为整洁性问题。

---

## 附：本次体检使用的可复现命令

```bash
cd /d/AI_health

# ① 编译 + 导入 + OpenAPI
D:/conda/envs/ai_py311/python.exe -c "import main; s=main.app.openapi(); print(len(s['paths']))"

# ② 鉴权覆盖审计（route.dependant 递归取真实依赖）
D:/conda/envs/ai_py311/python.exe "check_auth.py"

# ③ 代码质量扫描（ErrCode/SQL拼接/密钥/裸except）
D:/conda/envs/ai_py311/python.exe "scan_quality.py"

# ④ 依赖实连（MySQL/Redis/MinIO/Milvus/Ollama 发真实协议包）

# ⑤ 数据库一致性（模型vs实库、迁移、孤儿数据、唯一键）
D:/conda/envs/ai_py311/python.exe "check_db.py"

# ⑥ 全量测试（确定性计数）
D:/conda/envs/ai_py311/python.exe -m pytest tests -p no:warnings --tb=no -q --junitxml=pt.xml

# ⑦ 残留复查（务必包含 user_id IS NULL 口径）
# select count(*) from assessment_report r left join assessment_record d on r.record_id=d.id where d.id is null
```

---

*本报告所有数字均来自运行时实测，未手工统计。*
