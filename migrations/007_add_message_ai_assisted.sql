-- 007_add_message_ai_assisted.sql
-- 营养师故事 4（AI 咨询辅助建议）：消息需记录"本条是否经 AI 辅助生成"。
--
-- 背景：需求写法是「使用 AI 生成咨询辅助建议**并人工修改**」——AI 产出的是草稿，
-- 发给用户的那条消息由营养师定稿。若不留痕迹，运营与审核方无法回答
-- "这条建议是模型生成的还是营养师自己写的"，也无法统计 AI 辅助的实际使用率，
-- 更无法在出现误导性建议时回溯是模型原文还是人工改动后的版本。
--
-- 该列只标记**来源**，不代表内容未经人工确认：置 1 的消息必然是营养师
-- 在草稿基础上（可能已修改）主动发送的。
--
-- 执行：mysql -h127.0.0.1 -uroot -p test_db < migrations/007_add_message_ai_assisted.sql

ALTER TABLE consultation_message
  ADD COLUMN ai_assisted TINYINT(1) NOT NULL DEFAULT 0
  COMMENT '本条是否经 AI 辅助生成（营养师审核后发送）'
  AFTER content;
