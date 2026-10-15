-- AI 智参平台 · 第一批数据库迁移
-- 作用：为既有表 sys_user / knowledge_files 追加 V1.1 扩展字段。
-- 说明：SQLAlchemy create_all 只创建"不存在的表"，不会 ALTER 已有表，
--       因此新表由 create_all 自动创建，既有表的新增列必须执行本脚本。
-- 执行：mysql -u<user> -p<pass> <db> < migrations/001_extend_legacy_tables.sql

-- ---------- 1. sys_user 扩展 ----------
ALTER TABLE sys_user
  ADD COLUMN phone_cipher VARBINARY(512) COMMENT '手机号密文 AES-256' AFTER username,
  ADD COLUMN phone_hash   VARCHAR(64)     COMMENT '手机号 HMAC-SHA256，用于唯一性与检索',
  ADD COLUMN email_cipher VARBINARY(512) COMMENT '邮箱密文',
  ADD COLUMN email_hash   VARCHAR(64)     COMMENT '邮箱 HMAC',
  ADD COLUMN wechat_openid  VARCHAR(64)   COMMENT '微信 openid',
  ADD COLUMN alipay_user_id VARCHAR(64)   COMMENT '支付宝 user_id',
  ADD COLUMN last_active_at DATETIME      COMMENT '最近活跃时间（30 分钟自动登出判断）',
  ADD COLUMN deleted_at     DATETIME      COMMENT '软删除时间（注销后 15 日内完成删除/匿名化）',
  ADD UNIQUE KEY uk_phone_hash (phone_hash),
  ADD UNIQUE KEY uk_email_hash (email_hash),
  ADD UNIQUE KEY uk_wechat (wechat_openid),
  ADD KEY idx_role_status (role, status),
  ADD KEY idx_deleted (deleted_at);

-- ---------- 2. knowledge_files 扩展 ----------
ALTER TABLE knowledge_files
  ADD COLUMN category     VARCHAR(32) COMMENT 'food 食材 / disease 疾病 / guideline 指南',
  ADD COLUMN source       VARCHAR(200) COMMENT '来源，如 中国居民膳食指南2022',
  ADD COLUMN doc_version  VARCHAR(32) COMMENT '知识版本',
  ADD COLUMN embed_status VARCHAR(20) DEFAULT 'pending' COMMENT '向量化状态 pending/processing/done/failed',
  ADD COLUMN embedded_at  DATETIME COMMENT '向量化完成时间',
  ADD COLUMN chunk_count  INT DEFAULT 0 COMMENT '分块数量',
  ADD KEY idx_category (category, embed_status),
  ADD KEY idx_collection (collection_name);
