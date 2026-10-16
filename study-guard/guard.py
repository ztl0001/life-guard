#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学习积分管家 Study Guard

本地网页积分系统 + 游戏目录封锁 + B站封锁。
- 封锁：对受保护目录下发 DENY 读取/执行 ACL，被锁目录里的 exe 无法启动。
- 解锁：用行动点兑换娱乐时间，到点自动重新封锁。
- 网页：http://127.0.0.1:8765

运行：pythonw guard.py            正常启动
      pythonw guard.py --ensure   看门狗模式，若已在运行则直接退出

封锁没有一键解除的入口，也不会在面板上提供开关。想临时使用游戏或 B 站，
唯一的路是用行动点兑换；兑换到点后程序自动重新封锁。
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"
# 崩溃保险：state.json 旁边留一份"上一版"，再按天归档到 backups/
# （2026-10-13 那次硬重启把 state.json 写坏，守护进程静默按全新状态启动，
#   七天的历史和学习记录一度全部消失。这三样就是那次事故的补丁。）
STATE_PREV_PATH = BASE / "state.json.prev"
STATE_BACKUP_DIR = BASE / "backups"
STATE_BACKUP_KEEP = 14          # 归档保留最近 14 天
STATE_BACKUP_MIN_GAP = 60       # 当天归档最多每分钟刷新一次
_LAST_STATE_TEXT = {"value": None}
_LAST_BACKUP_AT = {"value": 0.0}
LOG_PATH = BASE / "guard.log"
DASH_PATH = BASE / "dashboard.html"
SCAN_PATH = BASE / "running.json"
HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts")
HOSTS_BEGIN = "# === STUDY GUARD BEGIN ==="
HOSTS_END = "# === STUDY GUARD END ==="
CREATE_NO_WINDOW = 0x08000000

# Study history is kept well past a year — it is a few hundred KB at most —
# but never without a bound, or state.json would grow forever.
STUDY_KEEP_DAYS = 400
STUDY_KEEP_ENTRIES = 6000

_LOCK = threading.RLock()
_LAST_HOSTS_STATE = {"applied": None, "note": "", "next_try": 0.0}


# ---------------------------------------------------------------- utilities
def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return json.loads(json.dumps(default))


def save_json(path, obj):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())      # 断电时尽量不留下半个文件
    if Path(path) == STATE_PATH:
        keep_state_prev()
    os.replace(tmp, path)


def keep_state_prev():
    """把当前这份 state.json 留成 state.json.prev，替换前调用。

    这是解析失败时唯一的救命副本：原子替换只保证"要么旧的要么新的"，
    但断电可能导致文件系统把新文件落成半截内容。
    """
    try:
        if not STATE_PATH.exists():
            return
        tmp = str(STATE_PREV_PATH) + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(STATE_PATH.read_bytes())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE_PREV_PATH)
    except Exception as exc:  # noqa: BLE001
        log("[警告] 备份 state.json.prev 失败：%r" % (exc,))


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=180)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def detect_sid():
    rc, out = run(["whoami", "/user"])
    if rc == 0:
        m = re.search(r"(S-1-5-21(?:-\d+){3,}-\d+)", out)
        if m:
            return m.group(1)
    return ""


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            ticks = ctypes.windll.kernel32.GetTickCount64()
            return max(0.0, (ticks - lii.dwTime) / 1000.0)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def is_elevated():
    """True only when running with an elevated (unfiltered) token."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- acl lock
def path_accessible(path):
    """True if the current user can list the folder."""
    try:
        os.listdir(path)
        return True
    except PermissionError:
        return False
    except OSError:
        return None  # missing / other


def acl_deny(path, sid):
    rc, out = run(["icacls", str(path), "/deny", "*%s:(OI)(CI)(RX)" % sid])
    return rc == 0, out


def acl_allow(path, sid):
    rc, out = run(["icacls", str(path), "/remove:d", "*%s" % sid])
    if rc != 0:
        rc2, out2 = run(["icacls", str(path), "/remove:d", sid])
        return rc2 == 0, out + out2
    return True, out


# ---------------------------------------------------------------- processes
def _norm(p):
    return str(p).lower().rstrip("\\/")


def running_under(paths):
    """Return [(pid, name, exe)] for processes whose exe lives under paths."""
    try:
        import psutil
    except ImportError:
        return []
    bases = [_norm(p) for p in paths if p]
    found = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            exe = proc.info.get("exe")
        except Exception:  # noqa: BLE001
            continue
        if not exe:
            continue
        low = _norm(exe)
        for b in bases:
            if low.startswith(b + "\\"):
                found.append((proc.info["pid"], proc.info.get("name") or "?", exe))
                break
    return found


def running_named(names):
    """Processes whose image name is in `names` (browsers, B站客户端, Steam 家族).

    返回 (pid, 名字, exe)；exe 读不到就留空——有的进程没权限看路径。
    """
    try:
        import psutil
    except ImportError:
        return []
    wanted = {str(n).lower() for n in names}
    found = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except Exception:  # noqa: BLE001
            continue
        if name in wanted:
            found.append((proc.info["pid"], proc.info.get("name") or "?",
                          proc.info.get("exe") or ""))
    return found


def kill_pids(pids):
    try:
        import psutil
    except ImportError:
        return 0
    n = 0
    for pid in pids:
        try:
            p = psutil.Process(pid)
            p.terminate()
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


# ---------------------------------------------------------------- hosts
def read_hosts():
    """Return (text, encoding) so the file can be written back unchanged."""
    try:
        raw = HOSTS_PATH.read_bytes()
    except OSError:
        return None, None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def write_hosts(text, encoding="utf-8"):
    try:
        HOSTS_PATH.write_text(text, encoding=encoding)
        return True, "ok"
    except PermissionError:
        return False, "需要管理员权限"
    except OSError as exc:
        return False, str(exc)


def strip_block(text):
    pattern = re.compile(
        re.escape(HOSTS_BEGIN) + r".*?" + re.escape(HOSTS_END) + r"\s*",
        re.DOTALL,
    )
    return pattern.sub("", text)


def proxy_bypass_list(cfg):
    """需要绕开系统代理的域名 = 封锁列表里的域名（外加它们的裸域名）。

    系统代理开着的时候，浏览器把请求交给代理，代理自己去解析域名——hosts 封锁
    形同虚设（2026-10-13 实测：原始 socket 直连被拒，走代理却到得了真站）。
    把域名塞进 WinINET 的"代理例外"，浏览器就会直连，于是重新走 hosts，被封。
    """
    out = []
    for d in (cfg.get("bilibili") or {}).get("domains") or []:
        d = str(d).strip().lower()
        if not d:
            continue
        out.append(d)
        parts = d.split(".")
        if len(parts) > 2:
            out.append(".".join(parts[-2:]))
    return list(dict.fromkeys(out))


def hosts_apply(domains):
    text, enc = read_hosts()
    if text is None:
        return False, "无法读取 hosts"
    body = "\n".join("127.0.0.1 %s" % d for d in domains)
    new = strip_block(text).rstrip("\n") + "\n\n" + HOSTS_BEGIN + "\n" + body + "\n" + HOSTS_END + "\n"
    if new == text:
        return True, "already"
    ok, note = write_hosts(new, enc)
    if ok:
        run(["ipconfig", "/flushdns"])
    return ok, note


def hosts_remove():
    text, enc = read_hosts()
    if text is None:
        return False, "无法读取 hosts"
    if HOSTS_BEGIN not in text:
        return True, "not present"
    ok, note = write_hosts(strip_block(text), enc)
    if ok:
        run(["ipconfig", "/flushdns"])
    return ok, note


def hosts_is_blocked(domains):
    text = (read_hosts()[0] or "")
    if HOSTS_BEGIN not in text:
        return False
    block = text.split(HOSTS_BEGIN, 1)[1]
    return all(("127.0.0.1 " + d) in block or ("0.0.0.0 " + d) in block for d in domains)


_HOSTS_WRITE = {"at": 0.0, "ok": None}


def hosts_can_write(force=False):
    """Can this process rewrite the hosts file? Needs an elevated token.

    Opening for append is the cheapest honest test: it exercises the same ACL
    that hosts_apply/hosts_remove need, without touching the contents.
    """
    now = time.time()
    if not force and _HOSTS_WRITE["ok"] is not None and now - _HOSTS_WRITE["at"] < 30:
        return _HOSTS_WRITE["ok"]
    try:
        with open(HOSTS_PATH, "a", encoding="utf-8"):
            ok = True
    except OSError:
        ok = False
    _HOSTS_WRITE.update({"at": now, "ok": ok})
    return ok


# ---------------------------------------------------------------- state
DEFAULT_CONFIG = {
    "port": 8765,
    "user_sid": "",
    "schedule": {
        "wake_target": "08:30",
        "wake_window_end": "08:50",
        # 起床超标（晚于 wake_window_end）就把手上的行动点砍一半，一天只扣一次。
        # 判定用的是系统实测的首次用机时间，不是点按钮的时间。
        "wake_penalty": True,
        "wake_penalty_divisor": 2,
        # 没有正余额可砍时（0 或欠账）改扣的固定金额
        "wake_penalty_debt": 10,
        "nap_limit_minutes": 40,
        # 午睡超过上限要掏行动点。一天只扣一次，扣到 0 为止（不会变成负数）。
        "nap_penalty": 5,
        "screen_off_before": "01:00",
        "screen_off_window_end": "01:00",
        # 关屏不再是"点一下就完事"：点下去就进入 3 分钟关机倒计时；
        # 到点还没点，同样 3 分钟后关机（只是不加分）。见 Engine.arm_shutdown。
        "shutdown_enabled": True,
        "shutdown_after_seconds": 180,
        "shutdown_window_end": "05:00",
        "shutdown_force": False,
        "streak_days": 7,
    },
    "ap": {
        "study_block_minutes": 40,
        "study_reward": 4,
        "outdoor_minutes": 20,
        "outdoor_reward": 2,
        "screen_off_reward": 2,
        "shower_reward": 2,
        "laundry_reward": 2,
        "no_overrun_reward": 1,
        "streak_reward": 10,
        "event_bonus": 0.1,
        "multiplier_cap": 1.0,
        # 关屏时的"完整一天"奖励：这一夜乘数满格、完成的事够多，额外加一笔。
        "screen_off_bonus": 5,
        "screen_off_bonus_multiplier": 1.0,
        "screen_off_bonus_blocks": 5,
        # 允许行动点变成负数（欠账）：罚款可以把余额压到 0 以下，之后的收入
        # 用来还债；还清之前买不了娱乐时间。设成 false 就退回"最多扣到 0"。
        "allow_negative": True,
    },
    "game": {
        "ap_per_hour": 6,
        # 点名要盯死的进程名（不管它从哪个目录启动）：游戏锁着的时候见到就结束。
        # 主要用于 Steam 这一族——整个客户端连同它的辅助进程。
        "blocked_processes": [
            "steam.exe", "steamwebhelper.exe", "steamservice.exe",
            "gameoverlayui.exe", "steamerrorreporter.exe",
        ],
        # 计划外游戏（系统管不到的那些，比如 Steam、手机）：按小时单价的这个倍数扣分。
        # 1.5 倍 = 9 AP/小时 = 每 10 分钟 1.5 AP。
        "unplanned_multiplier": 1.5,
        "steps_minutes": [30, 60, 90],
        "daily_cap_minutes": 120,
        "refund_unused": True,
        "warning_seconds": 180,
        "entertainment_processes": [
            "msedge.exe", "chrome.exe", "firefox.exe", "quark.exe",
            "bilibili.exe", "哔哩哔哩.exe",
        ],
    },
    "bilibili": {
        "enabled": True,
        "daily_cap_minutes": 120,
        "note": "默认封锁，只有兑换之后才临时打开",
        "domains": [
            "www.bilibili.com", "bilibili.com", "m.bilibili.com",
            "api.bilibili.com", "live.bilibili.com", "space.bilibili.com",
            "t.bilibili.com", "passport.bilibili.com", "b23.tv",
            "www.bilibili.tv", "bilibili.tv",
        ],
    },
    "rewards": [
        {"id": "outing", "name": "出门半天，市内或近郊", "cost": 5},
        {"id": "food", "name": "奶茶或一顿好吃的", "cost": 3},
        {"id": "movie", "name": "一场电影或一集剧", "cost": 4},
        {"id": "custom", "name": "自定义奖励", "cost": 10},
    ],
    "study_tasks": [
        {"id": "agent", "name": "Agent 开发"},
        {"id": "thesis", "name": "毕业论文设计"},
        {"id": "other", "name": "其他"},
    ],
    "pet": {
        "enabled": True,
        "image": "fish.png",
        "anim_dir": "pet_anim",
        "character_height": 227,
        "brightness": 1.1,
        "fps": 12,
        "speech": True,
        "speech_interval": [45, 110],
        "idle_action_interval": [50, 120],
        "roam": True,
        "facing": "right",
        "key_color": "#ff00ff",
        "hide_minutes": 30,
    },
    "protected": [],
}

DEFAULT_STATE = {
    "date": "",
    "ap": 0,
    "ap_today": 0,
    "study_minutes_today": 0,
    "fun_minutes_today": {"game": 0, "bilibili": 0},
    "unplanned_minutes_today": 0,
    "study_blocks_today": 0,
    "earned_events_today": 0,
    "study_task": "agent",
    "study_log": [],
    "study_days": {},
    "pet_hidden_until": 0,
    "wake_time": None,
    "wake_detected": None,
    "wake_penalty_done": False,
    "nap_minutes": None,
    "nap_penalty_done": False,
    "outdoor_done": False,
    "shower_done": False,
    "laundry_done": False,
    "screen_off_done": "",
    "shutdown_at": 0,
    "shutdown_reason": None,
    "shutdown_retries": 0,
    "no_overrun_awarded": False,
    "study_active": None,
    "unlock_until": 0,
    "session": None,
    "streak": 0,
    "talismans": 0,
    "talisman_used_on": None,
    "history": {},
    "rewards_bought": [],
    "blocked_attempts": 0,
}


def load_config():
    cfg = load_json(CFG_PATH, DEFAULT_CONFIG)
    for key, val in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = val
        elif isinstance(val, dict) and isinstance(cfg.get(key), dict):
            for k2, v2 in val.items():
                cfg[key].setdefault(k2, v2)
    if not cfg.get("user_sid"):
        cfg["user_sid"] = detect_sid()
        save_json(CFG_PATH, cfg)
    return cfg


def rebuild_study_days(entries):
    """Roll a flat block list up into {date: {minutes, blocks, ap, tasks}}."""
    days = {}
    for item in entries or []:
        day = str(item.get("date") or "")
        if not day:
            continue
        rec = days.setdefault(day, {"minutes": 0, "blocks": 0, "ap": 0, "tasks": {}})
        task = item.get("task") or "other"
        minutes = int(item.get("minutes") or 0)
        rec["minutes"] += minutes
        rec["blocks"] += 1
        rec["ap"] += int(item.get("ap") or 0)
        rec["tasks"][task] = int(rec["tasks"].get(task) or 0) + minutes
    return days


def _read_state_file(path):
    """严格读状态：文件缺失、内容坏掉、或者不是个正常状态对象，都抛异常。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not data:
        raise ValueError("内容不是有效的状态对象")
    return data


