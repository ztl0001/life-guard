# 夜间应用锁 一键安装脚本（需要手机已开 USB 调试并授权本机）
#
# 用法：
#   .\install.ps1                       # 用脚本目录下 tools\platform-tools\adb.exe
#   .\install.ps1 -Adb "adb"            # 直接用 PATH 里的 adb
#   .\install.ps1 -Apk ".\NightAppLock.apk"

param(
    [string]$Adb = "",
    [string]$Apk = ""
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Adb) { $Adb = Join-Path $root "tools\platform-tools\adb.exe" }
if (-not $Apk) { $Apk = Join-Path $root "out\NightAppLock.apk" }
$comp = "com.codex.applock/com.codex.applock.AppLockAccessibilityService"

if (-not (Test-Path -LiteralPath $Apk)) {
    throw "找不到 APK：$Apk`n仓库里自带的安装包在 night-app-lock\NightAppLock.apk，或先跑 build.ps1 自己构建。"
}

& $Adb start-server | Out-Null
$state = & $Adb get-state 2>&1
if ($state -notmatch "device") {
    Write-Output "手机未连接或未授权 USB 调试，当前状态：$state"
    & $Adb devices -l
    exit 1
}

Write-Output "== 安装 APK =="
& $Adb install -r -d "$Apk"

Write-Output "== 开启无障碍服务 =="
$cur = (& $Adb shell settings get secure enabled_accessibility_services) -join ""
$cur = $cur.Trim()
if ($cur -eq "null" -or $cur -eq "") {
    $new = $comp
} elseif ($cur -notlike "*$comp*") {
    $new = "$cur`:$comp"
} else {
    $new = $cur
}
& $Adb shell settings put secure enabled_accessibility_services $new
& $Adb shell settings put secure accessibility_enabled 1

Write-Output "== 悬浮窗 + 电池白名单 =="
& $Adb shell appops set com.codex.applock SYSTEM_ALERT_WINDOW allow
& $Adb shell dumpsys deviceidle whitelist +com.codex.applock | Out-Null

Write-Output "== 校验 =="
& $Adb shell settings get secure enabled_accessibility_services
& $Adb shell dumpsys accessibility | Select-String -Pattern "applock|enabledServiceInfo" | Select-Object -First 6
& $Adb shell am start -n com.codex.applock/.MainActivity | Out-Null
Start-Sleep -Seconds 2
& $Adb shell am broadcast -a com.codex.applock.CMD --es cmd status | Out-Null
Start-Sleep -Seconds 1
& $Adb logcat -d -s AppLock:I | Select-Object -Last 8
