-- 数据集来源登记表：真实、脱敏、合成数据必须可区分、可追溯。
CREATE TABLE IF NOT EXISTS dataset_provenance (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL UNIQUE,
    source_type ENUM('synthetic', 'deidentified_real', 'production') NOT NULL,
    generator_version VARCHAR(32) NOT NULL,
    period_start DATE NULL,
    period_end DATE NULL,
    status ENUM('running', 'complete', 'failed') NOT NULL DEFAULT 'running',
    row_counts JSON NULL,
    parameters_json JSON NULL,
    checksum_sha256 CHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    INDEX idx_dataset_source_status (source_type, status),
    INDEX idx_dataset_period (period_start, period_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集来源与生成批次审计';
