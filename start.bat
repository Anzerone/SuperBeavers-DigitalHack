@echo off
echo === Starting Complaint Classifier ===
echo.

echo [1/3] PostgreSQL (Docker)...
docker compose -f docker-compose.yml up -d
timeout /t 3 /nobreak >nul

echo [2/3] Backend (FastAPI)...
start "Backend" cmd /c "cd /d %~dp0 && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload"
timeout /t 2 /nobreak >nul

echo [3/3] Frontend (Vite)...
start "Frontend" cmd /c "cd /d %~dp0\frontend && npm run dev"

echo.
echo === All services starting ===
echo Backend:  http://localhost:8001/docs
echo Frontend: http://localhost:5173
echo.
pause
