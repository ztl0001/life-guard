# night-app-lock 夜间应用锁

> 01:30 – 06:00 之间，除了**短信 / 时钟 / 电话**，其他应用一律打不开。

一个 29 KB 的 Android 应用。没有账号、没有网络请求、没有后台服务端——拦截逻辑全部跑在手机本地的无障碍服务里，**电脑关机、断网都不影响**。

---

## 它怎么工作

要点是让「半夜的你」没有把锁关掉的地方。

| 机制 | 说明 |
|---|---|
| **拦截** | 无障碍服务监听窗口变化，锁定时段内只要前台包名不在放行名单里，立刻 `GLOBAL_ACTION_HOME` 退回桌面 |
| **提示** | 同时弹一个悬浮窗，显示被拦的是哪个 App、距离解锁还有多久，并直接放三个按钮：电话 / 短信 / 时钟 |
| **放行** | 短信 / 时钟 / 电话**自动识别**（内置各厂商包名 + 按应用名关键词兜底）；桌面、输入法、来电界面、SystemUI 强制放行，否则锁上之后手机就变砖了 |
| **时段** | `01:30 – 06:00` **写死在代码里**（`LockConfig.java`），App 里改不了 |
| **无开关** | 没有「启用夜间锁」开关，没有时段设置，没有白名单编辑——三个入口都被删掉了 |
| **临时解锁** | 锁定时段内可以点「解锁 3 分钟」，**每个自然日最多 3 次**，用完按钮变灰 |
| **试运行** | 「立即测试：锁定 2 分钟」不用等到凌晨就能验证拦截是否真生效；这个入口只加锁、不解除锁定 |

> 为什么留「解锁 3 分钟」？完全不给出路的话，人会在别的地方找更野的路（比如提前把无障碍服务关掉）。留一个每天 3 次、3 分钟的口子，是刻意的取舍。

---

## 安装

### 方式一：直接装（推荐）

下载 [NightAppLock.apk](NightAppLock.apk)（29 KB，Android 5.0+），传到手机上安装。

首次安装若提示「禁止安装未知应用」，点 **设置 → 允许来自此来源**。

装完打开 App，按顺序做三件事：

1. **① 打开「无障碍」服务设置（必须）** → 在列表里找到「夜间应用锁」→ 打开开关 → 弹窗点允许。**这一步不做，锁完全不生效。**
2. **② 授予「悬浮窗」权限**（拦截提示要用）
3. **③ 关闭电池优化**（否则夜里可能被系统清掉）

### 方式二：adb 一键安装

手机开好 USB 调试并授权本机后：

```powershell
.\install.ps1                                   # 用 tools\platform-tools\adb.exe
.\install.ps1 -Adb "adb" -Apk ".\NightAppLock.apk"   # 或用 PATH 里的 adb
```

脚本会安装 APK、直接写好 `enabled_accessibility_services`（省得手动点）、授予悬浮窗权限、加进电池白名单，最后跑一遍校验并抓 `AppLock` 的日志。

### 国产 ROM 还要多做一步

华为 / 荣耀、小米 / 红米、OPPO / vivo 这些系统会清理后台服务，**不设置的话夜里锁可能失效**：

- 华为 / 荣耀：设置 → 应用 → 应用启动管理 → 夜间应用锁 → 关掉「自动管理」，把**自启动 / 关联启动 / 后台活动**都打开
- 小米 / 红米：省电策略选「无限制」，并打开「自启动」
- OPPO / vivo：允许后台运行，电池设置里允许后台高耗电

手机重启后也确认一下无障碍服务仍是开启状态。

---

## 验收（建议装完就做一次）

点「立即测试：锁定 2 分钟」，然后依次打开：

| 打开这个 | 期望结果 |
|---|---|
| 电话 / 拨号 | ✅ 正常进入 |
| 短信 / 信息 | ✅ 正常进入 |
| 时钟 | ✅ 正常进入 |
| 微信 / 浏览器 / 任意其他 App | ⛔ 立刻弹回桌面，出现锁定提示浮层 |

再回 App 首页看一眼「放行应用」那一行，确认短信、时钟、电话三个都在识别结果里。**如果哪一个没被认出来，凌晨打进来的电话就会被拦住**——这时候告诉我它的应用名，或自行在 `Defaults.java` 的包名/关键词表里补一条。

---

## 自己构建

仓库里带了成品 APK，一般不用自己编。想改代码再构建的话，这个项目**不用 Gradle、不用 Android Studio**，一条 `build.ps1` 走完 `aapt2 → javac → d8 → zipalign → apksigner`。

准备工具链，按下面的布局放进 `night-app-lock/tools/`：

```
night-app-lock/tools/
├── jdk/jdk-17.0.2/                JDK 17（javac、keytool）
├── bt/android-14/                 Android build-tools（aapt2、d8、zipalign、apksigner）
├── sdk/android-35/android.jar     Android 35 platform 的 android.jar
└── platform-tools/adb.exe         只有 install.ps1 用得到
```

然后用 Android Studio 的 SDK Manager 或 `sdkmanager` 下载对应版本即可；也可以不改目录，直接用参数指定：

```powershell
.\build.ps1 -Jdk "D:\jdk-17" `
            -BuildTools "D:\Android\sdk\build-tools\34.0.0" `
            -AndroidJar "D:\Android\sdk\platforms\android-35\android.jar"
```

产物在 `out\NightAppLock.apk`。

> **签名说明**：`build.ps1` 会在 `keystore\` 下自动生成一个测试密钥（密码写死在脚本里，仅为方便出包）。仓库**不包含**作者当初用的 keystore，所以你自己构建出来的 APK 和仓库里自带的那个签名不一致——装到同一台手机上需要先卸载旧版本。要发布的话请换成你自己的密钥。

---

## 目录结构

```
night-app-lock/
├── app/
│   ├── AndroidManifest.xml
│   ├── res/values/strings.xml
│   ├── res/xml/accessibility_config.xml
│   └── src/com/codex/applock/
│       ├── LockConfig.java                  时段、白名单、每日解锁次数
│       ├── AppLockAccessibilityService.java 无障碍服务：盯前台应用
│       ├── LockOverlay.java                 锁定提示浮层
│       ├── MainActivity.java                只读状态 + 试运行 + 临时解锁
│       ├── Defaults.java                    各厂商短信/时钟/电话包名与关键词
│       └── TestReceiver.java                adb 广播入口（status / 测试）
├── www/index.html         手机浏览器下载页（电脑起个静态服务就能装）
├── NightAppLock.apk       已签名成品，v1.1
├── build.ps1              手工构建脚本
└── install.ps1            adb 安装 + 自动授权
```

想用手机浏览器下载安装（手机和电脑同一热点时很方便）：

```powershell
cd night-app-lock
python -m http.server 8000        # 手机浏览器打开 http://<电脑IP>:8000/www/
```

---

## 边界与免责

- **只认「当前前台应用」**。它靠无障碍服务读窗口变化，不是内核级的进程拦截。理论上从通知栏直接拉起某些界面、或者分屏多窗口的场景可能有缝隙（锁定时段内会尝试收起通知栏来减少这种情况）。
- **卸载 App 就等于解锁**。这个项目没有做防卸载——它防的是「半夜上头」，不是防一个有耐心的对手。
- **仅在自己的手机上使用**。这是给自己上的锁，不是家长控制软件；不要装到别人的设备上。
- 修改系统无障碍设置需要用户手动授权，这是 Android 的安全边界，本项目没有、也不打算绕过它。

---

## License

[MIT](../LICENSE) © 2026 ztl0001
