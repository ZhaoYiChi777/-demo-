$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '未找到 .runtime 环境。请按 README_zh.md 中的本机启动步骤准备 Python 环境。'
}
$logRoot = Join-Path $projectRoot 'backend\data\logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $pythonPath -ArgumentList '-m uvicorn main:app --host 127.0.0.1 --port 8000' -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'workbench-api-out.log') -RedirectStandardError (Join-Path $logRoot 'workbench-api-error.log') | Out-Null
}
if (-not (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $pythonPath -ArgumentList 'scripts/serve_frontend.py --port 8080 --bind 127.0.0.1' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'workbench-web-out.log') -RedirectStandardError (Join-Path $logRoot 'workbench-web-error.log') | Out-Null
}
Write-Output '用户工作台：http://127.0.0.1:8080'
Write-Output '管理员工作台：http://127.0.0.1:8080/admin.html'
Write-Output '若服务刚启动，请等待数秒后打开。原有 Docker Compose 启动方式仍可使用。'
