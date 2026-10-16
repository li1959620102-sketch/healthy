-- 006_add_report_scoring_version.sql
-- 评审 M6（补齐）：报告需记录**生成当时**的评分规则版本。
--
-- 背景：评分权重与等级阈值都落在 system_config（scoring.*），管理员可随时调整。
-- 若报告只存分数不存版本，就无法回答"这份 62 分是按哪套规则算的"——
-- 用户拿旧报告来对质、或做跨期趋势对比时都会失真。
-- 该列在生成报告时写入，历史报告的值不随配置变更而改变。
--
-- 执行：mysql -h127.0.0.1 -uroot -p test_db < migrations/006_add_report_scoring_version.sql

ALTER TABLE assessment_report
  ADD COLUMN scoring_version VARCHAR(32) NULL
  COMMENT '生成时的评分规则版本（取自 system_config 的 scoring.version）'
  AFTER model_name;
