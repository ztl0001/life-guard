# 打开面板：如果守护进程没在跑，先把它启动起来，再打开浏览器
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$dir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$guard = Join-Path $dir "guard.py"
$url   = "http://127.0.0.1:8765"

$py = $null
foreach ($c in @("C:\ProgramData\anaconda3\pythonw.exe", "C:\ProgramData\anaconda3\python.exe")) {
    if (Test-Path -LiteralPath $c) { $py = $c; break }
}
if (-not $py) {
    $g = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($g) { $py = $g.Source }
}

function Test-Panel {
    try {
        $r = Invoke-WebRequest -Uri "$url/api/ping" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (Test-Panel) {
    Write-Host "守护进程正在运行。" -ForegroundColor Green
} else {
    if (-not $py) {
        Write-Host "找不到 Python，无法启动守护进程。" -ForegroundColor Red
        Read-Host "按回车退出"; exit 1
    }
    Write-Host "守护进程没在运行，正在启动..."
    Start-Process -FilePath $py -ArgumentList ('"' + $guard + '"') -WindowStyle Hidden
    $ok = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 400
        if (Test-Panel) { $ok = $true; break }
    }
    if ($ok) {
        Write-Host "已启动，游戏目录现在处于封锁状态。" -ForegroundColor Green
    } else {
        Write-Host "启动失败。请把下面这个文件的内容发给我：" -ForegroundColor Red
        Write-Host (Join-Path $dir "guard.log")
        Read-Host "按回车退出"; exit 1
    }
}

Start-Process $url
Start-Sleep -Seconds 1
