@echo off
echo ============================================
echo   SmartQA Pro - 启动脚本
echo ============================================
echo.

REM 检查.env文件
if not exist ".env" (
    echo [警告] 未找到.env文件，正在从模板创建...
    copy .env.example .env
    echo [提示] 请编辑.env文件，填入你的API Key等配置
    echo.
)

REM 检查Python虚拟环境
if not exist "venv" (
    echo [1/3] 创建Python虚拟环境...
    python -m venv venv
    echo 虚拟环境创建成功！
) else (
    echo [1/3] 虚拟环境已存在，跳过创建
)

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 安装依赖
echo [2/3] 安装Python依赖...
pip install -r requirements.txt -q

REM 启动服务
echo [3/3] 启动FastAPI服务...
echo.
echo ============================================
echo   服务地址: http://localhost:8001
echo   API文档:  http://localhost:8001/docs
echo ============================================
echo.

uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