def recovery_candidates():
    """可以拿来救场的副本，按"最后修改时间新→旧"排序（只认今天的归档）。"""
    cands = [(STATE_PREV_PATH, "state.json.prev"),
             (STATE_BACKUP_DIR / ("state-%s.json" % today_str()),
              "backups/state-%s.json" % today_str())]
    alive = [(p, label) for p, label in cands if p.exists()]
    alive.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
    return alive


def load_state():
    """读状态。坏了先退回 state.json.prev，再不行才按全新状态启动并写日志报警。

    老实现是 load_json(STATE_PATH, DEFAULT_STATE)：解析失败就静默返回默认值，
    于是"文件被写坏"和"今天刚开始"看起来一模一样 —— 2026-10-13 那次重启
    就是这样把七天的历史和当天的记录全变成空的，还额外按起床超标扣了 10 分。
    """
    st, source = None, "state.json"
    try:
        st = _read_state_file(STATE_PATH)
    except Exception as exc:  # noqa: BLE001
        log("[严重] state.json 读取失败：%r" % (exc,))
    if st is None:
        # state.json.prev 只落后一个版本，而当天的归档最多落后一分钟，
        # 所以两份都试，取时间更新的那份，尽量少丢。
        for path, label in recovery_candidates():
            try:
                st = _read_state_file(path)
                source = label
                log("[恢复] state.json 不可用，已从 %s 恢复状态（%s）"
                    % (label, datetime.fromtimestamp(path.stat().st_mtime)
                       .strftime("%m-%d %H:%M:%S")))
                break
            except Exception as exc:  # noqa: BLE001
                log("[严重] %s 也不可用：%r" % (label, exc))
    if st is None:
        st = json.loads(json.dumps(DEFAULT_STATE))
        source = "fresh"
        log("[严重] 两个状态文件都读不出来，已按全新状态启动 —— 这不是正常的新一天，"
            "请去 backups 目录或 state.json.prev 找最近的备份")
    else:
        # migrate the old per-game counter to the shared entertainment counter
        if "fun_minutes_today" not in st and "game_minutes_today" in st:
            st["fun_minutes_today"] = st.get("game_minutes_today") or 0
        for key, val in DEFAULT_STATE.items():
            st.setdefault(key, val)
        normalize_fun(st)
        # older files only had the flat block list; rebuild the per-day summary
        if not st.get("study_days"):
            st["study_days"] = rebuild_study_days(st.get("study_log") or [])
        # screen_off_done used to be a daily bool; it now holds the night's date
        if not isinstance(st.get("screen_off_done"), str):
            st["screen_off_done"] = ""
    st["_state_source"] = source
    st["_state_reset_at"] = (datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                             if source == "fresh" else "")
    if source != "state.json":
        try:
            save_state(st, force=True)
            log("[恢复] 已把这份状态写回 state.json（来源：%s）" % source)
        except Exception as exc:  # noqa: BLE001
            log("[警告] 写回恢复出来的状态失败：%r" % (exc,))
    return st


def save_state(st, force=False):
    """写状态文件。

    tick 循环每 5 秒就调一次，内容没变就直接返回 —— 省磁盘写入，也让
    state.json.prev 真正代表"上一个不同的版本"而不是几十秒前的同一份内容。
    """
    text = json.dumps(st, ensure_ascii=False, indent=2)
    if (not force and text == _LAST_STATE_TEXT["value"]
            and STATE_PATH.exists()):
        return
    save_json(STATE_PATH, st)
    _LAST_STATE_TEXT["value"] = text
    archive_state(st)


def archive_state(st):
    """按天归档到 backups/state-<日期>.json，只留最近 STATE_BACKUP_KEEP 天。

    同一天最多每分钟刷新一次，所以跨天之后前一天的归档就是"那天结束时"的样子：
    再遇见崩溃，最多丢当天，不会连前面几天一起消失。
    """
    try:
        day = st.get("date") or today_str()
        target = STATE_BACKUP_DIR / ("state-%s.json" % day)
        now = time.time()
        if target.exists() and now - _LAST_BACKUP_AT["value"] < STATE_BACKUP_MIN_GAP:
            return
        STATE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(target) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        _LAST_BACKUP_AT["value"] = now
        prune_state_backups()
    except Exception as exc:  # noqa: BLE001
        log("[警告] 归档状态失败：%r" % (exc,))


def prune_state_backups():
    """只保留最近 STATE_BACKUP_KEEP 份，多出来的一份一份删（不做批量删除）。"""
    try:
        files = sorted(STATE_BACKUP_DIR.glob("state-*.json"))
        for old in files[:-STATE_BACKUP_KEEP]:
            try:
                old.unlink()
                log("[归档] 清理旧备份 %s" % old.name)
            except OSError as exc:
                log("[警告] 删除旧备份 %s 失败：%r" % (old.name, exc))
    except Exception as exc:  # noqa: BLE001
        log("[警告] 清理旧备份失败：%r" % (exc,))


# ------------------------------------------------------- entertainment quotas
# Game and B站 used to share one daily allowance; they now have their own
# counters, so every read/write goes through these three helpers.
FUN_KINDS = ("game", "bilibili")
FUN_LABEL = {"game": "游戏", "bilibili": "B 站"}


def fun_used(st, kind):
    """Minutes of this kind already spent today."""
    raw = st.get("fun_minutes_today")
    if isinstance(raw, dict):
        return max(0, int(raw.get(kind) or 0))
    # an old state file kept a single shared counter; the game side inherits it
    return max(0, int(raw or 0)) if kind == "game" else 0


def fun_add(st, kind, minutes):
    bucket = {k: fun_used(st, k) for k in FUN_KINDS}
    bucket[kind] = bucket.get(kind, 0) + int(minutes)
    st["fun_minutes_today"] = bucket
    return bucket[kind]


def normalize_fun(st):
    """Force the counter into its {game, bilibili} shape, migrating old files."""
    st["fun_minutes_today"] = {k: fun_used(st, k) for k in FUN_KINDS}
    return st["fun_minutes_today"]


def fun_cap(cfg, kind):
    """Daily allowance for one kind; B站 falls back to the game setting."""
    if kind == "bilibili":
        return int(cfg["bilibili"].get("daily_cap_minutes")
                   or cfg["game"]["daily_cap_minutes"])
    return int(cfg["game"]["daily_cap_minutes"])


# ---------------------------------------------------------------- rules
def today_str():
    return date.today().isoformat()


def at_time(day, hhmm):
    h, m = [int(x) for x in hhmm.split(":")]
    return datetime(day.year, day.month, day.day, h, m)


def clock_minutes(hhmm):
    h, m = [int(x) for x in str(hhmm).split(":")]
    return h * 60 + m


