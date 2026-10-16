# study-guard 学习积分管家

> 用行动点换游戏和 B 站时间。没兑换的时候，两者都被系统锁着。

一个纯本地的 Windows 自律工具：网页积分面板 + 常驻守护进程 + 桌面宠物。没有服务器、没有账号，全部数据在本地 `state.json` 里。

![面板](docs/dashboard-preview.png)

![桌宠](docs/pet-preview.png)

---

## 它到底做了什么

| 模块 | 行为 |
|---|---|
| **积分** | 完成一个 40 分钟学习块 +4 AP，洗澡 / 洗衣服 / 户外 / 关屏各 +2 AP，每天乘数由起床时间、午睡时长和完成的块数共同决定 |
| **封锁** | 游戏目录用 NTFS 权限锁死（双击直接被系统拒绝），B 站用 hosts 封锁 27 个域名；封锁独立于程序存在，删掉程序也不解除 |
| **兑换** | 花 AP 买 30 / 60 / 90 分钟游戏或 B 站时间，到点自动重新上锁；中途收掉按实际用量结算、余额按比例退回 |
| **守护** | 常驻进程每 10 秒核对一次封锁状态，被改动了自动纠正；注册开机自启 + 每 5 分钟看门狗 |
| **桌宠** | 桌面上一条会呼吸、会自己溜达、会说话的蓝鲸鱼；悬停显示行动点 / 当前任务 / 乘数 / 当天学习与娱乐时长 / deepseek 与 GLM 余额 |
| **到点关机** | 点「关屏」后 3 分钟自动关机；过了 01:00 还没点也一样，只是不加分 |

完整规则（包括每一条为什么这么设计）都在 **[说明.md](说明.md)** 里，400 多行，建议直接读那个。

---

## 快速开始

**前置条件**

