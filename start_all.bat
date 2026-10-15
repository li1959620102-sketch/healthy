@echo off
chcp 65001 >nul
title AI ZhiShen Platform - Start
cd /d "%~dp0"

echo ============================================
echo   AI ZhiShen Platform - one click start
echo ============================================
echo.

echo [1/4] Starting dependency containers (Redis 6380, MinIO 9000)...
docker start ai-health-redis ai-health-minio >nul 2>nul
timeout /t 6 /nobreak >nul

echo [2/4] Freeing port 8019 if held by a stale process...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8019" ^| findstr "LISTENING"') do (
  echo   killing stale PID %%P
  taskkill /PID %%P /F >nul 2>nul
)
timeout /t 2 /nobreak >nul

echo [3/4] Starting backend (port 8019)...
start "AI ZhiShen Backend (do not close)" /min "D:\conda\envs\ai_py311\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8019
timeout /t 14 /nobreak >nul

echo [4/4] Checking backend health...
curl -s -m 8 http://127.0.0.1:8019/api/v1/ready
echo.

echo Opening browser: http://127.0.0.1:8019/
start "" http://127.0.0.1:8019/

echo.
echo Done. The minimized "Backend" window must stay open.
echo To STOP: close that window.
echo.
pause