def within_evening(now_hhmm, limit_hhmm, start_hhmm="18:00"):
    """Is `now` inside the evening window that ends at `limit`?

    A limit such as 01:00 sits after midnight, so both sides are shifted by a
    day when needed; otherwise 23:45 would be judged later than 01:00.
    """
    now = clock_minutes(now_hhmm)
    limit = clock_minutes(limit_hhmm)
    start = clock_minutes(start_hhmm)
    if limit < start:
        limit += 24 * 60
        if now < start:
            now += 24 * 60
    return start <= now <= limit


def night_key(when=None):
    """Which night a 关屏 belongs to — the date of the evening it started.

    The screen-off window runs 18:00 → 01:00, so it straddles midnight. Keying
    it to the calendar day would answer wrong twice a night: clicking at 23:50
    and again at 00:30 is one night (one reward), while 00:30 and the next
    23:50 are two different nights.
    """
    now = when or datetime.now()
    if now.hour < 12:
        return (now.date() - timedelta(days=1)).isoformat()
    return now.date().isoformat()


def rollover(st, cfg):
    """Archive the finished day and reset the daily counters."""
    t = today_str()
    if st["date"] == t:
        return st
    if st["date"]:
        g_used, b_used = fun_used(st, "game"), fun_used(st, "bilibili")
        ok = bool(st.get("wake_time")) and multiplier_of(st, cfg, st["date"]) >= 1.0
        if st.get("talisman_used_on") == st["date"]:
            ok = True
        st["history"][st["date"]] = {
            "study": st.get("study_minutes_today", 0),
            "game": g_used,
            "bili": b_used,
            "fun": g_used + b_used,
            "unplanned": int(st.get("unplanned_minutes_today") or 0),
            # 关屏跨午夜，所以要按"这一夜"取数就得把当天的节奏和学习块数存下来
            "rhythm": rhythm_base(st, cfg, st["date"]),
            "blocks": int(st.get("earned_events_today") or 0),
            "ap": st.get("ap_today", 0),
            "wake": st.get("wake_time"),
            "ok": ok,
        }
        st["streak"] = st.get("streak", 0) + 1 if ok else 0
        days = cfg["schedule"]["streak_days"]
        if ok and st["streak"] and st["streak"] % days == 0:
            st["ap"] += cfg["ap"]["streak_reward"]
            st["talismans"] = st.get("talismans", 0) + 1
            log("连续 %d 天锚点达成，奖励 %d AP 和 1 张护身符" %
                (st["streak"], cfg["ap"]["streak_reward"]))
    # 跨天时不要把正在计时的学习块丢掉：继续计时，到点算新的一天完成。
    # 2026-10-14 00:00 就是这样丢掉了一个已经累计 36 分钟的学习块——
    # 计时被清空、也没留下任何记录，用户看到的就是"未完成的学习块消失了"。
    carried = st.get("study_active")
    if carried:
        carried["last_tick"] = time.time()   # 跨天那一瞬间不计入，也不补算
        carried["carried_from"] = st["date"]
    cutoff = (date.today() - timedelta(days=60)).isoformat()
    st["history"] = {k: v for k, v in st["history"].items() if k >= cutoff}
    st.update({
        "date": t, "ap_today": 0, "study_minutes_today": 0,
        "fun_minutes_today": {"game": 0, "bilibili": 0},
        "unplanned_minutes_today": 0,
        "study_blocks_today": 0,
        "earned_events_today": 0,
        "wake_time": None, "wake_detected": None, "nap_minutes": None,
        "wake_penalty_done": False,
        "nap_penalty_done": False,
        "outdoor_done": False, "shower_done": False, "laundry_done": False,
        # 注意：screen_off_done 不在这里重置 —— 它存的是"夜晚"的日期，
        # 而那一夜正好跨过午夜，重置掉等于让 00:30 又能重领一次。
        "shutdown_at": 0, "shutdown_reason": None, "shutdown_retries": 0,
        "no_overrun_awarded": False, "study_active": carried,
        "talisman_used_on": None, "rewards_bought": [],
    })
    if carried:
        log("跨天：保留进行中的学习块（%s，已累计 %d 分钟），继续计时"
            % (carried.get("task") or "?",
               int(float(carried.get("active_seconds") or 0) // 60)))
    save_state(st)
    return st


def rhythm_base(st, cfg, day=None):
    """The part of the multiplier that comes from sleep and naps (0.3 / 0.6 / 1.0)."""
    day = day or today_str()
    d = date.fromisoformat(day)
    if st.get("talisman_used_on") == day:
        return 1.0
    sched = cfg["schedule"]
    # 达标线用窗口结束时间；wake_target 只是显示用的目标点
    target = at_time(d, sched.get("wake_window_end") or sched["wake_target"])
    wake = st.get("wake_time")
    if wake:
        h, m = [int(x) for x in wake.split(":")]
        moment = datetime(d.year, d.month, d.day, h, m)
    else:
        if day != today_str():
            return 0.3
        moment = datetime.now()
    if moment <= target:
        mult = 1.0
    elif (moment - target).total_seconds() / 60.0 <= 60:
        mult = 0.6
    else:
        mult = 0.3
    nap = st.get("nap_minutes")
    if nap is not None and nap > cfg["schedule"]["nap_limit_minutes"]:
        mult = min(mult, 0.6)
    return mult


def multiplier_of(st, cfg, day=None):
    """Effective multiplier: rhythm plus 0.1 for every point-earning event today."""
    day = day or today_str()
    base = rhythm_base(st, cfg, day)
    if day != today_str():
        return base
    bonus = cfg["ap"].get("event_bonus", 0.1) * st.get("earned_events_today", 0)
    cap = cfg["ap"].get("multiplier_cap", 1.0)
    return round(min(cap, base + bonus), 4)


def nightly_multiplier(st, cfg):
    """这一夜结束时的乘数和学习块数（考虑关屏窗口跨午夜）。

    关屏的窗口是 18:00 → 01:00，而"当天学习块数"和乘数都在午夜归零。如果 00:30
    才点关屏，直接读当前值会看到 0 和 0，判定永远不成立。所以过了午夜就去
    history[前一晚] 里取归档：rhythm（睡眠那部分系数）+ 0.1 × 学习块数，再封顶。

    （归档字段早期叫 events，现在叫 blocks，两个都认。）
    """
    night = night_key()
    if night == today_str():
        return multiplier_of(st, cfg), int(st.get("earned_events_today") or 0)
    rec = (st.get("history") or {}).get(night)
    if not rec:
        return 0.0, 0
    base = float(rec.get("rhythm") or 0.0)
    events = int(rec.get("blocks", rec.get("events")) or 0)
    bonus = cfg["ap"].get("event_bonus", 0.1) * events
    cap = cfg["ap"].get("multiplier_cap", 1.0)
    return round(min(cap, base + bonus), 4), events


def award(st, cfg, base, reason, count_event=True):
    """加分。

    `count_event` 决定这次加分算不算进"完成的事"计数——而那个计数是乘数成长的
    唯一来源。现在只有学习块算数：洗澡、洗衣服、户外、关屏这些顺手一点的
    打卡照旧给分，但不再帮忙推乘数（不然睡晚了靠点四下按钮就能把系数挣回来）。
    """
    mult = multiplier_of(st, cfg)
    got = int(round(base * mult))
    if got < 1:
        got = 1 if base >= 1 else 0
    st["ap"] += got
    st["ap_today"] += got
    if count_event:
        st["earned_events_today"] = st.get("earned_events_today", 0) + 1
    log("+%d AP (基础 %d x 系数 %.2f) %s" % (got, base, mult, reason))
    return got, mult


def spend(st, amount):
    if st["ap"] < amount:
        return False
    st["ap"] -= amount
    return True


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self):
        self.cfg = load_config()
        self.st = load_state()
        self.paths = self._paths()
        self.acl_failed = []
        self._logged_fail = set()
        self._fail_backoff = {}
        self._running = []
        self._scan = {"games": [], "fun": []}
        self._running_at = 0.0
        self._hosts_ok = None
        self._hosts_at = 0.0
        self._enforce_at = 0.0
        self._scanner = None
        self._scanner_names = None
        self._scanner_blocked = None
        self._pet = None
        self._killed = {}
        self._overlay_pid = None
        self._cfg_mtime = self._cfg_mtime_now()

    def _cfg_mtime_now(self):
        try:
            return CFG_PATH.stat().st_mtime
        except OSError:
            return 0.0

    def _paths(self):
        out = []
        for item in self.cfg.get("protected", []):
            if isinstance(item, dict):
                out.append({
                    "path": item.get("path", ""),
                    "label": item.get("label") or Path(item.get("path", "")).name,
                    "enabled": bool(item.get("enabled", True)),
                })
            else:
                out.append({"path": str(item), "label": Path(str(item)).name,
                            "enabled": True})
        return out

    def active_paths(self):
        return [p["path"] for p in self.paths if p["enabled"] and p["path"]]

    def task_list(self):
        return [t for t in (self.cfg.get("study_tasks") or [])
                if isinstance(t, dict) and t.get("id")]

    def task_name(self, tid):
        for t in self.task_list():
            if t["id"] == tid:
                return t.get("name") or tid
        return tid or "其他"

    def tick_study(self, now=None):
        """Accrue effective study seconds.

        Two things stop the clock, and both are handled here: walking away for
        more than two minutes (counted by the OS idle timer) and pausing on
        purpose. A paused block also cannot auto-complete.
        """
        st = self.st
        sa = st.get("study_active")
        if not sa:
            return None
        now = time.time() if now is None else now
        last = sa.get("last_tick", now)
        delta = max(0.0, min(now - last, 10.0))
        if not sa.get("paused") and idle_seconds() < 120:
            sa["active_seconds"] = sa.get("active_seconds", 0) + delta
        sa["last_tick"] = now
        if sa.get("paused") or sa["active_seconds"] < sa["target_seconds"]:
            return None
        st["study_blocks_today"] += 1
        st["study_minutes_today"] += self.cfg["ap"]["study_block_minutes"]
        got, _ = award(st, self.cfg, self.cfg["ap"]["study_reward"], "学习块自动完成")
        task = sa.get("task")
        st["study_active"] = None
        self.log_study_block(task, self.cfg["ap"]["study_block_minutes"], got)
        log("学习块自动完成 +%d AP" % got)
        return got

    def log_study_block(self, task, minutes, got):
        """Append a finished study block, both flat and rolled up per day."""
        st = self.st
        day = today_str()
        tid = task or "other"
        entry = {
            "date": day,
            "task": tid,
            "name": self.task_name(tid),
            "minutes": int(minutes),
            "at": datetime.now().strftime("%H:%M"),
            "ap": int(got),
        }
        st.setdefault("study_log", []).append(entry)
        days = st.setdefault("study_days", {})
        rec = days.setdefault(day, {"minutes": 0, "blocks": 0, "ap": 0, "tasks": {}})
        rec["minutes"] = int(rec.get("minutes") or 0) + int(minutes)
        rec["blocks"] = int(rec.get("blocks") or 0) + 1
        rec["ap"] = int(rec.get("ap") or 0) + int(got)
        rec.setdefault("tasks", {})
        rec["tasks"][tid] = int(rec["tasks"].get(tid) or 0) + int(minutes)
        self.trim_study_history(st)
        log("学习块记录：%s %d 分钟 +%d AP" %
            (entry["name"], entry["minutes"], entry["ap"]))
        return entry

    def trim_study_history(self, st):
        """Drop records older than the retention window (and cap the count)."""
        cutoff = (date.today() - timedelta(days=STUDY_KEEP_DAYS)).isoformat()
        entries = [e for e in (st.get("study_log") or [])
                   if str(e.get("date") or "") >= cutoff]
        st["study_log"] = entries[-STUDY_KEEP_ENTRIES:]
        st["study_days"] = {k: v for k, v in (st.get("study_days") or {}).items()
                            if k >= cutoff}

    def reload(self):
        before = sorted(p["path"] for p in self.paths if p["enabled"])
        self.cfg = load_config()
        self.paths = self._paths()
        self._cfg_mtime = self._cfg_mtime_now()
        after = sorted(p["path"] for p in self.paths if p["enabled"])
        names = list(self.cfg["game"].get("entertainment_processes") or [])
        blocked = list(self.cfg["game"].get("blocked_processes") or [])
        # 扫描器只认它启动时拿到的那份清单（锁哪些目录、盯哪些进程名）。任一项
        # 变了都得重启它，否则被移出封锁范围的目录还被旧清单盯着——ACL 已经放开，
        # 进程却照杀。原实现只比"目录总数"，所以"取消勾选"根本不会触发重启。
        if (before != after or names != self._scanner_names
                or blocked != self._scanner_blocked):
            self.ensure_scanner(force=True)

    # -- locking -------------------------------------------------------
    def active_kind(self):
        """'game', 'bilibili', or None — whichever window is currently open."""
        if time.time() < float(self.st.get("unlock_until") or 0):
            return (self.st.get("session") or {}).get("kind")
        return None

    def game_locked(self):
        return self.active_kind() != "game"

    def bili_blocked(self):
        if not self.cfg["bilibili"].get("enabled", True):
            return False
        return self.active_kind() != "bilibili"

    def want_locked(self):
        return self.game_locked()

    def apply_acl(self, lock, only=None):
        """Apply or lift the folder lock.

        上锁只针对"封锁范围里"的目录；**解锁要扫全部目录**——一个目录被取消勾选
        （或手工改了 config）之后就不在范围内了，可它身上还留着以前锁上去的拒绝
        权限，不主动解掉的话"移除封锁"就是句空话。
        """
        sid = self.cfg["user_sid"]
        if only is not None:
            scope = list(only)
            want_locked = set(only) if lock else set()
        else:
            scope = [p["path"] for p in self.paths if p["path"]]
            want_locked = set(self.active_paths()) if lock else set()
        changed, failed = [], []
        for p in scope:
            accessible = path_accessible(p)
            if accessible is None:
                continue
            if p in want_locked and accessible:
                # A folder we already know we cannot lock is retried on a slow
                # timer instead of on every pass.
                if time.time() < self._fail_backoff.get(p, 0.0):
                    failed.append(p)
                    continue
                ok, out = acl_deny(p, sid)
                if ok and path_accessible(p) is False:
                    changed.append(p)
                    self._fail_backoff.pop(p, None)
                    self._logged_fail.discard(p)
                else:
                    failed.append(p)
                    # A folder only becomes lockable after the user runs the
                    # elevated installer, which restarts this process anyway.
                    # Retrying often just burns time while holding the lock.
                    self._fail_backoff[p] = time.time() + 900
                    if p not in self._logged_fail:
                        self._logged_fail.add(p)
                        last = (out or "").strip().splitlines()
                        log("无法用权限锁定 %s（%s）" %
                            (p, last[-1] if last else "未知原因"))
            elif p not in want_locked and accessible is False:
                ok, out = acl_allow(p, sid)
                if ok:
                    changed.append(p)
                    self._fail_backoff.pop(p, None)
                else:
                    log("解除锁定失败 %s：%s" % (p, (out or "").strip()))
        self.acl_failed = failed
        return changed

    def running_games(self):
        """正在跑的"游戏"：封锁目录里的进程 + 点名要盯的进程（Steam 那一族）。"""
        data = self.scan_data()
        found = [(g.get("pid"), g.get("name", "?"), g.get("exe", ""))
                 for g in data.get("games", [])]
        seen = {p[0] for p in found}
        for g in data.get("blocked", []):
            if g.get("pid") not in seen:
                found.append((g.get("pid"), g.get("name", "?"), g.get("exe", "")))
        return found

    def running_fun(self):
        return self.scan_data().get("fun", [])

    def scan_data(self):
        """Read the scanner's result file.

        Enumerating ~300 processes with psutil costs ~0.8 s of interpreter time,
        which starves the web server, so the scan lives in its own process and
        only the result file is read here.
        """
        if time.time() - self._running_at < 0.8:
            return self._scan
        self._running_at = time.time()
        try:
            data = json.loads(SCAN_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):  # older single-list format
                data = {"games": data, "fun": [], "blocked": []}
            data.setdefault("blocked", [])
        except (OSError, ValueError):
            data = {"games": [], "fun": [], "blocked": []}
        self._scan = data
        return data

    def ensure_scanner(self, force=False):
        paths = self.active_paths()
        if self._scanner is not None and self._scanner.poll() is None:
            if not force:
                return
            try:
                self._scanner.terminate()
            except Exception:  # noqa: BLE001
                pass
        if not paths:
            return
        try:
            payload = {
                "paths": paths,
                "names": self.cfg["game"].get("entertainment_processes") or [],
                "blocked": self.cfg["game"].get("blocked_processes") or [],
            }
            self._scanner = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--scan-loop",
                 json.dumps(payload, ensure_ascii=False)],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._scanner_names = list(payload["names"])
            self._scanner_blocked = list(payload["blocked"])
            log("进程扫描器已启动（PID %d，盯 %d 个目录）"
                % (self._scanner.pid, len(paths)))
        except Exception as exc:  # noqa: BLE001
            log("启动进程扫描器失败: %r" % exc)

    def ensure_pet(self, force=False):
        """Keep the desktop fish alive; skip while the user has tucked it away."""
        if not (self.cfg.get("pet") or {}).get("enabled", True):
            return
        if time.time() < float(self.st.get("pet_hidden_until") or 0):
            return
        if self._pet is not None and self._pet.poll() is None and not force:
            return
        if pet_already_running():
            return  # already on screen; nothing to do
        try:
            self._pet = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--pet"],
                creationflags=CREATE_NO_WINDOW)
            log("桌面宠物已启动（PID %d）" % self._pet.pid)
        except Exception as exc:  # noqa: BLE001
            log("启动桌面宠物失败: %r" % exc)

    def close_pet(self):
        p = self._pet
        self._pet = None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass

    def spawn_overlay(self, seconds, label):
        """Always-on-top countdown window for the last minutes of a session."""
        try:
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()),
                 "--overlay", str(int(seconds)), label],
                creationflags=CREATE_NO_WINDOW)
            self._overlay_pid = proc.pid
            if self.st.get("session") is not None:
                self.st["session"]["overlay_pid"] = proc.pid
            log("已弹出%s倒计时浮层（%d 秒）" % (label, seconds))
        except Exception as exc:  # noqa: BLE001
            log("弹出倒计时浮层失败: %r" % exc)

    def close_overlay(self):
        """Kill any countdown overlay windows that are still on screen."""
        killed = 0
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmd = " ".join(proc.info.get("cmdline") or [])
                except Exception:  # noqa: BLE001
                    continue
                if "--overlay" in cmd:
                    try:
                        proc.terminate()
                        killed += 1
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            log("关闭倒计时浮层失败: %r" % exc)
        self._overlay_pid = None
        if self.st.get("session") is not None:
            self.st["session"].pop("overlay_pid", None)
        if killed:
            log("已关闭 %d 个倒计时浮层" % killed)
        return killed

    def arm_shutdown(self, reason):
        """Start the countdown that ends with the machine going down.

        Two things reach this point and both get the same three-minute overlay:
        an early 关屏 click (reason "click", worth AP) and the deadline going by
        without one (reason "deadline", worth nothing). The OS shutdown is
        scheduled right here so it still happens if this process dies.
        """
        sched = self.cfg["schedule"]
        st = self.st
        delay = max(30, int(sched.get("shutdown_after_seconds", 180) or 180))
        st["shutdown_at"] = time.time() + delay
        st["shutdown_reason"] = reason
        if reason == "click":
            log("已打卡关屏，%d 秒后自动关机" % delay)
        else:
            log("过了 %s 还没点关屏，%d 秒后自动关机（今天不加分）"
                % (sched.get("screen_off_before", "01:00"), delay))
        self.spawn_overlay(delay, "关屏")
        cmd = ["shutdown", "/s", "/t", str(delay)]
        if sched.get("shutdown_force"):
            cmd.append("/f")
        cmd += ["/c", "关屏时间到了：电脑将在 %d 秒后关闭，先把手头的东西保存好。"
                % delay]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=10,
                                  creationflags=CREATE_NO_WINDOW)
        except Exception as exc:  # noqa: BLE001
            log("调用系统关机失败：%r" % exc)
            return 0
        if done.returncode != 0:
            err = (done.stderr or done.stdout or b"").decode("gbk", "replace")
            log("系统拒绝了这个关机请求（返回码 %d）：%s"
                % (done.returncode, err.strip()[:140]))
            return 0
        return delay

    def screen_off_watch(self):
        """Force the shutdown once the deadline has gone by without a click."""
        sched = self.cfg["schedule"]
        st = self.st
        if not sched.get("shutdown_enabled", True):
            return None
        start = sched.get("screen_off_before", "01:00")
        end = sched.get("shutdown_window_end", "05:00")
        now = time.time()
        due = float(st.get("shutdown_at") or 0)
        if due and now < due + 90:
            return None                       # countdown is running normally
        if not within_evening(datetime.now().strftime("%H:%M"), end, start_hhmm=start):
            return None                       # outside the night window
        if due:
            # Past due and the machine is still up: the shutdown was cancelled
            # or blocked. Try a couple more times, then stop fighting.
            tries = int(st.get("shutdown_retries") or 0)
            if tries >= 2:
                return None
            st["shutdown_retries"] = tries + 1
            log("关机没有执行（可能被取消或被程序拦下），重新倒计时（第 %d 次）"
                % st["shutdown_retries"])
            st["shutdown_at"] = 0
        return self.arm_shutdown("deadline")

    def nap_penalty(self, minutes):
        """午睡超时的代价：扣一笔行动点，一天只扣一次。

        允许欠账时可以把余额扣成负数，还清之前买不了娱乐。
        """
        sched = self.cfg["schedule"]
        st = self.st
        amount = int(sched.get("nap_penalty", 5) or 0)
        if amount <= 0 or st.get("nap_penalty_done"):
            return 0
        st["nap_penalty_done"] = True
        before = int(st.get("ap") or 0)
        after = before - amount
        if after < 0 and not self.cfg["ap"].get("allow_negative", True):
            after = 0
        st["ap"] = after
        lost = before - st["ap"]
        if lost:
            log("午睡 %d 分钟超过上限 %s，扣 %d AP（%d -> %d%s）"
                % (minutes, sched.get("nap_limit_minutes"), lost, before, st["ap"],
                   "，欠账" if st["ap"] < 0 else ""))
        else:
            log("午睡 %d 分钟超过上限 %s，但行动点是 %d，没得扣"
                % (minutes, sched.get("nap_limit_minutes"), before))
        return lost

    def wake_penalty(self, why):
        """起床超标：把手上的行动点按除数砍一刀。一天只会砍一次。

        判定依据是"实际几点起的"（系统检测到的首次用机时间），不是"几点点的
        打卡按钮"——按按钮的时间可以补，起床时间补不了。

        有正余额时按除数砍一半；余额是 0 或已经是欠账时没有正数可砍，改扣一笔
        固定金额（0 也算这一档——0 的一半还是 0，否则"余额清零"就成了免费迟到）。
        """
        sched = self.cfg["schedule"]
        st = self.st
        if not sched.get("wake_penalty", True) or st.get("wake_penalty_done"):
            return 0
        st["wake_penalty_done"] = True
        div = max(2, int(sched.get("wake_penalty_divisor", 2) or 2))
        before = int(st.get("ap") or 0)
        if before <= 0:
            flat = max(0, int(sched.get("wake_penalty_debt", 10) or 0))
            if flat <= 0:
                log("起床超标（%s）：当前 %d AP，固定罚款为 0，跳过" % (why, before))
                return 0
            st["ap"] = before - flat
            log("起床超标（%s）：当前 %d AP 没有正余额可砍，改扣固定 %d AP（%d -> %d）"
                % (why, before, flat, before, st["ap"]))
            return flat
        st["ap"] = before // div
        lost = before - st["ap"]
        log("起床超标（%s）：行动点 %d -> %d，扣掉 %d"
            % (why, before, st["ap"], lost))
        return lost

    def wake_penalty_watch(self):
        """自动触发一次，不用等你来点打卡。"""
        sched = self.cfg["schedule"]
        st = self.st
        if not sched.get("wake_penalty", True) or st.get("wake_penalty_done"):
            return None
        end = sched.get("wake_window_end") or sched["wake_target"]
        det = st.get("wake_detected") or st.get("wake_time")
        now_hhmm = datetime.now().strftime("%H:%M")
        if det:
            # 系统已经看到你今天几点开始用电脑了，就以那个为准
            if clock_minutes(det) <= clock_minutes(end):
                return None
            who = "首次用机 %s" % det
        else:
            # 一整个早上都没碰电脑，那就看现在几点
            if clock_minutes(now_hhmm) <= clock_minutes(end):
                return None
            who = "到 %s 还没用过电脑" % now_hhmm
        return self.wake_penalty("%s，达标线 %s" % (who, end))

    def apply_proxy_bypass(self, on):
        """按需给 B 站域名加/去系统代理例外（WinINET ProxyOverride）。

        只在"封锁生效"时加，解除封锁时去掉——所以代理本身的其它流量一点不受影响。
        代理软件重新启用系统代理时可能会把这个列表覆盖掉，所以 enforce 每轮都会
        重新核对一次（读到已经在了就不写盘），等于自愈。
        """
        try:
            import winreg
        except ImportError:
            return None
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        want = proxy_bypass_list(self.cfg)
        if not want:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as key:
                try:
                    current, _ = winreg.QueryValueEx(key, "ProxyOverride")
                except FileNotFoundError:
                    current = ""
                current = str(current or "")
                items = [x for x in current.split(";") if x]
                if on:
                    new_items = items + [d for d in want if d not in items]
                else:
                    drop = set(want)
                    new_items = [x for x in items if x not in drop]
                new_value = ";".join(new_items)
                if new_value != current:
                    winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ,
                                      new_value)
                    log("系统代理例外已%s（%d 个 B 站域名）"
                        % ("加上" if on else "移除", len(want)))
                return new_value
        except OSError as exc:  # noqa: BLE001
            log("改系统代理例外失败：%r" % exc)
            return None

    def reset_browser_network(self):
        """Drop the browser's open sockets without closing a single tab.

        The hosts block only redirects *new* name lookups, so a browser that is
        already sitting on B站 keeps working through its cached address and its
        live connections — the allowance would not really end. Chromium
        browsers run all networking inside one utility process, so restarting
        that process closes every socket while every tab (and anything typed
        into it) survives. The browser respawns the service and re-resolves
        through the now-blocked hosts file: B站 dies, other pages reconnect.
        """
        try:
            import psutil
        except ImportError:
            return 0
        names = {str(n).lower() for n in
                 (self.cfg["game"].get("entertainment_processes") or [])}
        if not names:
            return 0
        killed, seen, browsers = 0, set(), set()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name not in names:
                    continue
                cmd = " ".join(proc.info.get("cmdline") or [])
            except Exception:  # noqa: BLE001
                continue
            browsers.add(name)
            if "utility-sub-type=network" not in cmd and "NetworkService" not in cmd:
                continue
            try:
                proc.terminate()
                killed += 1
                seen.add(name)
            except Exception:  # noqa: BLE001
                pass
        if killed:
            log("B 站封锁生效：重启了 %s 的网络服务，标签页没动、已有连接全部断开"
                % "、".join(sorted(seen)))
        elif browsers:
            log("提示：%s 在运行但没找到独立的网络服务进程，只能靠 hosts 封锁，"
                "已经打开的连接可能还会继续用一会儿"
                % "、".join(sorted(browsers)))
        return killed

    def bilibili_applied(self):
        if time.time() - self._hosts_at > 15.0:
            self._hosts_ok = hosts_is_blocked(self.cfg["bilibili"]["domains"])
            self._hosts_at = time.time()
        return bool(self._hosts_ok)

    def enforce(self, force_lock=False):
        with _LOCK:
            mtime = self._cfg_mtime_now()
            if mtime and mtime != self._cfg_mtime:
                log("检测到 config.json 变更，重新加载设置")
                self.reload()
            self.st = rollover(self.st, self.cfg)
            lock = force_lock or self.want_locked()
            if lock:
                # Use the cache maintained by watch_loop; scanning here would
                # hold the lock for the better part of a second.
                died = self.running_games() if time.time() - self._running_at < 5 else []
                if died and self.st.get("unlock_until"):
                    n = kill_pids([d[0] for d in died])
                    if n:
                        log("娱乐时间结束，结束 %d 个游戏进程" % n)
            self.apply_acl(lock)
            self._enforce_at = time.time()
            self.ensure_pet()
            # hosts
            if self.bili_blocked():
                if (not hosts_is_blocked(self.cfg["bilibili"]["domains"])
                        and time.time() >= _LAST_HOSTS_STATE["next_try"]):
                    ok, note = hosts_apply(self.cfg["bilibili"]["domains"])
                    _LAST_HOSTS_STATE["applied"] = ok
                    _LAST_HOSTS_STATE["note"] = note
                    if not ok:
                        _LAST_HOSTS_STATE["next_try"] = time.time() + 300
                # 光有 hosts 不够：系统代理开着时浏览器走代理、代理自己解析域名，
                # 封锁会被完全绕过。所以同时让浏览器对 B 站域名绕开代理。
                self.apply_proxy_bypass(True)
            else:
                ok, note = hosts_remove()
                _LAST_HOSTS_STATE["applied"] = not ok
                _LAST_HOSTS_STATE["note"] = note
                self.apply_proxy_bypass(False)
            save_state(self.st)

    # -- unlock windows -------------------------------------------------
    def expire_session(self, force=False):
        """Close the open entertainment window and settle the score.

        Early locking ("立即重新封锁") passes force=True: the user chose to stop
        now, so bill whatever was actually used and hand back the rest instead
        of voiding the whole purchase.
        """
        st = self.st
        sess = st.get("session")
        if not sess:
            return None
        if not force and time.time() < float(st.get("unlock_until") or 0):
            return None
        kind = sess.get("kind", "game")
        if kind not in FUN_KINDS:
            kind = "game"
        used = min(sess["bought"], int(sess.get("used_seconds", 0) // 60) + 1)
        used = max(0, min(used, sess["bought"]))
        fun_add(st, kind, used)
        refund = 0
        if self.cfg["game"].get("refund_unused", True):
            unused = sess["bought"] - used
            if unused > 0:
                refund = int(round(unused / 60.0 * self.cfg["game"]["ap_per_hour"]))
                if refund > 0:
                    st["ap"] += refund
                    log("未用完 %d 分钟，退回 %d AP" % (unused, refund))
        log("%s时间结束：本次实际使用 %d 分钟" % (FUN_LABEL[kind], used))
        self.close_overlay()
        bought = sess["bought"]
        st["session"] = None
        st["unlock_until"] = 0
        reset = 0
        if kind == "game":
            self.apply_acl(True)
        else:
            hosts_apply(self.cfg["bilibili"]["domains"])
            if self.cfg["bilibili"].get("reset_browser_network_on_end", True):
                reset = self.reset_browser_network()
        return {"kind": kind, "label": FUN_LABEL[kind], "bought": bought,
                "used": used, "refund": refund, "network_reset": reset}


ENGINE = None


# ---------------------------------------------------------------- API
def snapshot():
    e = ENGINE
    with _LOCK:
        st = rollover(e.st, e.cfg)
        cfg = e.cfg
        mult = multiplier_of(st, cfg)
        base_mult = rhythm_base(st, cfg)
        unlock_left = max(0, int(float(st.get("unlock_until") or 0) - time.time()))
        paths = []
        for p in e.paths:
            acc = path_accessible(p["path"]) if p["path"] else None
            paths.append({
                "path": p["path"], "label": p["label"], "enabled": p["enabled"],
                "exists": acc is not None, "locked": acc is False,
                "watch_guarded": p["path"] in e.acl_failed,
            })
        failed = list(e.acl_failed)
        hist = []
        for i in range(6, -1, -1):
            d = (date.today() - timedelta(days=i)).isoformat()
            if d == today_str():
                g_used, b_used = fun_used(st, "game"), fun_used(st, "bilibili")
                rec = {"study": st["study_minutes_today"], "game": g_used,
                       "bili": b_used, "fun": g_used + b_used,
                       "ap": st["ap_today"]}
            else:
                rec = st["history"].get(d, {"study": 0, "game": 0, "ap": 0})
            hist.append({"date": d, "study": rec.get("study", 0),
                         "game": rec.get("game", 0), "bili": rec.get("bili", 0),
                         "fun": rec.get("fun", rec.get("game", 0)),
                         "ap": rec.get("ap", 0)})
        active = None
        if st.get("study_active"):
            sa = st["study_active"]
            target = sa["target_seconds"]
            done = sa.get("active_seconds", 0)
            paused = bool(sa.get("paused"))
            paused_for = 0
            if paused and sa.get("paused_since"):
                paused_for = max(0, int(time.time() - sa["paused_since"]))
            active = {"target_seconds": target, "active_seconds": int(done),
                      "remaining": max(0, int(target - done)),
                      "paused": paused, "paused_for": paused_for,
                      "paused_total": int(sa.get("paused_total", 0)),
                      "task": sa.get("task"), "name": e.task_name(sa.get("task"))}
        study_log = list(st.get("study_log") or [])
        by_task = {}
        for item in study_log:
            if item.get("date") == today_str():
                key = item.get("task") or "other"
                by_task[key] = by_task.get(key, 0) + int(item.get("minutes") or 0)
        study_days = []
        for day in sorted((st.get("study_days") or {}), reverse=True)[:180]:
            rec = st["study_days"].get(day) or {}
            if day == today_str():
                day_hist = {"wake": st.get("wake_time"), "ok": None,
                            "game": fun_used(st, "game"),
                            "bili": fun_used(st, "bilibili")}
            else:
                day_hist = st["history"].get(day) or {}
            tasks = [{"id": tid, "name": e.task_name(tid), "minutes": int(mins)}
                     for tid, mins in sorted((rec.get("tasks") or {}).items(),
                                             key=lambda kv: -int(kv[1] or 0))]
            study_days.append({
                "date": day,
                "minutes": int(rec.get("minutes") or 0),
                "blocks": int(rec.get("blocks") or 0),
                "ap": int(rec.get("ap") or 0),
                "tasks": tasks,
                "wake": day_hist.get("wake"),
                "ok": day_hist.get("ok"),
                "game": int(day_hist.get("game") or 0),
                "bili": int(day_hist.get("bili") or 0),
            })
        return {
            "ok": True,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "today": today_str(),
            "ap": st["ap"],
            "ap_today": st["ap_today"],
            "multiplier": mult,
            "rhythm_base": base_mult,
            "earned_events": st.get("earned_events_today", 0),
            "event_bonus": cfg["ap"].get("event_bonus", 0.1),
            "multiplier_cap": cfg["ap"].get("multiplier_cap", 1.0),
            "wake_time": st.get("wake_time"),
            "wake_detected": st.get("wake_detected"),
            "wake_penalty_done": bool(st.get("wake_penalty_done")),
            "wake_target": cfg["schedule"]["wake_target"],
            "wake_window_end": (cfg["schedule"].get("wake_window_end")
                                or cfg["schedule"]["wake_target"]),
            "nap_minutes": st.get("nap_minutes"),
            "nap_penalty_done": bool(st.get("nap_penalty_done")),
            "nap_limit": cfg["schedule"]["nap_limit_minutes"],
            "screen_off_before": cfg["schedule"]["screen_off_before"],
            "screen_off_window_end": (cfg["schedule"].get("screen_off_window_end")
                                      or cfg["schedule"]["screen_off_before"]),
            "screen_off_done": st.get("screen_off_done") == night_key(),
            "shutdown_in": max(0, int(float(st.get("shutdown_at") or 0)
                                     - time.time())),
            "shutdown_reason": st.get("shutdown_reason"),
            "shutdown_enabled": bool(cfg["schedule"].get("shutdown_enabled", True)),
            "outdoor_done": bool(st.get("outdoor_done")),
            "shower_done": bool(st.get("shower_done")),
            "laundry_done": bool(st.get("laundry_done")),
            "active_kind": e.active_kind(),
            "bili_blocked": e.bili_blocked(),
            "warning_seconds": cfg["game"].get("warning_seconds", 180),
            "study_minutes": st["study_minutes_today"],
            "study_blocks": st["study_blocks_today"],
            "study_block_minutes": cfg["ap"]["study_block_minutes"],
            "study_active": active,
            "study_task": st.get("study_task"),
            "study_tasks": e.task_list(),
            "study_by_task": by_task,
            "study_log": list(reversed(study_log[-60:])),
            "study_days": study_days,
            "study_keep_days": STUDY_KEEP_DAYS,
            "game_minutes": fun_used(st, "game"),
            "game_cap": fun_cap(cfg, "game"),
            "bili_minutes": fun_used(st, "bilibili"),
            "bili_cap": fun_cap(cfg, "bilibili"),
            "unplanned_minutes": int(st.get("unplanned_minutes_today") or 0),
            "unplanned_multiplier": float(
                cfg["game"].get("unplanned_multiplier", 1.5) or 1.5),
            "fun_minutes": fun_used(st, "game") + fun_used(st, "bilibili"),
            "fun_cap": fun_cap(cfg, "game") + fun_cap(cfg, "bilibili"),
            "unlock_remaining": unlock_left,
            "unlock_total": (st.get("session") or {}).get("bought", 0) * 60,
            "locked": e.game_locked(),
            "streak": st.get("streak", 0),
            "streak_target": cfg["schedule"]["streak_days"],
            "talismans": st.get("talismans", 0),
            "talisman_used": st.get("talisman_used_on") == today_str(),
            "history": hist,
            "paths": paths,
            "acl_failed": failed,
            "elevated": is_elevated(),
            "blocked_attempts": st.get("blocked_attempts", 0),
            "running_games": [{"pid": p[0], "name": p[1]} for p in
                              e.running_games()],
            "bilibili": {
                "enabled": cfg["bilibili"]["enabled"],
                "applied": e.bilibili_applied(),
                "blocked": e.bili_blocked(),
                "hosts_blocked": hosts_is_blocked(cfg["bilibili"]["domains"]),
                "can_write_hosts": hosts_can_write(),
                "note": _LAST_HOSTS_STATE.get("note", ""),
                "domains": cfg["bilibili"]["domains"],
            },
            "rewards": cfg["rewards"],
            "bought": st.get("rewards_bought", []),
            "pet": {
                "enabled": (cfg.get("pet") or {}).get("enabled", True),
                "running": pet_already_running(),
                "hidden_for": max(0, int(float(st.get("pet_hidden_until") or 0)
                                         - time.time())),
                "character_height": (cfg.get("pet") or {}).get("character_height", 227),
                "brightness": (cfg.get("pet") or {}).get("brightness", 1.0),
                "anim_dir": (cfg.get("pet") or {}).get("anim_dir", "pet_anim"),
                "anim_exists": (BASE / (cfg.get("pet") or {}).get(
                    "anim_dir", "pet_anim")).exists(),
                "image_exists": (BASE / (cfg.get("pet") or {}).get(
                    "image", "fish.png")).exists(),
            },
            "cost": {
                "per_hour": cfg["game"]["ap_per_hour"],
                "steps": cfg["game"]["steps_minutes"],
            },
        }


def do_action(payload):
    e = ENGINE
    name = (payload or {}).get("action", "")
    with _LOCK:
        st = rollover(e.st, e.cfg)
        cfg = e.cfg
        msg = ""
        ok = True

        if name in ("wake", "work"):
            det = st.get("wake_detected")
            stamp = det or datetime.now().strftime("%H:%M")
            st["wake_time"] = stamp
            mult = multiplier_of(st, cfg)
            end = (cfg["schedule"].get("wake_window_end")
                   or cfg["schedule"]["wake_target"])
            msg = "工作打卡 %s（达标线 %s），今日系数 %.1f" % (stamp, end, mult)
            if clock_minutes(stamp) > clock_minutes(end):
                lost = e.wake_penalty("打卡时间 %s" % stamp)
                msg += ("；超过达标线，行动点减半（-%d AP）" % lost if lost
                        else "；超过达标线的惩罚已经生效过了")

        elif name == "nap":
            minutes = int(payload.get("minutes") or 0)
            st["nap_minutes"] = max(0, minutes)
            limit = cfg["schedule"]["nap_limit_minutes"]
            if minutes > limit:
                amount = int(cfg["schedule"].get("nap_penalty", 5) or 0)
                already = bool(st.get("nap_penalty_done"))
                lost = e.nap_penalty(minutes)
                msg = "午睡 %d 分钟，超过上限 %d" % (minutes, limit)
                if lost:
                    msg += "，扣 %d AP" % lost
                elif already:
                    msg += "，今天已经扣过了"
                elif amount <= 0:
                    msg += "（午睡超时罚金已关闭）"
                else:
                    msg += "，行动点是 0，没得扣"
            else:
                msg = "午睡 %d 分钟，达标" % minutes

        elif name == "outdoor":
            if st.get("outdoor_done"):
                ok, msg = False, "今天已经打卡过"
            else:
                st["outdoor_done"] = True
                got, _ = award(st, cfg, cfg["ap"]["outdoor_reward"], "户外/运动",
                               count_event=False)
                msg = "户外打卡 +%d AP" % got

        elif name == "shower":
            if st.get("shower_done"):
                ok, msg = False, "今天已经打过卡"
            else:
                st["shower_done"] = True
                got, _ = award(st, cfg, cfg["ap"].get("shower_reward", 2), "洗澡",
                               count_event=False)
                msg = "洗澡打卡 +%d AP" % got

        elif name == "laundry":
            if st.get("laundry_done"):
                ok, msg = False, "今天已经打过卡"
            else:
                st["laundry_done"] = True
                got, _ = award(st, cfg, cfg["ap"].get("laundry_reward", 2), "洗衣服",
                               count_event=False)
                msg = "洗衣服打卡 +%d AP" % got

        elif name == "screen_off":
            night = night_key()
            limit = (cfg["schedule"].get("screen_off_window_end")
                     or cfg["schedule"]["screen_off_before"])
            if st.get("screen_off_done") == night:
                ok, msg = False, "这一晚已经打过卡了"
            else:
                st["screen_off_done"] = night
                if within_evening(datetime.now().strftime("%H:%M"), limit):
                    got, _ = award(st, cfg, cfg["ap"]["screen_off_reward"], "按时关屏",
                                   count_event=False)
                    msg = "关屏打卡 +%d AP" % got
                    # "完整一天"奖励：这一夜乘数满格 + 完成的事够多
                    bonus = int(cfg["ap"].get("screen_off_bonus", 5) or 0)
                    need_mult = float(cfg["ap"].get("screen_off_bonus_multiplier", 1.0))
                    need_events = int(cfg["ap"].get(
                        "screen_off_bonus_blocks",
                        cfg["ap"].get("screen_off_bonus_events", 5)) or 0)
                    mult, events = nightly_multiplier(st, cfg)
                    if bonus > 0 and mult >= need_mult and events >= need_events:
                        st["ap"] += bonus
                        log("完整一天奖励：乘数 %.2f、完成 %d 个学习块，关屏额外 +%d AP"
                            % (mult, events, bonus))
                        msg += ("；乘数 %.2f、完成 %d 个学习块，额外 +%d AP"
                                % (mult, events, bonus))
                    else:
                        msg += "（乘数 %.2f、完成 %d 个学习块）" % (mult, events)
                else:
                    msg = "已记录，但晚于 %s，本次不加分" % limit
            if cfg["schedule"].get("shutdown_enabled", True) and not st.get("shutdown_at"):
                delay = e.arm_shutdown("click")
                if delay:
                    msg += "；%d 秒后自动关机" % delay

        elif name == "study_start":
            if st.get("study_active"):
                ok, msg = False, "已经在计时中"
            else:
                task = payload.get("task") or st.get("study_task") or "other"
                if task not in [t["id"] for t in e.task_list()]:
                    task = (e.task_list() or [{"id": "other"}])[0]["id"]
                st["study_task"] = task
                target = cfg["ap"]["study_block_minutes"] * 60
                st["study_active"] = {"start": time.time(), "target_seconds": target,
                                      "active_seconds": 0, "last_tick": time.time(),
                                      "task": task, "paused": False,
                                      "paused_since": 0, "paused_total": 0}
                msg = "开始 %d 分钟学习：%s" % (cfg["ap"]["study_block_minutes"],
                                               e.task_name(task))

        elif name in ("study_pause", "study_resume"):
            sa = st.get("study_active")
            want = (name == "study_pause")
            if not sa:
                ok, msg = False, "现在没有在计时的学习块"
            elif bool(sa.get("paused")) == want:
                ok, msg = False, "已经是%s状态" % ("暂停" if want else "进行中")
            else:
                sa["paused"] = want
                if want:
                    sa["paused_since"] = time.time()
                    msg = "已暂停计时（这段时间不计入有效时间）"
                else:
                    if sa.get("paused_since"):
                        gap = time.time() - sa["paused_since"]
                        # 关机/休眠也会走到这里，别把好几天记成"暂停时长"
                        sa["paused_total"] = (sa.get("paused_total", 0)
                                              + max(0.0, min(gap, 6 * 3600)))
                    sa["paused_since"] = 0
                    sa["last_tick"] = time.time()   # 暂停期间一秒都不补
                    msg = "已继续计时"

        elif name == "set_task":
            task = payload.get("task")
            ids = [t["id"] for t in e.task_list()]
            if task not in ids:
                ok, msg = False, "没有这个学习任务"
            else:
                st["study_task"] = task
                msg = "已切换到 %s" % e.task_name(task)

        elif name == "study_cancel":
            st["study_active"] = None
            msg = "已取消本次学习计时"

        elif name == "study_finish":
            # 手动"完成学习块"已经删除：时间没到不算数，到点由 tick_study 自动结算。
            # 留着这个分支只是为了让旧调用方拿到一句人话，而不是"未知操作"。
            ok, msg = False, "学习块不能手动完成，有效时间满了会自动结算"

        elif name == "log_unplanned":
            # 计划外游戏时间：系统管不到的那些（Steam、手机、别人机器上的）。
            # 按小时单价的 1.5 倍补缴，扣成欠账也照扣——自报才可能让记录是真的。
            minutes = int(payload.get("minutes") or 0)
            if minutes <= 0:
                ok, msg = False, "分钟数无效"
            elif minutes > 24 * 60:
                ok, msg = False, "一次最多记 1440 分钟（24 小时）"
            else:
                per_hour = float(cfg["game"].get("ap_per_hour", 6) or 6)
                mult = float(cfg["game"].get("unplanned_multiplier", 1.5) or 1.5)
                cost = max(1, int(round(minutes / 60.0 * per_hour * mult)))
                st["ap"] = int(st.get("ap") or 0) - cost
                fun_add(st, "game", minutes)
                st["unplanned_minutes_today"] = (int(st.get("unplanned_minutes_today") or 0)
                                                 + minutes)
                log("计划外游戏 %d 分钟：扣 %d AP（%.0f AP/小时的 %.1f 倍），"
                    "余额 %d%s" % (minutes, cost, per_hour, mult, st["ap"],
                                   "，欠账" if st["ap"] < 0 else ""))
                msg = ("已记录计划外游戏 %d 分钟，扣 %d AP；当前余额 %d%s"
                       % (minutes, cost, st["ap"],
                          "（欠账）" if st["ap"] < 0 else ""))

        elif name == "redeem":
            minutes = int(payload.get("minutes") or 0)
            kind = payload.get("kind") or "game"
            if kind not in FUN_KINDS:
                kind = "game"
            label = FUN_LABEL[kind]
            cap = fun_cap(cfg, kind)
            sess = st.get("session") or {}
            # only a session of the same kind eats into that kind's allowance
            pending = sess.get("bought", 0) if sess.get("kind") == kind else 0
            already = fun_used(st, kind) + pending
            if e.active_kind():
                ok, msg = False, "还有一段娱乐时间没走完，等它结束或点立即重新封锁"
            elif minutes <= 0:
                ok, msg = False, "分钟数无效"
            elif already + minutes > cap:
                ok, msg = False, "今天%s额度只剩 %d 分钟" % (label, max(0, cap - already))
            elif kind == "bilibili" and not hosts_can_write():
                # B站 opens by lifting the hosts block, and hosts needs an
                # elevated token. Say so instead of spending points on nothing.
                ok, msg = False, ("打不开 B 站：修改 hosts 需要管理员权限，"
                                  "守护进程现在不是以管理员身份运行的。"
                                  "请右键「安装.cmd」选「以管理员身份运行」，重启后再兑换。"
                                  "这次没有扣行动点。")
            else:
                cost = max(1, int(round(minutes / 60.0 * cfg["game"]["ap_per_hour"])))
                if not spend(st, cost):
                    ok, msg = False, "行动点不足，需要 %d，当前 %d" % (cost, st["ap"])
                else:
                    st["unlock_until"] = time.time() + minutes * 60
                    st["session"] = {"kind": kind, "bought": minutes,
                                     "used_seconds": 0, "overlay_shown": False}
                    if kind == "game":
                        e.apply_acl(False)
                        msg = "解锁游戏 %d 分钟，消耗 %d AP" % (minutes, cost)
                    else:
                        ok2, note = hosts_remove()
                        if not ok2:
                            # could not really open it -> undo the whole purchase
                            st["ap"] += cost
                            st["session"] = None
                            st["unlock_until"] = 0
                            ok, msg = False, ("打开 B 站失败（%s），已退回 %d AP，"
                                              "B 站仍然是封锁状态。" % (note, cost))
                        else:
                            _LAST_HOSTS_STATE["applied"] = False
                            _LAST_HOSTS_STATE["note"] = "兑换中，已临时打开"
                            msg = "解锁 B 站 %d 分钟，消耗 %d AP" % (minutes, cost)

        elif name == "lock_now":
            done = e.expire_session(force=True) if st.get("session") else None
            e.close_overlay()
            st["unlock_until"] = 0
            st["session"] = None
            e.apply_acl(True)
            if cfg["bilibili"].get("enabled", True):
                ok2, note = hosts_apply(cfg["bilibili"]["domains"])
                _LAST_HOSTS_STATE["applied"] = ok2
                _LAST_HOSTS_STATE["note"] = note
            else:
                hosts_remove()
            if done:
                msg = ("已重新封锁：%s实际用了 %d 分钟"
                       % (done["label"], done["used"]))
                msg += ("，退回 %d AP（还剩 %d 分钟没用）"
                        % (done["refund"], done["bought"] - done["used"])
                        if done["refund"] else "，没有剩余分钟可退")
                if done.get("network_reset"):
                    msg += "，已断开浏览器的已有连接（标签页不受影响）"
            else:
                msg = "已立即重新封锁游戏和 B 站"

        elif name == "close_overlay":
            n = e.close_overlay()
            msg = ("已关掉 %d 个倒计时浮层" % n) if n else "当前没有倒计时浮层在显示"

        elif name == "lock_bili":
            sess = st.get("session") or {}
            done = None
            if sess.get("kind") == "bilibili":
                done = e.expire_session(force=True)
            if not cfg["bilibili"].get("enabled", True):
                ok, msg = False, "B 站封锁总开关在 config.json 里被关掉了"
            else:
                ok2, note = hosts_apply(cfg["bilibili"]["domains"])
                _LAST_HOSTS_STATE["applied"] = ok2
                _LAST_HOSTS_STATE["note"] = note
                if not ok2:
                    ok, msg = False, "重新封锁 B 站失败：%s" % note
                elif done:
                    msg = ("已提前封锁 B 站：本次实际用了 %d 分钟"
                           % done["used"])
                    if done["refund"]:
                        msg += "，退回 %d AP" % done["refund"]
                    if done.get("network_reset"):
                        msg += "，已断开浏览器的已有连接（标签页不受影响）"
                else:
                    msg = "B 站现在处于封锁状态"

        elif name == "hide_pet":
            minutes = int(cfg.get("pet", {}).get("hide_minutes", 30))
            st["pet_hidden_until"] = time.time() + minutes * 60
            e.close_pet()
            msg = "桌面宠物已收起，%d 分钟后自动回来" % minutes

        elif name == "show_pet":
            st["pet_hidden_until"] = 0
            e.ensure_pet(force=True)
            msg = "桌面宠物已重新显示"

        elif name == "use_talisman":
            if st.get("talismans", 0) <= 0:
                ok, msg = False, "没有护身符"
            elif st.get("talisman_used_on") == today_str():
                ok, msg = False, "今天已经用过了"
            else:
                st["talismans"] -= 1
                st["talisman_used_on"] = today_str()
                msg = "护身符已使用，今日系数视为 1.0"

        elif name == "buy_reward":
            rid = payload.get("id")
            reward = next((r for r in cfg["rewards"] if r["id"] == rid), None)
            if not reward:
                ok, msg = False, "奖励不存在"
            elif rid in st.get("rewards_bought", []):
                ok, msg = False, "今天已经兑换过"
            else:
                if spend(st, reward["cost"]):
                    st.setdefault("rewards_bought", []).append(rid)
                    msg = "已兑换：%s" % reward["name"]
                else:
                    ok, msg = False, "行动点不足，需要 %d" % reward["cost"]

        elif name == "toggle_path":
            target = payload.get("path")
            cur = next((p for p in e.paths if p["path"] == target), None)
            if cur is None:
                ok, msg = False, "封锁列表里没有这个目录"
            elif cur["enabled"]:
                # 封锁范围只增不减：面板上不给"取消勾选"这条退路，免得想玩的时候
                # 点一下就把目录放出来。真要移除得手动改 config.json —— 那至少是
                # 一个清醒的、要动文件的动作，不是顺手一点。
                ok, msg = False, ("封锁范围只增不减：已经锁上的目录不能在面板上取消。"
                                  "确实要移除的话，只能手工编辑 config.json。")
            else:
                cur["enabled"] = True
                for item in cfg["protected"]:
                    if isinstance(item, dict) and item.get("path") == target:
                        item["enabled"] = True
                save_json(CFG_PATH, cfg)
                e.reload()
                e.apply_acl(e.game_locked())
                msg = "已加入封锁：%s" % (cur.get("label") or target)

        elif name == "add_path":
            raw = (payload.get("path") or "").strip().strip('"')
            if not raw or not Path(raw).exists():
                ok, msg = False, "路径不存在"
            elif any(p["path"].lower() == raw.lower() for p in e.paths):
                ok, msg = False, "已经在列表里"
            else:
                cfg["protected"].append({"path": raw, "label": Path(raw).name,
                                         "enabled": True})
                save_json(CFG_PATH, cfg)
                e.reload()
                e.apply_acl(e.game_locked())
                msg = "已加入封锁列表"

        elif name == "bilibili":
            # Deliberately not switchable from the panel: a one-click toggle
            # would let B站 be opened without spending points.
            ok, msg = False, "B 站封锁不能在面板上切换，需要的话改 config.json 里的 bilibili.enabled"

        elif name == "elevate":
            try:
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(BASE / "_runas.ps1"),
                     "-Target", str(BASE / "install.ps1")],
                    creationflags=CREATE_NO_WINDOW)
                msg = "已弹出管理员授权窗口，同意之后程序会自动重启并完成封锁"
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, "启动失败：%s" % exc

        elif name == "rescan":
            found = scan_games()
            added = []
            for path, label in found:
                if not any(p["path"].lower() == path.lower() for p in e.paths):
                    cfg["protected"].append({"path": path, "label": label,
                                             "enabled": False})
                    added.append(label)
            save_json(CFG_PATH, cfg)
            e.reload()
            msg = ("发现并加入 %d 个候选目录（默认未启用）：%s"
                   % (len(added), "、".join(added))) if added else "没有发现新的候选目录"

        else:
            ok, msg = False, "未知操作"

        save_state(st)
        return {"ok": ok, "message": msg, "state": snapshot()}


