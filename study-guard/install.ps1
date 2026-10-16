# 学习积分管家 安装脚本（需要管理员权限；安装.cmd 会自动判断是否已提权）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$guard = Join-Path $dir "guard.py"
$logFile = Join-Path $dir "install.log"

function Write-Log($msg, $color = "Gray") {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $msg -ForegroundColor $color
    try { Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8 } catch {}
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Log "=== 学习积分管家 安装开始 ===" "Cyan"
Write-Log ("管理员权限: " + (Test-Admin))
Write-Log ("目录: " + $dir)

# ---------- 1. 找 Python ----------
$py = $null
foreach ($c in @("C:\ProgramData\anaconda3\pythonw.exe", "C:\ProgramData\anaconda3\python.exe")) {
    if (Test-Path -LiteralPath $c) { $py = $c; break }
}
if (-not $py) {
    $g = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($g) { $py = $g.Source }
}
if (-not $py) {
    Write-Log "[1/6] 找不到 Python，安装中止。" "Red"
    Read-Host "按回车退出"; exit 1
}
Write-Log "[1/6] Python: $py" "Green"

# ---------- 2. 停掉旧进程 ----------
try {
    $old = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*guard.py*" })
    foreach ($p in $old) {
        Write-Log ("      结束旧进程 PID " + $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 800
    Write-Log "[2/6] 已清理旧守护进程（$($old.Count) 个）" "Green"
} catch {
    Write-Log "[2/6] 清理旧进程时出错：$($_.Exception.Message)" "Yellow"
}

# ---------- 3. 注册计划任务 ----------
try {
    $tr  = '"' + $py + '" "' + $guard + '"'
    $tr2 = $tr + " --ensure"
    $o1 = schtasks /Create /TN "StudyGuard" /TR $tr /SC ONLOGON /RL HIGHEST /F 2>&1
    $o2 = schtasks /Create /TN "StudyGuardWatchdog" /TR $tr2 /SC MINUTE /MO 5 /RL HIGHEST /F 2>&1
    Write-Log ("[3/6] " + ($o1 -join " "))
    Write-Log ("[3/6] " + ($o2 -join " "))
    if (($o1 -join "") -match "成功|SUCCESS") { Write-Log "[3/6] 开机自启任务已注册" "Green" }
    else { Write-Log "[3/6] 任务注册可能失败，请把 install.log 发给我" "Yellow" }
} catch {
    Write-Log "[3/6] 注册计划任务失败：$($_.Exception.Message)" "Yellow"
}

# ---------- 4. 关闭浏览器安全 DNS ----------
try {
    foreach ($k in @("HKLM:\SOFTWARE\Policies\Google\Chrome", "HKLM:\SOFTWARE\Policies\Microsoft\Edge")) {
        if (-not (Test-Path $k)) { New-Item -Path $k -Force -ErrorAction Stop | Out-Null }
        Set-ItemProperty -Path $k -Name "DnsOverHttpsMode" -Value "off" -ErrorAction Stop
    }
    Write-Log "[4/6] 已关闭 Chrome / Edge 的安全 DNS" "Green"
} catch {
    Write-Log "[4/6] 没有权限改浏览器策略（不影响目录封锁）：$($_.Exception.Message)" "Yellow"
}

# ---------- 5. 启动守护进程 ----------
try {
    Start-Process -FilePath $py -ArgumentList ('"' + $guard + '"') -WindowStyle Hidden
    Write-Log "[5/6] 守护进程已拉起，等待面板响应..."
    $ok = $false
    for ($i = 0; $i -lt 50; $i++) {
        Start-Sleep -Milliseconds 600
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/ping" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch {}
    }
    if ($ok) { Write-Log "[5/6] 面板已就绪" "Green" }
    else {
        Write-Log "[5/6] 面板 30 秒内没有响应" "Red"
        $gl = Join-Path $dir "guard.log"
        if (Test-Path -LiteralPath $gl) {
            Write-Log "      guard.log 最后几行：" "Yellow"
            Get-Content -LiteralPath $gl -Tail 8 | ForEach-Object { Write-Log ("      " + $_) }
        } else {
            Write-Log "      没有 guard.log，说明守护进程根本没启动起来" "Yellow"
        }
    }
} catch {
    Write-Log "[5/6] 启动守护进程失败：$($_.Exception.Message)" "Red"
}

# ---------- 6. 封锁自检 ----------
try {
    $s = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/state" -UseBasicParsing -TimeoutSec 10
    $j = $s.Content | ConvertFrom-Json
    $enabled = @($j.paths | Where-Object { $_.enabled }).Count
    $locked  = @($j.paths | Where-Object { $_.enabled -and $_.locked }).Count
    Write-Log "[6/6] 受保护目录 $enabled 个：权限锁死 $locked 个，进程拦截 $($enabled - $locked) 个" "Green"
    if ($j.bilibili.applied) { Write-Log "[6/6] B 站封锁已生效" "Green" }
    else { Write-Log "[6/6] B 站封锁未生效（hosts 写入失败）" "Yellow" }
} catch {
    Write-Log "[6/6] 自检失败：$($_.Exception.Message)" "Yellow"
}

Write-Log "=== 安装结束，日志保存在 install.log ===" "Cyan"
Start-Process "http://127.0.0.1:8765"
Read-Host "`n按回车关闭此窗口"
