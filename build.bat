@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] 检查 PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller 未安装，正在安装...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo 安装失败，请检查 Python 环境。
        pause
        exit /b 1
    )
)

echo [2/2] 开始打包...
python -m PyInstaller --noconfirm --onefile --windowed --uac-admin --name NVCacheCleaner NVCacheCleaner.py
if errorlevel 1 (
    echo 打包失败。
    pause
    exit /b 1
)

echo.
echo 打包完成：dist\NVCacheCleaner.exe
pause
