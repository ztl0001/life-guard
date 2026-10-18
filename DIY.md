# 按自己的想法改

这两个项目都是「我自己在用」的东西，不是通用产品——**所有默认值都是照我的作息定的**：08:50 前打卡、40 分钟一个学习块、游戏和 B 站共用 240 分钟娱乐额度、凌晨 01:30 锁手机。

这些数字对你大概率不合适。所以这份文档按「改动成本」从低到高，把能改的地方都列出来。**前三层不用写一行代码。**

先决条件：先按各自的 README 把它跑起来。

- 学习积分管家 → [study-guard/README.md](study-guard/README.md)
- 夜间应用锁 → [night-app-lock/README.md](night-app-lock/README.md)

---

## 目录

| 层级 | 改什么 | 成本 |
|---|---|---|
| [第 1 层](#第-1-层改-configjson) | 作息、分数、上限、桌宠外观 | 改 JSON，10 秒生效 |
| [第 2 层](#第-2-层改封锁范围) | 锁哪些游戏、锁哪些网站 | 改 JSON + 面板点击 |
| [第 3 层](#第-3-层换桌宠角色加动作) | 换角色、加动作、加台词 | 改 JSON + 导出素材 |
| [第 4 层](#第-4-层改面板) | 面板配色、加卡片、加按钮 | 改 HTML + 一个 action |
| [第 5 层](#第-5-层改规则引擎) | 乘数公式、新的奖惩 | 改 Python |
| [第 6 层](#第-6-层改应用锁) | 锁定时段、白名单、浮层 | 改 Java + 重新构建 |
| [红线](#红线哪些改动会毁掉这个东西) | 哪些改动会毁掉这个东西 | —— |

---

## 第 1 层：改 `config.json`

`study-guard/config.json` **保存后最多 10 秒自动生效，不用重启**。守护进程每一轮循环都会重新读它。

### 最常改的几个

| 字段 | 含义 | 默认 |
|---|---|---|
| `schedule.wake_target` | 工作打卡目标时间 | `08:30` |
| `schedule.wake_window_end` | 达标线，晚于此开始扣系数 | `08:50` |
| `ap.study_block_minutes` | 一个学习块多长 | `40` |
| `ap.study_reward` | 完成一个块给多少 AP | `4` |
| `ap.event_bonus` | 每完成一个块，当天乘数加多少 | `0.15` |
| `ap.multiplier_cap` | 乘数上限 | `1.0` |
| `game.ap_per_hour` | 每小时游戏/娱乐花多少 AP | `6` |
| `game.daily_cap_minutes` | 游戏每天上限 | `120` |
| `bilibili.daily_cap_minutes` | B 站每天上限 | `120` |
| `schedule.shutdown_enabled` | 到点自动关机总开关 | `true` |
| `schedule.nap_limit_minutes` | 午睡上限 | `40` |
| `schedule.nap_penalty` | 午睡超时扣多少 AP，`0` = 关掉 | `5` |
| `ap.allow_negative` | 罚款能不能把余额扣成负数（欠账） | `true` |

### 例子：把「游戏」改成「短视频」，上限砍到 60 分钟

用不着改代码——这套机制不关心你玩的是什么，它只管「**时间换点数**」。

```json
{
  "game": {
    "ap_per_hour": 10,
    "daily_cap_minutes": 60
  },
  "bilibili": {
    "enabled": true,
    "daily_cap_minutes": 60,
    "domains": ["www.douyin.com", "douyin.com", "v.douyin.com",
                "www.xiaohongshu.com", "xiaohongshu.com"]
  }
}
```

B 站的封锁就是把域名写进 hosts，所以换成抖音、小红书、微博都行（应用锁那部分有更彻底的手机端方案）。注意 `bilibili` 这个键名只是历史遗留，它锁的是 `domains` 里写的任何域名。

### 例子：我是夜猫子，把作息整体推后两小时

```json
{
  "schedule": {
    "wake_target": "10:30",
    "wake_window_end": "10:50",
    "screen_off_window_end": "03:00",
    "shutdown_window_end": "07:00"
  }
}
```

### 例子：换掉学习任务的分类

```json
{
  "study_tasks": [
    {"id": "ielts", "name": "雅思"},
    {"id": "side",  "name": "副业项目"},
    {"id": "read",  "name": "读书"}
  ]
}
```

面板上就会多出对应的按钮。`id` 随便取，只用来区分记录。

### 桌宠的外观也能在配置里改

| 字段 | 含义 | 默认 |
|---|---|---|
| `pet.enabled` | 要不要桌宠 | `true` |
| `pet.character_height` | 角色在屏幕上的高度（像素） | `227` |
| `pet.brightness` | 亮度倍数，`1.0` 是素材原样 | `1.1` |
| `pet.fps` | 动画帧率 | `12` |
| `pet.speech` | 说不说话 | `true` |
| `pet.speech_interval` | 碎碎念间隔秒数区间 | `[45, 110]` |
| `pet.roam` | 自己走不走动 | `true` |
| `pet.facing` | 朝向 `left` / `right` | `right` |
| `pet.daily_action_rotation` | 点击回应是否每天换一批 | `true` |
| `pet.exclude_actions` | 永不出现的动作名 | `[]` |

---

## 第 2 层：改封锁范围

封锁名单在 `config.json` 的 `protected` 数组里：

```json
{
  "protected": [
    {"path": "D:\\Games\\Elden Ring", "label": "老头环", "enabled": true},
    {"path": "D:\\Games\\Stardew",    "label": "星露谷", "enabled": false}
  ]
}
```

- `enabled: true` → 立刻锁死（改 NTFS 权限，双击直接被系统拒绝）
- `enabled: false` → 只是候选，面板上显示成未勾选

**不想手写路径**就用面板上的「**自动扫描新增**」：它会扫常见游戏盘，按 `UnityPlayer.dll` / `GameAssembly.dll` / `steam_api` / `.pak` / `.xp3` 之类的特征文件认出游戏目录，扫到的候选**默认全是不启用**，要你自己勾。

### 按进程名盯（对付「不从那个目录启动」的情况）

```json
{
  "game": {
    "blocked_processes": ["steam.exe", "steamwebhelper.exe", "game.exe"]
  }
}
```

只要游戏锁着，这些进程名一出现就被结束——不管它从哪儿启动的。Steam 的更新进程、覆盖层进程经常从缓存的别处拉起来，只锁目录会漏。

### 改变封锁的严格程度

`protected` 里的目录**一旦锁上，面板上取消不了**（那个框会变灰）。这是故意的：让「解除封锁」变成一个要手工改文件、要清醒决定的动作。

想减弱这个设计，可以：
- 把 `guard.py` 里面板的 `toggle_path` 分支改掉（不推荐，见[红线](#红线哪些改动会毁掉这个东西)）
- 或者更温和的做法：把某个目录的 `enabled` 改成 `false`，它就不再被锁，也不会显示成灰色

---

## 第 3 层：换桌宠角色、加动作

桌宠是独立的一块，改坏了也不影响积分和封锁。

### 3.1 加一段新动作

素材来自 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，它把 106 段动画都以 VP9 + alpha 的 webm 放在 `assets/webm/`。流程：

```bash
# 1. 把 webm 转成 360×270 的带 alpha APNG（仓库里没提交素材，得自己生成）
python tools/make_pet_anim.py --dsh-pet "D:\dsh-pet\dsh-pet" --only 我的新动作

# 2. 在 pet_anim/manifest.json 里登记
```

```json
{
  "my_action": {
    "source": "webm 文件名，不含扩展名",
    "file": "my_action.png",
    "frames": 120,
    "size": [360, 270]
  }
}
```

```python
# 3. 在 pet.py 里挂上触发条件
CLICK_CATALOG = [
    "click_happy", "click_wave", ..., 
    "my_action",          # ← 加进来，它就会参与每天的随机轮换
]

MOOD_LINES = {
    ...
    "my_action": ["新台词一", "新台词二"],   # ← 点击时随机说一句
}

TONE_COLOR = {
    ...
    "my_action": ("#7cf0b4", "#04301c"),   # ← 气泡底色、文字色
}
```

这三个表都在 `pet.py` 开头，很好找。加完重启桌宠即可。

**当天那批动作是预解码的**（点下去立刻有反应，不用等动画文件加载），所以一天里换的那 7 个动作启动时就定好了。想让它固定成老样子，把 `pet.daily_action_rotation` 设成 `false`。

> **本仓库只用了 26 段，[dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 那边一共 100 多段**，挑的时候直接看它的 [`assets/webm/`](https://github.com/PC2005-cloud/dsh-pet/tree/main/dsh-pet/assets/webm)，文件名就是动作的中文名。
>
> 想要的动作那边也没有？**去 dsh-pet 提**，别在这儿造轮子——它是这个角色和全部素材的源头，「AI 生成动画」的提示词和管线都在那个仓库里，在那边加一段，这个项目一条命令就能接进来。

### 3.2 换掉整个角色

桌宠不认「鲸鱼」这个概念，它只认：

- `fish.png`（静态兜底图）
- `pet_anim/manifest.json` + 对应的 APNG 文件

所以换成**任何**你喜欢的角色都行：

1. 把静态图存成 `study-guard/fish.png`
2. 把你自己的动画按同样的规格（建议 360×270、12fps、带透明通道）导出成 APNG，放进 `pet_anim/`
3. 改 `manifest.json` 的 `source` / `file` / `frames` / `size`
4. 把 `pet.py` 里 `MOOD_LINES` 的台词改成符合新角色的

要自己生成动画的话，dsh-pet 仓库里那套「提示词 → 视频 → 抠图 → 编码」的管线是可以直接复用的（`prompts/` 和 `scripts/`），它是 MIT 协议。

### 3.3 让它说你自己的话

桌宠的「状态相关台词」不在 `MOOD_LINES` 里，而是 `pet.py` 里根据当前状态拼的（还没打卡、乘数偏低、娱乐时间快结束……）。想加自己的规则，找那些调用 `say(...)` 的地方，照着写就行。

---

## 第 4 层：改面板

`dashboard.html` 是**单文件、零依赖、零外部资源**——没有 CDN、没有构建步骤、没有 `<script src>`。双击用浏览器打开就能看（虽然没数据），改起来没有任何心理负担。

### 加一个自己的按钮

面板上每个按钮最终都是往 `/api/action` POST 一个 JSON：

```javascript
const r = await fetch('/api/action', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({action: 'shower'})
});
```

所以要加一个「喝水」按钮，两步：

```html
<!-- 1. dashboard.html：照着「洗澡」那个按钮复制一份 -->
<button class="btn" onclick="act('water')">喝水 +1</button>
```

```python
# 2. guard.py 的 do_action() 里加一个分支
elif name == "water":
    amount = int(cfg["ap"].get("water_reward", 1) or 0)
    e.add_points(amount)
    msg = "喝水 +%d AP" % amount
```

> `do_action()` 里那一长串 `if name in (...)` / `elif name == ...` 就是所有动作的实现，照葫芦画瓢即可。记得在 `config.json` 里给新分数留个字段，别写死在代码里。

### 改配色

配色写在 `dashboard.html` 的 `<style>` 里（深色主题），直接改十六进制值。想要浅色主题，把背景和文字色对调即可，没有变量层要处理。

---

## 第 5 层：改规则引擎

`guard.py` 里跟「规则」有关的都是纯函数，改起来风险低：

| 想改什么 | 找哪里 |
|---|---|
| 乘数怎么算 | `multiplier_of()` |
| 罚多少、奖多少 | `Engine.wake_penalty()` / `nap_penalty()` / `add_points()` |
| 一天怎么归档、跨天怎么算 | `rollover()` |
| 什么时候自动关机 | `Engine.arm_shutdown()` |
| 兑换怎么结算、退多少 | 搜 `refund_unused` |

改完直接重启守护进程生效。

**一个建议**：改规则前先想清楚「这个规则是让绕过变难了还是变简单了」。比如把「起床超标扣一半」改成「扣 5 分」，看起来温和了，但迟到一小时的代价从「当天收入腰斩」变成「少喝一杯奶茶」——规则就失去意义了。

---

## 第 6 层：改应用锁

应用锁的时段和白名单**故意写死在代码里**（App 里改不了），所以 DIY 必须重新构建。构建流程见 [night-app-lock/README.md](night-app-lock/README.md)，不用 Gradle，一条 `build.ps1` 走完。

### 改锁定时段

```java
// night-app-lock/app/src/com/codex/applock/LockConfig.java
public static final int WINDOW_START = 1 * 60 + 30;  // 01:30  ← 改成你要的
public static final int WINDOW_END   = 6 * 60;       // 06:00
```

单位是「当天的第几分钟」，所以 `22 * 60` = 22:00。**时段跨午夜是支持的**（`isLockActive` 会处理 `start > end` 的情况），想锁 23:00–07:00 直接写就行。

### 改「每晚几次临时解锁」

```java
public static final int UNLOCK_MINUTES = 3;        // 每次解锁几分钟
public static final int UNLOCK_MAX_PER_DAY = 3;    // 每天最多几次
```

### 改放行名单

```java
// night-app-lock/app/src/com/codex/applock/Defaults.java
```

里面是各厂商短信 / 时钟 / 电话的包名表，还有一轮「按应用名关键词兜底」的匹配。想让它变成「**只锁某几个 App**」的白名单模式，思路是反过来的：

```java
// 在 AppLockAccessibilityService 判断前台应用的地方，
// 把「不在放行名单就拦」改成「在封锁名单里才拦」
```

这个改动不小（要加一套自己维护的封锁名单 UI），但结构上是清楚的：服务的拦截判断只有一处。

### 改浮层的样式和文案

`LockOverlay.java`：深色卡片 `0xFF15161C`、圆角 20dp、标题「🌙 夜间锁已开启」、三个快捷键（电话/短信/时钟）、黄色的解锁按钮 `0xFFFFC66D`。都是直接写在 `buildViews()` 里的，改字符串和颜色即可。

### 换个完全不同的用途

这套「无障碍服务盯前台应用 + 悬浮窗拦住 + 每日限次放行」的骨架是通用的，不只是「夜间锁」：

- 考试周：全天锁住娱乐 App
- 专注模式：工作时段锁住社交 App
- 家长控制：锁住指定几个 App（但请只在自己或已充分知情同意的设备上用）

改 `LockConfig` 里时段判定那一段就够了。

---

## 红线：哪些改动会毁掉这个东西

这不是洁癖，是这两个项目的全部价值所在。

**1. 不要在面板上加「一键解锁」按钮。**

这个项目防的就是「上头的那一刻」。那一刻你没有意志力，但你有鼠标。一个解锁按钮的存在，等于规则在那一刻完全不成立。

**2. 不要让「已经锁上的目录」能在面板上取消勾选。**

「想玩的时候顺手一点就把目录放出来」这条路必须堵死。要放出来可以——手工编辑 `config.json`，那个动作至少要你清醒地打开一次文件。

**3. 不要在 App 里恢复「关闭夜间锁」开关。**

应用锁 v1.1 特意删掉了三个入口：启用开关、时段设置、白名单编辑。「解锁 3 分钟、每天 3 次」是刻意留的唯一出口——完全不给出路的话，人会在别的地方找更野的路（比如把无障碍服务关掉）。

**4. 不要引入网络上报、账号、云同步。**

这两个项目都是纯本地的。`state.json`、`guard.log` 全在你自己的硬盘上。余额查询是唯二的外发请求（DeepSeek / 智谱官方接口），而且密钥不写进代码、日志里只记「有密钥 / 缺密钥」。

如果你想加的功能撞上了上面几条，欢迎先开个 Issue 聊聊——很可能有个不破坏设计的做法。

---

## 想动手了？

改完觉得不错，欢迎提 PR 回到仓库：[CONTRIBUTING.md](CONTRIBUTING.md)
