# 项目评审 Demo

<p align="right"><a href="README.md">English</a></p>

提供独立用户工作台和管理员工作台的项目、报告评审演示系统。这是 2026-10-20 准备的源码快照，不包含既有业务文件、数据库、登录会话、模型密钥及生成的评审结果。

## 功能

- 用户工作台：我的任务、项目组、单个项目、报告、关键词搜索、日期筛选与分页。
- 报告与附件自动分组；补充附件时可以选择所属报告。
- 评审对话支持选择、搜索报告。
- 管理员工作台：项目管理、工作记录、规则管理、模型设置和账号管理。
- 切换账号时进入登录界面。初始演示账号为 `user1` / `12345678`（用户）和 `manner` / `00000000`（管理员）。

## 本机启动

使用 Python 3.12 或 3.13，可用已有的 Conda Python 创建本地环境。在仓库根目录的 PowerShell 中执行：

```powershell
python -m venv .runtime
.\.runtime\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
.\start-demo.ps1
```

打开[用户工作台](http://127.0.0.1:8080/index.html)或[管理员工作台](http://127.0.0.1:8080/admin.html)。后端使用 8000 端口。启动脚本会保留这两个端口上已有的服务，切换源码版本前请停止旧实例。

容器启动可使用 `docker compose up --build -d`。容器运行数据保存在 `review-data` 卷中；本机运行数据保存在 `backend/data/`。

## 模型配置与使用边界

示例配置默认关闭外部模型调用。在本机 `.env` 中填写有效的接口密钥、地址和模型名称，启用 `LLM_ENABLED` 与 `LLM_RETRIEVAL_ENABLED` 后重启后端。使用兼容图像模型时再配置 `OCR_MODEL`。管理员工作台也提供模型设置。不要公开 `.env`。

旧版 Office 文档转换需要 LibreOffice；PDF 和图片提取可能需要[后端容器](backend/Dockerfile)安装的 OCR 与 PDF 外部工具。当前演示不包含模型重新训练。正式部署以及替换已公开的演示密码需要另行安排。

## 源码导航

| 区域 | 入口 |
| --- | --- |
| 用户与管理员页面 | [index.html](index.html)、[admin.html](admin.html) |
| 工作台交互 | [user-workspace.js](user-workspace.js)、[admin-workspace.js](admin-workspace.js)、[account-switcher.js](account-switcher.js) |
| 报告与附件流程 | [app.js](app.js)、[report-workflows.js](report-workflows.js) |
| 后端接口、账号和数据 | [backend/main.py](backend/main.py)、[backend/accounts.py](backend/accounts.py)、[backend/workbench.py](backend/workbench.py) |
| 规则和知识表 | [csv/](csv/) |
| 启动与配置 | [start-demo.ps1](start-demo.ps1)、[.env.example](.env.example)、[docker-compose.yml](docker-compose.yml) |
| 后端重点检查 | [test_batch_review.py](backend/test_batch_review.py)、[test_document_processing.py](backend/test_document_processing.py)、[test_visual_assets.py](backend/test_visual_assets.py) |
| 当前浏览器检查 | [check_user_workspace_ui.py](scripts/check_user_workspace_ui.py)、[check_admin_workspace.py](scripts/check_admin_workspace.py)、[check_login_routes.py](scripts/check_login_routes.py) |

## 验证

在仓库根目录安装开发依赖，运行后端重点检查：

```powershell
.\.runtime\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
Push-Location backend
..\.runtime\Scripts\python.exe -m unittest test_batch_review test_document_processing test_visual_assets
Pop-Location
.\.runtime\Scripts\python.exe -m playwright install chromium
.\.runtime\Scripts\python.exe scripts/check_user_workspace_ui.py
.\.runtime\Scripts\python.exe scripts/check_admin_workspace.py
.\.runtime\Scripts\python.exe scripts/check_login_routes.py
```

浏览器检查请逐个运行：它们使用 18000、18080 端口、临时测试数据库和模拟模型响应。检查产物写入被忽略的 `artifacts/`。部分历史检查依赖未附带的业务样例或基线产物；以上命令列出了当前可独立运行的检查。检查不评估真实外部模型的回答质量。