- Windows 10 / 11
- Python 3.10+（脚本会自动在 `C:\ProgramData\anaconda3\` 和 PATH 里找 `pythonw.exe`）
- 管理员权限（要写 hosts 文件和改目录 ACL）

**安装**

```
右键 安装.cmd → 以管理员身份运行
```

它会依次：结束旧进程 → 注册两个计划任务（登录自启 + 5 分钟看门狗）→ 关闭 Chrome/Edge 的安全 DNS（不然 hosts 封锁会被 DoH 绕过）→ 拉起守护进程 → 自检封锁状态 → 打开面板 <http://127.0.0.1:8765>。

**日常使用**

| 脚本 | 作用 |
|---|---|
| `安装.cmd` | 安装、启动、注册开机自启 |
| `打开面板.cmd` | 打开面板；守护进程没在跑就自动拉起来 |
| `诊断.cmd` | 收集进程、端口、任务、hosts、接口和日志，生成 `诊断报告.txt` |

> 没有卸载脚本，也没有一键解锁——这是有意的，理由见 [说明.md](说明.md) 第七节。

---

## 目录结构

```
study-guard/
├── guard.py              守护进程 + HTTP API（约 2470 行）
├── pet.py                桌面宠物（约 1070 行，tkinter + APNG）
├── dashboard.html        网页面板，单文件、零依赖、零框架
├── config.json           全部可调项都在这（改完 10 秒内自动生效，不用重启）
├── pet_anim/
│   └── manifest.json     动画清单：键名 → dsh-pet 素材名 / 帧数 / 尺寸
├── tools/
│   └── make_pet_anim.py  从 dsh-pet 素材重建动画的小脚本
├── docs/                 截图
├── install.ps1 / open.ps1 / setup.ps1 / diag.ps1 / _runas.ps1
├── 安装.cmd / 打开面板.cmd / 诊断.cmd
└── 说明.md                完整使用手册
```

---

## 桌宠动画素材要自己生成一次

仓库里**没有** `pet_anim/*.png`——18 段动画一共约 239 MB，放进 Git 仓库不划算，所以只提交了清单文件。

不生成也能跑：`pet.py` 找不到动画时会用 `fish.png` 静态图兜底，功能都在，只是鱼不会动。

想让鱼动起来：

```bash
# 1. 取素材（MIT 协议，见文末致谢）
git clone https://github.com/PC2005-cloud/dsh-pet.git

# 2. 按 manifest.json 把 18 段 webm 解码成 360×270 的带 alpha APNG
python tools/make_pet_anim.py --dsh-pet "D:\dsh-pet\dsh-pet"

# 只想先试两段：
python tools/make_pet_anim.py --dsh-pet "D:\dsh-pet\dsh-pet" --only idle,click_happy
```

需要 `ffmpeg` 在 PATH 里（或用 `--ffmpeg` 指定）。全部生成约需几分钟，产物落在 `pet_anim/`，文件名与清单里 `file` 字段一致，程序直接就能认。

生成出来的动画合计约 239 MB，常驻内存约 150–200 MB。介意的话可以只生成常用的几段——`manifest.json` 里没有的键，程序会退回到静态图或跳过该动作。

---

## 配置

所有开关都在 `config.json`，**保存后最多 10 秒自动生效，不用重启**。几个最常改的：

| 字段 | 含义 | 默认 |
|---|---|---|
| `schedule.wake_window_end` | 工作打卡达标线 | `08:50` |
| `schedule.shutdown_enabled` | 到点自动关机总开关 | `true` |
| `game.daily_cap_minutes` | 游戏每天上限 | `120` |
| `bilibili.daily_cap_minutes` | B 站每天上限 | `120` |
| `game.ap_per_hour` | 每小时娱乐时间消耗的 AP | `6` |
| `ap.allow_negative` | 罚款是否可以把余额扣成负数（欠账） | `true` |
| `pet.enabled` | 是否显示桌面宠物 | `true` |
| `pet.character_height` | 角色在屏幕上的高度（像素） | `227` |
| `protected` | 封锁名单，每项 `{path, label, enabled}` | `[]` |

> 仓库里的 `config.json` 是**脱敏后的空白模板**：`user_sid` 留空（程序启动时自己探测），`protected` 是空数组。首次运行后在面板上勾选要锁的目录，或者直接编辑 `protected` 加 `{"path": "D:\\Games\\xxx", "label": "xxx", "enabled": true}`。

余额显示需要在 `D:\api_key.txt`（路径可改 `balance.key_file`）里放 API 密钥，按行匹配关键词：含 `deepseek` 的行给 DeepSeek 用，含 `智谱` / `glm` 的行给智谱用。**密钥不写进代码，也不写进日志**（日志里只记「有密钥 / 缺密钥」）。不需要余额功能就把 `balance.enabled` 改成 `false`。

---

## 已知限制

- **只支持 Windows**。ACL 封锁、hosts、`shutdown`、计划任务都是 Windows 的东西。
- **B 站封锁只在没开系统代理时自然生效**。程序会把 27 个域名加进 WinINET 的 `ProxyOverride` 例外列表并每 10 秒自愈，但代理软件重新启用系统代理时可能覆盖它。
- **到点只掐 Edge / Chrome 的网络进程**。 Firefox 之类的浏览器网络跑在主进程里，没有可单独重启的网络服务，到点只能靠 hosts 拦住新的解析。
- **Steam、手机游戏、别人的电脑管不到**。系统看不见它们，只有一个「记录计划外游戏」按钮让你如实补缴（1.5 倍价格）。
- **防不住蓄意绕过**。你是这台机器的管理员，真要研究怎么绕总能绕过去。这一条在 说明.md 第九节写得很直白。

---

## 素材来源

桌面宠物的角色形象与动画素材来自 [**PC2005-cloud/dsh-pet**](https://github.com/PC2005-cloud/dsh-pet)（MIT License, Copyright © 2026 PC2005-cloud）。

本项目取用了其中 18 段动画（`assets/webm/*.webm`，VP9 + alpha），用 ffmpeg 解码为 360×270 的 APNG 后播放，并做了约 1.1 倍的提亮（`pet.brightness`，因为素材比原始立绘暗约 10%）。这属于该项目的衍生物，同样遵循 MIT 协议，版权声明见仓库根目录的 [LICENSE](../LICENSE)。

---

## License

[MIT](../LICENSE) © 2026 ztl0001
