# 夜间应用锁 构建脚本
# 不依赖 Android Studio / Gradle：直接用 aapt2 + javac + d8 + zipalign + apksigner 手工出包。
#
# 用法（在 PowerShell 里）：
#   .\build.ps1
#   .\build.ps1 -Jdk "D:\jdk-17" -BuildTools "D:\sdk\build-tools\34.0.0" -AndroidJar "D:\sdk\platforms\android-35\android.jar"
#
# 默认在脚本目录下的 tools\ 里找工具链，布局见 README「自己构建」一节。

param(
    [string]$Jdk = "",
    [string]$BuildTools = "",
    [string]$AndroidJar = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $root "tools"
$app = Join-Path $root "app"
$out = Join-Path $root "out"
$build = Join-Path $root "build"
$keystoreDir = Join-Path $root "keystore"

if (-not $Jdk) { $Jdk = Join-Path $tools "jdk\jdk-17.0.2" }
if (-not $BuildTools) { $BuildTools = Join-Path $tools "bt\android-14" }
if (-not $AndroidJar) { $AndroidJar = Join-Path $tools "sdk\android-35\android.jar" }

foreach ($p in @($Jdk, $BuildTools, $AndroidJar)) {
    if (-not (Test-Path -LiteralPath $p)) {
        throw "找不到工具链路径：$p`n请先按 README 里的说明准备 JDK / build-tools / android.jar，或用参数指定。"
    }
}

$env:JAVA_HOME = $Jdk
$env:PATH = "$Jdk\bin;$env:PATH"

New-Item -ItemType Directory -Force -Path $out | Out-Null
New-Item -ItemType Directory -Force -Path $build | Out-Null
New-Item -ItemType Directory -Force -Path $keystoreDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $build "gen") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $build "classes") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $build "dex") | Out-Null

Write-Output "== aapt2 compile =="
& "$BuildTools\aapt2.exe" compile --dir "$app\res" -o "$build\res.zip"
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }

Write-Output "== aapt2 link =="
& "$BuildTools\aapt2.exe" link `
    -o "$build\base.apk" `
    -I $AndroidJar `
    --manifest "$app\AndroidManifest.xml" `
    -R "$build\res.zip" `
    --java "$build\gen" `
    --min-sdk-version 21 `
    --target-sdk-version 34 `
    --version-code 2 `
    --version-name 1.1 `
    --auto-add-overlay
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }

Write-Output "== javac =="
$srcs = @(Get-ChildItem -Recurse -Path (Join-Path $app "src") -Filter *.java | ForEach-Object { $_.FullName })
$gens = @(Get-ChildItem -Recurse -Path (Join-Path $build "gen") -Filter *.java | ForEach-Object { $_.FullName })
& "$Jdk\bin\javac.exe" -encoding UTF-8 -source 8 -target 8 -bootclasspath $AndroidJar -nowarn -d "$build\classes" @srcs @gens
if ($LASTEXITCODE -ne 0) { throw "javac failed" }

Write-Output "== d8 =="
$classFiles = @(Get-ChildItem -Recurse -Path (Join-Path $build "classes") -Filter *.class | ForEach-Object { $_.FullName })
& "$BuildTools\d8.bat" --min-api 21 --lib $AndroidJar --output "$build\dex" @classFiles
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Write-Output "== package dex into apk =="
Copy-Item "$build\base.apk" "$build\unaligned.apk" -Force
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open("$build\unaligned.apk", "Update")
try {
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, "$build\dex\classes.dex", "classes.dex", [System.IO.Compression.CompressionLevel]::Optimal)
} finally {
    $zip.Dispose()
}

Write-Output "== zipalign =="
& "$BuildTools\zipalign.exe" -f -p 4 "$build\unaligned.apk" "$build\aligned.apk"
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }

# 注意：这里生成的是「本机测试用」的签名密钥，密码写死在脚本里，只为了让你能自己出包。
# 公开仓库里不提供作者的 keystore，所以自己构建出来的包签名和仓库里自带的 APK 不一致，
# 想装到同一台手机上需要先卸载旧版本。
$ks = Join-Path $keystoreDir "applock.keystore"
if (-not (Test-Path $ks)) {
    Write-Output "== generate keystore =="
    & "$Jdk\bin\keytool.exe" -genkeypair -v -keystore $ks -alias applock -keyalg RSA -keysize 2048 `
        -validity 10950 -storepass android -keypass android `
        -dname "CN=AppLock, OU=AppLock, O=AppLock, L=Shanghai, ST=Shanghai, C=CN"
    if ($LASTEXITCODE -ne 0) { throw "keytool failed" }
}

Write-Output "== sign =="
& "$BuildTools\apksigner.bat" sign --ks $ks --ks-pass pass:android --key-pass pass:android `
    --v1-signing-enabled true --v2-signing-enabled true `
    --out "$out\NightAppLock.apk" "$build\aligned.apk"
if ($LASTEXITCODE -ne 0) { throw "apksigner sign failed" }

Write-Output "== verify =="
& "$BuildTools\apksigner.bat" verify --verbose "$out\NightAppLock.apk"
if ($LASTEXITCODE -ne 0) { throw "apksigner verify failed" }

Get-Item "$out\NightAppLock.apk" | Select-Object FullName, Length
