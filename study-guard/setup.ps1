# 安装入口：已经提权就直接装，没有就申请提权（只弹一次 UAC）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$id  = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir "install.ps1")
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir "_runas.ps1") `
        -Target (Join-Path $dir "install.ps1")
}
