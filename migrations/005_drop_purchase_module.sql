-- 005_drop_purchase_module.sql
-- 移除「自助购买」模块（2026-10-15 用户决策：整体删除，不再保留留桩能力）
--
-- 背景：该模块原为「第三方电商对接待接入」的留桩能力（checkout_enabled=false），
--       依赖外部电商接口，一期从未产生真实业务；用户决定不再保留。
-- 影响范围：purchase_list / purchase_item 两张表（执行时均为 0 行，无业务数据）。
-- 回滚方式：见 AI智参平台_设计_02_数据库设计.md §3.21（建表语句），
--           或从整库备份 backups/test_db_before_p1_4_20261015.sql 中恢复这两张表。

DROP TABLE IF EXISTS `purchase_item`;
DROP TABLE IF EXISTS `purchase_list`;
