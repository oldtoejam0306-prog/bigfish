@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not defined BIGFISH_DATA_DIR set "BIGFISH_DATA_DIR=%LOCALAPPDATA%\BigFish\BiliWorkbench"
call "%~dp0python-run.cmd" -m bili_workbench.doctor --data-dir "%BIGFISH_DATA_DIR%" --probe-media --verify-source
pause
