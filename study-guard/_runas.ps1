param([Parameter(Mandatory=$true)][string]$Target)
# 以管理员身份重新运行指定脚本，并在被拒绝时给出明确提示
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    $p = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($p) { $p.Source } else { "powershell.exe" }
    Start-Process -FilePath $exe -Verb RunAs -ErrorAction Stop `
        -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Target
} catch {
    Write-Host ""
    Write-Host "没有拿到管理员权限，脚本没有执行。" -ForegroundColor Red
    Write-Host ("系统提示：" + $_.Exception.Message)
    Write-Host ""
    Write-Host "请改用这个办法：在文件夹里右键 安装.cmd，选「以管理员身份运行」。" -ForegroundColor Yellow
    Write-Host "（只想要面板能打开的话，双击 打开面板.cmd 就行，那个不需要管理员权限。）"
    Read-Host "`n按回车退出"
}
