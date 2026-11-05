@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
call "%~dp0python-run.cmd" -m unittest -q
if errorlevel 1 goto done
call "%~dp0python-run.cmd" -m bili_workbench.preflight
:done
pause
