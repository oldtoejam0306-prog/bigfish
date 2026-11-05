@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not defined BIGFISH_DATA_DIR set "BIGFISH_DATA_DIR=%LOCALAPPDATA%\BigFish\BiliWorkbench"
call "%~dp0python-run.cmd" -m bili_workbench.control --data-dir "%BIGFISH_DATA_DIR%"
pause
