@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
call "%~dp0python-run.cmd" -m bili_workbench.safe_preview
echo 预览已停止。临时目录可在确认无需要的数据后自行清理。
pause
