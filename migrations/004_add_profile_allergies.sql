-- 004: 健康档案增加过敏 / 忌口结构化字段（测评档案题写入）
-- 背景：「测评末尾过敏/忌口选择题」需要把用户选择以 JSON 落库，
--       供聊天右栏健康上下文展示、食养计划服务端忌口过滤使用。
-- 格式：{"labels": ["海鲜鱼类", "蛋类"], "keywords": ["虾", "蟹", "鱼", "鸡蛋"]}
--       labels 用于界面展示，keywords 用于菜谱忌口匹配。
-- 执行：mysql -u<user> -p <db> < migrations/004_add_profile_allergies.sql
-- 回滚：ALTER TABLE user_profile DROP COLUMN allergies_json, DROP COLUMN taboo_json;

ALTER TABLE user_profile
    ADD COLUMN allergies_json VARCHAR(512) NULL COMMENT '过敏清单 JSON {labels,keywords}（测评档案题写入）',
    ADD COLUMN taboo_json VARCHAR(512) NULL COMMENT '忌口清单 JSON {labels,keywords}（测评档案题写入）';
