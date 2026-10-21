-- 008_add_deletion_task_started_at.sql
-- 注销清理任务需要记录"本次执行开始时间"。
--
-- 背景：清理作业把任务置为 running 后开始删数据，如果进程在中途被杀，
-- 任务会永久停在 running —— 既不重试，也不再进"到期待办"的范围，数据就一直留着。
-- 有了 started_at，下一轮就能把"running 且 started_at 超过 2 小时"的任务退回 pending。
--
-- 注意：既有表加列必须手工执行本文件（create_all 只建新表，不会改已有表结构）。
-- 执行：mysql -u<user> -p <db> < migrations/008_add_deletion_task_started_at.sql
-- 幂等：先判断列是否存在，重复执行不报错。

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'data_deletion_task'
    AND COLUMN_NAME = 'started_at'
);

SET @ddl := IF(@col_exists = 0,
  'ALTER TABLE `data_deletion_task` ADD COLUMN `started_at` DATETIME NULL COMMENT ''本次执行开始时间（用于识别中途崩溃的任务）'' AFTER `execute_at`',
  'SELECT ''started_at 已存在，跳过'' AS msg');

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