def scan_games(roots=None):
    """Look for game folders we do not protect yet."""
    roots = roots or [r"D:\2", r"D:\Games",
                      r"D:\Steam\steamapps\common", r"D:\leidian", r"D:\MuMuPlayer"]
    markers = ("unityplayer", "gameassembly", "steam_api", "steam_emu",
               "unrealengine", "godot", ".pak", ".xp3", "balatro")
    skip = ("steamworks shared", "resources", "locales", "bin", "config",
            "lib", "extension", "audio", "userdata", "steamapps", "workshop")
    out = []
    for root in roots:
        rp = Path(root)
        if not rp.is_dir():
            continue
        try:
            entries = list(rp.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            low = entry.name.lower()
            if low in skip or low.startswith("$"):
                continue
            score = 0
            try:
                names = [c.name.lower() for c in list(entry.iterdir())[:200]]
            except OSError:
                continue
            for n in names:
                if n.endswith(".exe"):
                    score += 1
                if any(m in n for m in markers):
                    score += 2
            if score >= 3:
                out.append((str(entry), entry.name))
    return out


# ---------------------------------------------------------------- http
def already_running(port):
    """True when another guard instance is already answering on this port."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port,
                                    timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


class GuardServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind the same port, which
    # would give us two daemons fighting over state.json. Refuse to share.
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "StudyGuard"

    def log_message(self, fmt, *args):  # silence
        return

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                text = DASH_PATH.read_text(encoding="utf-8")
                self._send(200, text, "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(200, snapshot())
            elif path == "/api/study_log":
                day = ""
                if "?" in self.path:
                    for part in self.path.split("?", 1)[1].split("&"):
                        key, _, val = part.partition("=")
                        if key == "day":
                            day = val.strip()
                with _LOCK:
                    entries = [e for e in (ENGINE.st.get("study_log") or [])
                               if not day or e.get("date") == day]
                    entries = list(reversed(entries))[-200:]
                self._send(200, {"ok": True, "day": day, "entries": entries})
            elif path == "/api/ping":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001
            log("GET %s 出错: %r" % (path, exc))
            self._send(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        try:
            if path == "/api/action":
                self._send(200, do_action(payload))
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001
            log("POST %s 出错: %r" % (path, exc))
            self._send(500, {"ok": False, "error": str(exc)})


# ---------------------------------------------------------------- loops
def tick_loop():
    """Track active study seconds and expire unlock windows."""
    while True:
        try:
            with _LOCK:
                e = ENGINE
                e.st = rollover(e.st, e.cfg)
                st = e.st
                now = time.time()
                e.tick_study(now)
                # unlock window
                sess = st.get("session")
                if sess:
                    until = float(st.get("unlock_until") or 0)
                    if now < until:
                        kind = sess.get("kind", "game")
                        busy = bool(e.running_games()) if kind == "game" \
                            else bool(e.running_fun())
                        if busy:
                            sess["used_seconds"] = sess.get("used_seconds", 0) + 5
                        warn = e.cfg["game"].get("warning_seconds", 180)
                        if until - now <= warn and not sess.get("overlay_shown"):
                            sess["overlay_shown"] = True
                            e.spawn_overlay(int(until - now),
                                            "游戏" if kind == "game" else "B 站")
                    else:
                        e.expire_session()
                # remember the first moment the user touched the PC today
                if not st.get("wake_detected"):
                    nowdt = datetime.now()
                    if nowdt.hour >= 5 and idle_seconds() < 60:
                        st["wake_detected"] = nowdt.strftime("%H:%M")
                        log("检测到今日首次使用电脑：%s" % st["wake_detected"])
                # 关屏硬停：到点自动进入 3 分钟关机倒计时
                e.screen_off_watch()
                # 起床超标：到点自动把行动点砍一半
                e.wake_penalty_watch()
                save_state(st)
        except Exception as exc:  # noqa: BLE001
            log("tick 出错: %r" % exc)
        time.sleep(5)


def enforce_loop():
    while True:
        try:
            with _LOCK:
                ENGINE.enforce()
        except Exception as exc:  # noqa: BLE001
            log("enforce 出错: %r" % exc)
        time.sleep(10)


def watch_loop():
    """Fast fallback: if a protected folder could not be ACL-locked, kill the game
    process as soon as it appears. Works without administrator rights."""
    while True:
        try:
            with _LOCK:
                e = ENGINE
                now = time.time()
                procs = [p for p in e.running_games()
                         if now - e._killed.get(p[0], 0.0) > 6.0]
                if e.game_locked() and procs:
                    n = kill_pids([p[0] for p in procs])
                    if n:
                        e.st["blocked_attempts"] = e.st.get("blocked_attempts", 0) + 1
                        for p in procs:
                            e._killed[p[0]] = now
                        if len(e._killed) > 500:
                            e._killed = {k: v for k, v in e._killed.items()
                                         if now - v < 60}
                        log("拦截并结束了 %d 个游戏进程：%s" %
                            (n, "、".join(p[1] for p in procs[:4])))
                        save_state(e.st)
        except Exception as exc:  # noqa: BLE001
            log("watch 出错: %r" % exc)
        time.sleep(1.5)


def overlay_window(seconds, label):
    """Always-on-top countdown: top-centre of the screen, 一号 = 26pt."""
    try:
        import tkinter as tk
    except ImportError:
        return
    bg = "#7f1d1d" if label == "游戏" else "#7c2d12"
    root = tk.Tk()
    root.overrideredirect(True)
    root.title("学习积分管家 倒计时")
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.95)
    except Exception:  # noqa: BLE001
        pass
    root.configure(bg=bg)
    width, height = 760, 152
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    root.geometry("%dx%d+%d+%d" % (width, height, x, 6))
    var = tk.StringVar()
    lab = tk.Label(root, textvariable=var, font=("Microsoft YaHei", 26, "bold"),
                   fg="#ffe6a7", bg=bg)
    lab.pack(expand=True, fill="both", pady=(8, 0))
    hint = tk.Label(root, text="点一下这条浮层就能关掉它", font=("Microsoft YaHei", 9),
                    fg="#ffcfa0", bg=bg)
    hint.pack(side="bottom", pady=(0, 6))
    # Clicking anywhere on the banner dismisses it.
    root.bind_all("<Button-1>", lambda _e: root.destroy())
    end = time.time() + seconds

    def tick():
        left = int(round(end - time.time()))
        if left <= 0:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
            return
        var.set("%s时间还剩 %02d:%02d" % (label, left // 60, left % 60))
        root.after(200, tick)

    tick()
    try:
        root.lift()
        root.mainloop()
    except Exception:  # noqa: BLE001
        pass


def pet_already_running():
    """True when a desktop fish from an earlier launch is still alive."""
    import pet as pet_module
    return pet_module.already_running(str(BASE))


def pet_loop():
    """Entry point for the animated desktop pet; the implementation is in pet.py."""
    import pet as pet_module
    pet_module.run(str(BASE))



def scan_loop(payload_json):
    """Standalone process: publish running game and browser processes to a file."""
    payload = json.loads(payload_json)
    paths = payload.get("paths") or []
    names = payload.get("names") or []
    blocked = payload.get("blocked") or []
    parent = os.getppid()
    while True:
        try:
            try:
                import psutil
            except ImportError:
                return
            if parent and not psutil.pid_exists(parent):
                return
            data = {
                "games": [{"pid": p[0], "name": p[1], "exe": p[2]}
                          for p in running_under(paths)],
                # 点名要盯死的进程（Steam 那一族）：不管它从哪个目录启动
                "blocked": [{"pid": p[0], "name": p[1], "exe": p[2]}
                            for p in running_named(blocked)],
                "fun": [{"pid": p[0], "name": p[1], "exe": p[2]}
                        for p in running_named(names)],
                "at": time.time(),
            }
            tmp = str(SCAN_PATH) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, SCAN_PATH)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)


def main():
    global ENGINE
    args = sys.argv[1:]

    if "--scan-loop" in args:
        idx = args.index("--scan-loop")
        scan_loop(args[idx + 1] if len(args) > idx + 1 else "[]")
        return

    if "--overlay" in args:
        idx = args.index("--overlay")
        secs = int(args[idx + 1]) if len(args) > idx + 1 else 180
        label = args[idx + 2] if len(args) > idx + 2 else "游戏"
        overlay_window(secs, label)
        return

    if "--pet" in args:
        pet_loop()
        return

    unknown = [a for a in args if a.startswith("--") and a != "--ensure"]
    if unknown:
        log("收到未知参数 %s，本进程退出" % " ".join(unknown))
        return

    ENGINE = Engine()
    if "--ensure" in args:
        import urllib.request
        port = ENGINE.cfg["port"]
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=2)
            return
        except Exception:  # noqa: BLE001
            log("看门狗未发现守护进程，重新启动")
        # Launch the real daemon as a *separate* process and leave. If the
        # watchdog itself became the long-lived guard, the scheduled task would
        # keep that instance marked as running and every later watchdog tick
        # would be refused — which is exactly how the guard stayed down on
        # 2026-10-09. A detached child keeps the watchdog disposable.
        try:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve())],
                             cwd=str(BASE), creationflags=CREATE_NO_WINDOW)
        except Exception as exc:  # noqa: BLE001
            log("看门狗启动守护进程失败：%r" % exc)
        return

    ENGINE.st = rollover(ENGINE.st, ENGINE.cfg)
    ENGINE.enforce()
    ENGINE.ensure_scanner()
    threading.Thread(target=tick_loop, daemon=True).start()
    threading.Thread(target=enforce_loop, daemon=True).start()
    threading.Thread(target=watch_loop, daemon=True).start()

    port = ENGINE.cfg["port"]
    if already_running(port):
        log("端口 %d 上已有实例在运行，本进程退出" % port)
        return
    try:
        server = GuardServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        log("端口 %d 已被占用，本进程退出（%s）" % (port, exc))
        return
    log("学习积分管家已启动，端口 %d" % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import atexit
    atexit.register(lambda: log("守护进程退出（正常路径）"))
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        log("守护进程异常退出:\n" + traceback.format_exc())
        raise
