# ★ 2026-06-17 云集智能视频创意站 - dev 启动器
#   全自动,无 Y/N 提示,只杀启动器进程,保留前后端服务和状态
#   旧启动器被杀后,前后端变孤儿继续跑 → 新启动器探测端口 → 接管

$ErrorActionPreference = 'Stop'

$ProjectDir = 'E:\软件开发\云集智能视频创意站'
$PythonExe = Join-Path $ProjectDir 'dev\data\.venv\Scripts\python.exe'
$AppDir = Join-Path $ProjectDir 'dev\app'
$LockFile = Join-Path $env:TEMP 'yunji_video_studio_dev.lock'

Write-Host '============================================================'
Write-Host '  Dev Test Mode'
Write-Host "  Python: $PythonExe"
Write-Host "  App:    $AppDir"
Write-Host '  Close this window to exit'
Write-Host '============================================================'

# 步骤 1: 杀旧启动器(只杀主进程,不连带前后端子进程)
if (Test-Path $LockFile) {
    Write-Host '[BAT] Found old launcher lock, killing old launcher (no /T)...'
    $oldPid = Get-Content $LockFile -ErrorAction SilentlyContinue
    if ($oldPid -match '^\d+$') {
        Write-Host "  Old launcher PID=$oldPid, running taskkill /F..."
        $null = & taskkill.exe /F /PID $oldPid 2>&1
    }
    Write-Host '  Waiting 2 seconds for children to stabilize as orphans...'
    Start-Sleep -Seconds 2
    Remove-Item -Force $LockFile -ErrorAction SilentlyContinue
}

# 步骤 2: 启动新的启动器(异步,新窗口显示)
Write-Host '[BAT] Starting new launcher...'
Set-Location $AppDir
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $PythonExe
$psi.Arguments = 'main.py'
$psi.WorkingDirectory = $AppDir
$psi.UseShellExecute = $true
$psi.CreateNoWindow = $false
$psi.WindowStyle = 'Normal'
[System.Diagnostics.Process]::Start($psi) | Out-Null
Write-Host '[BAT] New launcher started in a new window.'
