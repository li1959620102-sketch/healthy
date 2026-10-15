-- 002_add_plan_period.sql
-- 食养计划「周/月」周期（原型 diet 页：周计划 / 月计划 切换）
-- 说明：SQLAlchemy create_all 不会修改已存在的表，故既有 diet_plan 需显式 ALTER。
-- 执行：mysql -u<user> -p<db> < migrations/002_add_plan_period.sql
ALTER TABLE `diet_plan`
  ADD COLUMN `period` ENUM('week', 'month') NOT NULL DEFAULT 'week'
  COMMENT '周期 week 周计划 / month 月计划' AFTER `template_type`;
