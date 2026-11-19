@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  一键启动演示环境（不依赖任何编辑器 / AI 工具）
rem
rem  为什么要这个脚本：
rem  之前前端是作为某个工具的后台任务启动的，工具一退出，
rem  前端（vite）会跟着被杀掉，页面就打不开了。
rem  这里用独立的 cmd 窗口启动，关掉什么工具都不影响。
rem ============================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo ============================================================
echo   启动 多Agent电商广告平台（本地演示）
echo ============================================================
echo.

rem ---------- 0. 检查端口是否已被占用 ----------
set "BUSY="
for %%P in (8000 5173) do (
    netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul 2>&1 && set "BUSY=1"
)
if defined BUSY (
    echo [提示] 8000 或 5173 端口已被占用，可能已经在运行了。
    echo        先跑 停止演示.bat 再启动，或直接打开 http://127.0.0.1:5173
    echo.
    pause
    exit /b 1
)

rem ---------- 1. 后端（独立窗口）----------
echo [1/3] 启动后端  http://127.0.0.1:8000
start "广告平台-后端" cmd /k "cd /d "%ROOT%backend" && set PYTHONIOENCODING=utf-8 && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info"

rem ---------- 2. 前端（独立窗口）----------
echo [2/3] 启动前端  http://127.0.0.1:5173
start "广告平台-前端" cmd /k "cd /d "%ROOT%frontend" && npm run dev"

rem ---------- 3. 等就绪并打开浏览器 ----------
echo [3/3] 等待服务就绪（约 20 秒，首次启动后端要探测依赖）...
set /a TRY=0
:wait
timeout /t 3 >nul
set /a TRY+=1
powershell -NoProfile -Command "try{(Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -TimeoutSec 3 -UseBasicParsing).StatusCode}catch{exit 1}" >nul 2>&1
if %errorlevel%==0 goto ready
if %TRY% GEQ 15 goto timeout
goto wait

:ready
echo.
echo   ✅ 已就绪，正在打开浏览器...
start "" "http://127.0.0.1:5173"
echo.
echo   账号：demo / demo123456
echo   关闭演示请双击 停止演示.bat（或直接关掉两个黑色窗口）
echo.
pause
exit /b 0

:timeout
echo.
echo   ⚠️ 45 秒内没等到前端就绪。请看那两个新开的窗口里的报错。
echo      常见原因：node_modules 没装（在 frontend 目录跑 npm install）
echo.
pause
exit /b 1
