@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在安装"写字机打印"的运行环境（第一次大约 1-3 分钟，需要联网）……
echo.
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto nopy
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple || goto fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcut.ps1" || goto fail
echo.
echo 装好了：开始菜单里有"写字机打印"。
echo 还差一步：准备字库，见 fonts 文件夹里的"字库说明.txt"。
pause
exit /b 0

:nopy
echo 没找到 Python。请先到 https://www.python.org/downloads/ 下载安装 Python 3.12，
echo 安装时勾选"Add python.exe to PATH"，装好后再双击本文件。
pause
exit /b 1

:fail
echo.
echo 安装没有完成，上面有出错的原因。
pause
exit /b 1
