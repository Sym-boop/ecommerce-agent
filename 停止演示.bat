@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  停止演示环境：按端口结束进程（8000 后端 / 5173 前端）
rem
rem  为什么按端口而不是按进程名：
rem  这台机器上可能还有别的 python / node 在跑，
rem  按名字杀会误伤；按监听端口杀只影响本项目的服务。
rem ============================================================

echo ============================================================
echo   停止 多Agent电商广告平台
echo ============================================================
echo.

set "FOUND="
for %%P in (8000 5173) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
        echo   端口 %%P → 结束 PID %%A
        taskkill /F /PID %%A >nul 2>&1
        set "FOUND=1"
    )
)

if not defined FOUND (
    echo   没有服务在运行（8000 / 5173 都空闲）
) else (
    echo.
    echo   ✅ 已停止
    echo   注意：如果还有两个黑色的命令窗口没关，手动关掉即可。
)

echo.
pause
