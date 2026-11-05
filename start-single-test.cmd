@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
set "BIGFISH_ONESHOT_DIR=%LOCALAPPDATA%\BigFish\OneShot-20261103"
if exist "%BIGFISH_ONESHOT_DIR%" (echo 测试目录已存在。为避免重复测试，请不要重试，先核对结果。 & pause & exit /b 1)
call "%~dp0python-run.cmd" -m bili_workbench.single_test_setup --data-dir "%BIGFISH_ONESHOT_DIR%"
if errorlevel 1 (echo 准备失败，未调用模型。请保留窗口中的错误信息。 & pause & exit /b 1)
call "%~dp0python-run.cmd" -m bili_workbench.web --single-test --data-dir "%BIGFISH_ONESHOT_DIR%"
echo 工作台已停止；如果结果不确定，请核对账单，不要重试。
pause
