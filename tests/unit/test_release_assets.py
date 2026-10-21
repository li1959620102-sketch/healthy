# -*- coding: utf-8 -*-
"""上线配置与运维脚本的静态契约测试。"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.unit


def test_production_dependencies_cover_new_runtime_features():
    deps = (ROOT / "requirements-prod.txt").read_text(encoding="utf-8").lower()
    for package in ("apscheduler", "faster-whisper", "ctranslate2", "av==", "edge-tts", "langgraph"):
        assert package in deps


def test_scheduler_and_asr_production_settings_are_passed():
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    for key in ("SCHEDULER_ENABLE", "SCHEDULER_TIMEZONE", "DELETION_PURGE_HOUR",
                "ALERT_EVAL_MINUTES", "ALERT_WEBHOOK_URL", "ASR_MODEL_DIR", "APP_VERSION", "APP_BUILD"):
        assert re.search(rf"^\s+{key}:", compose, re.M), key
    assert ":ro" in compose


def test_docker_image_contains_frontend_and_real_ready_probe():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "COPY frontend ./frontend" in dockerfile
    assert "/api/v1/ready" in dockerfile
    assert "PIP_INDEX_URL" in dockerfile
    assert "PIP_DEFAULT_TIMEOUT" in dockerfile
    assert not re.search(r"^frontend/?$", dockerignore, re.M)


def test_backup_restore_scripts_are_guarded_and_gzip_based():
    backup = (ROOT / "scripts" / "backup_database.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore_database.sh").read_text(encoding="utf-8")
    assert "--single-transaction" in backup
    assert "gzip.GzipFile" in backup
    assert "RESTORE_CONFIRM" in restore
    assert "TARGET_DATABASE" in restore
    assert "gzip.open" in restore


def test_release_and_rollback_scripts_validate_live_version():
    release = (ROOT / "scripts" / "release.sh").read_text(encoding="utf-8")
    rollback = (ROOT / "scripts" / "rollback.sh").read_text(encoding="utf-8")
    for script in (release, rollback):
        assert "/api/v1/version" in script
        assert "docker compose" in script
    assert "--no-build" in rollback


def test_https_proxy_config_disables_stream_buffering():
    nginx = (ROOT / "deploy" / "nginx" / "ai-health.conf").read_text(encoding="utf-8")
    assert "listen 443 ssl" in nginx
    assert "X-Forwarded-Proto https" in nginx
    assert "proxy_buffering off" in nginx
    assert "X-Accel-Buffering no" in nginx
