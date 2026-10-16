# 诊断：把所有相关信息收集到一个文件里
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $dir "诊断报告.txt"
$lines = New-Object System.Collections.Generic.List[string]

function Add-Line($s = "") { $lines.Add([string]$s) }
function Add-Cmd($title, $block) {
    Add-Line "----- $title -----"
    try { $r = & $block 2>&1; foreach ($x in $r) { Add-Line $x } }
    catch { Add-Line ("命令失败: " + $_.Exception.Message) }
    Add-Line ""
}

Add-Line ("诊断时间: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Add-Line ("目录: " + $dir)

Add-Cmd "管理员权限" {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}
Add-Cmd "Python" { @("C:\ProgramData\anaconda3\pythonw.exe", "C:\ProgramData\anaconda3\python.exe") |
    ForEach-Object { "$_  exists=$(Test-Path -LiteralPath $_)" } }
Add-Cmd "守护进程" { Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*guard.py*" } |
    Select-Object ProcessId, Name, CommandLine | Out-String }
Add-Cmd "端口 8765" { Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, State, OwningProcess | Out-String }
Add-Cmd "计划任务" { foreach ($t in @("StudyGuard", "StudyGuardWatchdog")) {
    "=== $t ==="; schtasks /Query /TN $t /FO LIST 2>&1 | Select-Object -First 6 } }
Add-Cmd "hosts 封锁" { Select-String -LiteralPath "C:\Windows\System32\drivers\etc\hosts" -Pattern "STUDY GUARD|bilibili" -ErrorAction SilentlyContinue |
    ForEach-Object { $_.Line } }
Add-Cmd "接口 /api/ping" { try { (Invoke-WebRequest "http://127.0.0.1:8765/api/ping" -UseBasicParsing -TimeoutSec 8).Content } catch { "失败: " + $_.Exception.Message } }
Add-Cmd "接口 /api/state（截断）" {
    try {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-WebRequest "http://127.0.0.1:8765/api/state" -UseBasicParsing -TimeoutSec 20
        $sw.Stop()
        "耗时 $($sw.ElapsedMilliseconds) ms, 长度 $($r.Content.Length)"
        $j = $r.Content | ConvertFrom-Json
        "locked=$($j.locked) elevated=$($j.elevated) acl_failed=$(@($j.acl_failed).Count)"
        $j.paths | ForEach-Object { "  {0,-52} enabled={1} locked={2}" -f $_.path, $_.enabled, $_.locked }
    } catch { "失败: " + $_.Exception.Message }
}
Add-Cmd "默认浏览器与代理" {
    "默认浏览器: " + (Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice" -ErrorAction SilentlyContinue).ProgId
    $p = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
    "代理启用: $($p.ProxyEnable)  服务器: $($p.ProxyServer)"
}
Add-Cmd "guard.log 最后 50 行" {
    if (Test-Path (Join-Path $dir "guard.log")) { Get-Content (Join-Path $dir "guard.log") -Tail 50 }
    else { "没有 guard.log" }
}
Add-Cmd "install.log 最后 50 行" {
    if (Test-Path (Join-Path $dir "install.log")) { Get-Content (Join-Path $dir "install.log") -Tail 50 }
    else { "没有 install.log（说明安装脚本没跑起来）" }
}

Set-Content -LiteralPath $out -Value $lines -Encoding UTF8
Write-Host ""
Write-Host "诊断报告已生成：$out" -ForegroundColor Green
Write-Host "把这个文件发给我即可。"
Start-Process notepad.exe $out
Read-Host "`n按回车退出"
