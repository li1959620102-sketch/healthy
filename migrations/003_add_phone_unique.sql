-- 003: 手机号注册/登录 —— 给 sys_user.phone_hash 补唯一索引
-- 背景：模型里声明了 uk_phone_hash，但该表在约束声明前已建（create_all 不改既有表），
--       实库此前只有 PRIMARY / user_id / username 三个索引。
-- 说明：MySQL 唯一索引允许多个 NULL，存量账号（phone_hash 为 NULL）不受影响。
-- 执行：mysql -u<user> -p <db> < migrations/003_add_phone_unique.sql
-- 回滚：ALTER TABLE sys_user DROP INDEX uk_phone_hash;

ALTER TABLE sys_user ADD UNIQUE KEY uk_phone_hash (phone_hash);
