-- Agent Runtime 第一阶段：任务、步骤、工具调用、记忆、审批与事件。
-- 生产环境执行前请在专用数据库备份，并核对当前数据库版本。

CREATE TABLE IF NOT EXISTS agent_task (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL UNIQUE,
  agent_key VARCHAR(64) NOT NULL,
  parent_task_id VARCHAR(64) NULL,
  user_id VARCHAR(64) NULL,
  goal VARCHAR(2000) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  current_step VARCHAR(128) NULL,
  plan_json JSON NULL,
  context_snapshot_json JSON NULL,
  result_json JSON NULL,
  error_code VARCHAR(64) NULL,
  error_message VARCHAR(1000) NULL,
  retry_count INT NOT NULL DEFAULT 0,
  max_retries INT NOT NULL DEFAULT 2,
  trace_id VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL,
  started_at DATETIME NULL,
  paused_at DATETIME NULL,
  finished_at DATETIME NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_agent_task_user (user_id, created_at),
  INDEX idx_agent_task_status (status, updated_at),
  INDEX idx_agent_task_trace (trace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_step (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(128) NOT NULL,
  step_order INT NOT NULL DEFAULT 0,
  action VARCHAR(500) NOT NULL,
  tool_name VARCHAR(128) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  input_snapshot JSON NULL,
  output_snapshot JSON NULL,
  attempt INT NOT NULL DEFAULT 0,
  error_code VARCHAR(64) NULL,
  error_message VARCHAR(1000) NULL,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  UNIQUE KEY uk_agent_step (task_id, step_id),
  INDEX idx_agent_step_task (task_id, step_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_tool_call (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(128) NOT NULL,
  tool_name VARCHAR(128) NOT NULL,
  arguments_json JSON NULL,
  result_json JSON NULL,
  permission_result VARCHAR(32) NOT NULL DEFAULT 'allowed',
  latency_ms INT NULL,
  model_name VARCHAR(128) NULL,
  prompt_tokens INT NULL,
  completion_tokens INT NULL,
  total_tokens INT NULL,
  estimated_cost DECIMAL(12,6) NULL,
  success BOOLEAN NOT NULL DEFAULT FALSE,
  error_message VARCHAR(1000) NULL,
  created_at DATETIME NOT NULL,
  INDEX idx_agent_tool_task (task_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_memory (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id VARCHAR(64) NULL,
  task_id VARCHAR(64) NULL,
  agent_key VARCHAR(64) NOT NULL,
  memory_type VARCHAR(32) NOT NULL,
  content_json JSON NOT NULL,
  source VARCHAR(128) NULL,
  confidence DECIMAL(5,4) NULL,
  expires_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_agent_memory_user (user_id, memory_type, updated_at),
  INDEX idx_agent_memory_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_approval (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  approval_id VARCHAR(64) NOT NULL UNIQUE,
  task_id VARCHAR(64) NOT NULL,
  step_id VARCHAR(128) NOT NULL,
  approval_type VARCHAR(64) NOT NULL,
  risk_level VARCHAR(32) NOT NULL DEFAULT 'medium',
  approval_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  requested_by VARCHAR(64) NULL,
  approved_by VARCHAR(64) NULL,
  comment VARCHAR(1000) NULL,
  expires_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  approved_at DATETIME NULL,
  INDEX idx_agent_approval_status (approval_status, created_at),
  INDEX idx_agent_approval_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_event (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL,
  trace_id VARCHAR(64) NOT NULL,
  span_id VARCHAR(64) NULL,
  event_type VARCHAR(64) NOT NULL,
  payload_json JSON NULL,
  created_at DATETIME NOT NULL,
  INDEX idx_agent_event_trace (trace_id, created_at),
  INDEX idx_agent_event_task (task_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
