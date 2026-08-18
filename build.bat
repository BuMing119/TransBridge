@echo off
chcp 65001 >nul
setlocal

echo ============================================
echo   TransBridge 打包构建脚本
echo ============================================
echo.

:: 切换到项目根目录
cd /d "%~dp0"

:: ── 步骤 1：按锁文件同步构建环境 ─────────────────────────────
echo [1/4] 按 uv.lock 同步构建环境...
uv sync --frozen --group dev
if errorlevel 1 (
    echo [错误] 构建环境与 uv.lock 不一致。请先重建环境和锁文件。
    pause & exit /b 1
)
for /f "usebackq delims=" %%V in (`uv run --frozen python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
    echo [错误] 无法从 pyproject.toml 读取版本。
    pause & exit /b 1
)
echo 构建版本：%APP_VERSION%
echo.

:: ── 步骤 2：预下载 embedding 模型 ────────────────────────────
echo [2/4] 检查 embedding 模型...
if not exist "data\models\paraphrase-multilingual-MiniLM-L12-v2" (
    echo 正在下载模型（约 465 MB，仅首次需要）...
    uv run python -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2'); m.save('data/models/paraphrase-multilingual-MiniLM-L12-v2')"
    if errorlevel 1 (
        echo [错误] 模型下载失败，请检查网络。
        pause & exit /b 1
    )
) else (
    echo 模型已存在，跳过下载。
)
echo.

:: ── 步骤 3：PyInstaller 打包 ──────────────────────────────────
echo [3/4] 正在用 PyInstaller 打包应用...
uv run pyinstaller transbridge.spec --noconfirm
if errorlevel 1 (
    echo [错误] PyInstaller 打包失败，请查看上方日志。
    pause & exit /b 1
)
echo 打包完成，输出目录：dist\TransBridge\
echo.

:: ── 步骤 4：调用 Inno Setup 生成安装包 ───────────────────────
echo [4/4] 正在生成安装程序...

:: 尝试常见的 Inno Setup 安装路径
set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "%ISCC%"=="" (
    echo [警告] 未找到 Inno Setup 6，跳过安装包生成。
    echo 请先安装 Inno Setup 6：https://jrsoftware.org/isdl.php
    echo 安装后重新运行此脚本，或手动用 ISCC 编译 installer\setup.iss
    echo.
    echo 打包文件已生成于：dist\TransBridge\
    pause & exit /b 0
)

mkdir installer\output 2>nul
"%ISCC%" /DAppVersion=%APP_VERSION% installer\setup.iss
if errorlevel 1 (
    echo [错误] Inno Setup 编译失败，请查看上方日志。
    pause & exit /b 1
)

echo.
echo ============================================
echo   构建完成！
echo   安装包位置：installer\output\
echo ============================================
echo.
pause
