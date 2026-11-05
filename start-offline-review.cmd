@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
call "%~dp0python-run.cmd" -E -B -X utf8 -m bili_workbench.offline_acceptance --preview --open-browser
set "BIGFISH_REVIEW_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %BIGFISH_REVIEW_EXIT_CODE%
