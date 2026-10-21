-- 为已经执行过 009_agent_runtime.sql 的环境补充工具成本字段。
ALTER TABLE agent_tool_call ADD COLUMN model_name VARCHAR(128) NULL;
ALTER TABLE agent_tool_call ADD COLUMN prompt_tokens INT NULL;
ALTER TABLE agent_tool_call ADD COLUMN completion_tokens INT NULL;
ALTER TABLE agent_tool_call ADD COLUMN total_tokens INT NULL;
ALTER TABLE agent_tool_call ADD COLUMN estimated_cost DECIMAL(12,6) NULL;
