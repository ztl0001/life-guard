# -*- coding: utf-8 -*-
"""Animated desktop pet for the study guard.

Animation frames come from the open-source dsh-pet pack (same character as the
user's own artwork), re-encoded to APNG so tkinter can play them with alpha.
"""
import json
import io
import os
import random
import subprocess
import threading
import time
from pathlib import Path

# A cached entry is only an open file handle plus the current frame, so keeping
# every animation resident is cheap and avoids evicting the one on screen.
ANIM_CACHE_LIMIT = 12


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save_json(path, obj):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _log(base, msg):
    try:
        with open(os.path.join(base, "guard.log"), "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


# Animations that answer a click. These are reaction clips only — the idle
# fidgets stay out of this list so a poke never turns into "goes back to sleep".
# Each day a different slice of the catalogue is live, so the repertoire
# changes with the date.
CLICK_CATALOG = [
    "click_happy", "click_wave", "click_angry", "click_shy", "click_tickle",
    "click_startled", "click_wakeup", "click_caught", "click_rage",
    "click_curtsy", "click_tap", "click_flower", "click_juggle",
    "click_tailslap", "click_dance",
]
# things the pet does on its own; kept gentle on purpose
IDLE_CATALOG = ["yawn", "rest", "jump", "eat_rice"]
# the rare 12%-chance easter eggs stay special rather than joining the rotation
SPECIAL_CATALOG = ["special_dove", "special_magic"]
DAILY_CLICK_COUNT = 7
DAILY_IDLE_COUNT = 4

# Used when the rotation is switched off in config.json (pet.daily_action_rotation)
CLICK_POOL = ["click_happy", "click_wave", "click_angry", "click_shy", "click_tickle"]
SPECIALS = ["special_dove", "special_magic"]
IDLE_POOL = ["yawn", "rest", "jump", "eat_rice"]
MOOD_LINES = {
    "click_happy": ["戳我干嘛，我又不会掉积分", "在的在的", "今天也要加油"],
    "click_wave": ["嗨", "看到你了", "在忙吗"],
    "click_angry": ["别戳了，痒", "再点我就生气了", "喂，有事说事"],
    "click_shy": ["干嘛突然点我", "……别盯着看", "我没有脸红"],
    "click_tickle": ["哈哈哈别挠", "痒痒痒", "住手啦"],
    "special_dove": ["猜猜我是谁", "变！", "飞走了"],
    "special_magic": ["看好了", "抽一张", "献丑了"],
    "click_startled": ["吓我一跳！", "谁？！", "别突然冒出来"],
    "click_wakeup": ["我没睡！", "……醒了醒了", "谁吵我"],
    "click_caught": ["我什么都没吃", "……就一口", "放下了放下了"],
    "click_rage": ["这游戏有问题", "不玩了不玩了", "再来一把"],
    "click_curtsy": ["主人好", "有何吩咐", "为您服务"],
    "click_tap": ["敲回去", "有事说事", "别敲了嘛"],
    "click_flower": ["送你一朵", "变！", "献丑了"],
    "click_juggle": ["看好了", "接得住", "杂技时间"],
    "click_tailslap": ["尾巴痒", "别惹我", "啪！"],
    "click_dance": ["跳一个", "看我的", "热闹一下"],
    "jump": ["蹦一下", "接住它", "抓到了"],
    "yawn": ["困死了", "啊——欠", "几点了都"],
    "rest": ["别吵，我在睡", "让我眯一会儿", "……zzz"],
    "eat_rice": ["我在吃白饭", "干饭中", "这白饭真香"],
    "celebrate": ["漂亮", "又搞定一件", "值得庆祝一下"],
    "talk": ["你在听吗", "我刚才说到哪了", "嗯……"],
    "walk": ["走两步", "活动一下", "溜达溜达"],
}

# speech bubble colour by tone: (background, text)
TONE_COLOR = {
    "good": ("#7cf0b4", "#04301c"),
    "warn": ("#ffc94a", "#2e1c00"),
    "bad": ("#ff6b6b", "#2e0606"),
    "info": ("#63c8ff", "#04202e"),
    "chat": ("#ffe08a", "#2a1e00"),
    "click_happy": ("#ffd84d", "#2a1e00"),
    "click_wave": ("#63c8ff", "#04202e"),
    "click_angry": ("#ff5a4d", "#ffffff"),
    "click_shy": ("#ff9ad5", "#3a0d28"),
    "click_tickle": ("#8bf08b", "#0b2a0b"),
    "special_dove": ("#bda8ff", "#1d1240"),
    "special_magic": ("#ffa64d", "#2b1400"),
    "click_startled": ("#ffd1a8", "#3a1e00"),
    "click_wakeup": ("#c9d3ff", "#141a3a"),
    "click_caught": ("#ffe08a", "#2a1e00"),
    "click_rage": ("#ff8a5a", "#3a1200"),
    "click_curtsy": ("#ffc9e8", "#3a0d28"),
    "click_tap": ("#a8e6ff", "#04202e"),
    "click_flower": ("#d6b8ff", "#231040"),
    "click_juggle": ("#9df0c4", "#06301c"),
    "click_tailslap": ("#7fd4ff", "#032033"),
    "click_dance": ("#ffb3d1", "#3a0d20"),
    "jump": ("#7cf0b4", "#04301c"),
    "yawn": ("#c9d3ff", "#141a3a"),
    "rest": ("#b6a7ff", "#1d1240"),
    "eat_rice": ("#ffe08a", "#2a1e00"),
    "celebrate": ("#ffd84d", "#2a1e00"),
    "talk": ("#63c8ff", "#04202e"),
    "walk": ("#8bf0c0", "#0b2a1c"),
}
DEFAULT_TONE = ("#ffe08a", "#2a1e00")


def daily_pools(day=None, exclude=()):
    """Pick today's click reactions, easter eggs and idle fidgets.

    Seeded by the date, so the pet keeps one repertoire for the whole day —
    including across restarts — and swaps to a different one tomorrow.
    """
    drop = set(exclude or ())
    clicks_src = [n for n in CLICK_CATALOG if n not in drop]
    idle_src = [n for n in IDLE_CATALOG if n not in drop]
    rng = random.Random("study-guard-pet-" + (day or time.strftime("%Y-%m-%d")))
    rng.shuffle(clicks_src)
    clicks = clicks_src[:DAILY_CLICK_COUNT] or [n for n in CLICK_POOL if n not in drop]
    rng.shuffle(idle_src)
    # prefer fidgets that are not also on today's click list, so the two roles
    # stay distinguishable; reuse them only when the catalogue is too short
    idle = [n for n in idle_src if n not in clicks][:DAILY_IDLE_COUNT]
    if not idle:
        idle = idle_src[:DAILY_IDLE_COUNT] or [n for n in IDLE_POOL if n not in drop]
    specials = [n for n in SPECIAL_CATALOG if n not in drop] or list(SPECIALS)
    return clicks, specials, idle


# DeepSeek bills peak hours on workdays; weekends are off-peak all day.
# Windows are minutes-since-midnight in Beijing time.
DEEPSEEK_PEAK_WINDOWS = ((9 * 60, 12 * 60), (14 * 60, 18 * 60))
KEYFILE_DEFAULT = r"D:\api_key.txt"


def beijing_now():
    """UTC+8 wall clock, independent of the machine's own timezone."""
    return time.gmtime(time.time() + 8 * 3600)


def deepseek_is_peak(when=None):
    """True inside DeepSeek's non-discount window (workday 9-12 and 14-18)."""
    t = beijing_now() if when is None else when
    if t.tm_wday >= 5:          # Saturday / Sunday: peak pricing never applies
        return False
    minutes = t.tm_hour * 60 + t.tm_min
    for start, end in DEEPSEEK_PEAK_WINDOWS:
        if start <= minutes < end:
            return True
    return False


def load_keys(path):
    """Read the loose ``name: value`` key file the user keeps outside the app."""
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.replace("\uff1a", ":")          # full-width colon variant
        name, sep, val = line.partition(":")
        if not sep:
            continue
        name, val = name.strip(), val.strip().split(" ")[0].strip()
        if name and val:
            out.append((name, val))
    return out


def resolve_key(spec, keys, env_name=""):
    """Explicit config value wins, then the key file, then the environment.

    ``match`` entries are searched in order, so a specific needle can be put
    ahead of a generic one when several keys in the file look similar.
    """
    if spec.get("api_key"):
        return str(spec["api_key"]).strip()
    for needle in [n.lower() for n in (spec.get("match") or [])]:
        for name, val in keys:
            if needle and needle in name.lower():
                return val
    return (os.environ.get(env_name) or "").strip() if env_name else ""


def fetch_deepseek(key, timeout=8.0):
    """CNY balance from the DeepSeek open platform."""
    import urllib.request
    req = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": "Bearer %s" % key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    infos = data.get("balance_infos") or []
    if not infos:
        raise ValueError("接口没返回余额")
    pick = None
    for item in infos:
        if str(item.get("currency") or "").upper() == "CNY":
            pick = item
            break
    pick = pick or infos[0]
    return float(pick.get("total_balance") or 0)


def fetch_glm(key, timeout=8.0):
    """Balance (CNY) from the Zhipu / bigmodel console account endpoint."""
    import urllib.request
    req = urllib.request.Request(
        "https://open.bigmodel.cn/api/biz/account/getAccountBalance",
        headers={"Authorization": "Bearer %s" % key,
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("success"):
        raise ValueError(str(data.get("msg") or "余额查询被拒绝"))
    return float(data.get("data") or 0)


def short_error(exc):
    """A short Chinese reason that never leaks the key."""
    text = str(exc)
    for token in ("Bearer ", "api_key", "sk-"):
        if token in text:
            text = text.split(token)[0].strip(" :")
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "网络超时"
    if not text:
        text = exc.__class__.__name__
    return text[:26]


def _proc_start(pid):
    """Start time of a live pid, or 0.0 when that process no longer exists."""
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001
        return 0.0


def pid_is_ours(pid, created, slack=3.0):
    """True only when `pid` is still the very process we recorded.

    Windows hands pids out again after a process exits, so "the pid exists" is
    not enough. This actually happened: pet.lock held pid 24276, that pet was
    killed, and ShellHost.exe later inherited the number — every new pet then
    refused to start because the lock looked alive. Start times tell them apart.
    """
    if not pid:
        return False
    start = _proc_start(pid)
    if not start:
        return False
    if created and abs(start - created) > slack:
        return False
    return True


def acquire_lock(base):
    """Atomic single-instance lock; handles stale locks left by a crash."""
    lock = Path(base) / "pet.lock"
    mine = os.getpid()
    started = _proc_start(mine)
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, ("%d %.3f" % (mine, started)).encode("ascii"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                parts = lock.read_text(encoding="ascii").split()
                pid = int(parts[0])
                created = float(parts[1]) if len(parts) > 1 else 0.0
            except Exception:  # noqa: BLE001
                pid, created = 0, 0.0
            if pid and not created:
                # locks written by older versions held the pid only; the file's
                # own timestamp is the moment that pet claimed it
                try:
                    created = lock.stat().st_mtime
                except OSError:
                    created = 0.0
            if pid_is_ours(pid, created):
                return False
            try:
                lock.unlink()
            except OSError:
                return False
    return False


def release_lock(base):
    try:
        (Path(base) / "pet.lock").unlink()
    except OSError:
        pass


def already_running(base):
    """True when a pet launched earlier is still alive."""
    info = _load_json(Path(base) / "pet.pid", {})
    return pid_is_ours(int(info.get("pid") or 0), float(info.get("created") or 0))


def speech_lines(d):
    """State aware chatter, so the pet says something useful rather than random noise."""
    if not d:
        return [("连不上管家了…", "bad")]
    out = []
    ap = int(d.get("ap") or 0)
    sa = d.get("study_active") or {}
    mult = float(d.get("multiplier") or 0)
    base = float(d.get("rhythm_base") or 0)
    study_min = int(d.get("study_minutes") or 0)
    gfun = int(d.get("game_minutes") or 0)
    bfun = int(d.get("bili_minutes") or 0)
    # 游戏和 B 站可能共用一个池子（config 的 shared_cap_minutes）：那种情况下
    # 额度剩余要按总量算，只看单边会把「还剩一百多分钟」误报成快用完
    shared = bool(d.get("fun_shared"))
    if shared:
        fun_cap = int(d.get("fun_cap") or 0)
        fun_min = int(d.get("fun_minutes") or 0)
        gcap = bcap = 0
    else:
        gcap = int(d.get("game_cap") or 0)
        bcap = int(d.get("bili_cap") or 0)
        fun_cap = fun_min = 0
    kind = d.get("active_kind")
    left = int((d.get("unlock_remaining") or 0) // 60)
    if sa:
        out.append(("正在做 %s，还剩 %d 分钟"
                    % (sa.get("name") or "学习",
                       (int(sa.get("remaining") or 0) + 59) // 60), "good"))
        out.append(("专注一点，我在看着你", "good"))
    if sa and sa.get("paused"):
        out.append(("计时暂停着呢，别忘了回来", "warn"))
    if not d.get("wake_time"):
        out.append(("今天还没工作打卡，达标线是 %s" % d.get("wake_window_end"), "warn"))
    if study_min == 0:
        out.append(("今天一个学习块都还没做呢", "warn"))
    if kind == "game":
        out.append(("游戏还剩 %d 分钟，记得先存档" % left, "info"))
    if kind == "bilibili":
        out.append(("B 站还剩 %d 分钟" % left, "info"))
    if kind and left <= 3:
        out.append(("快结束了，别硬拖", "bad"))
    if ap >= 3:
        out.append(("攒了 %d 点，能换 %d 分钟娱乐" % (ap, ap * 10), "info"))
    if mult >= 1.0:
        out.append(("乘数满格了，今天状态不错", "good"))
    elif base < 1.0:
        out.append(("乘数只有 %.2f，做件事能加 0.1" % mult, "warn"))
    if shared:
        if fun_cap and fun_min >= fun_cap:
            out.append(("娱乐额度用完了，明天再来", "bad"))
    else:
        if gcap and gfun >= gcap:
            out.append(("游戏额度用完了，明天再来", "bad"))
        if bcap and bfun >= bcap:
            out.append(("B 站额度用完了，明天再来", "bad"))
    if study_min >= 40:
        out.append(("今天已经学了 %d 分钟" % study_min, "good"))
    out += [(t, "chat") for t in
            ["记得喝水", "肩膀放松一点", "别缩成一团", "眼睛离屏幕远一点",
             "坐直了再干活", "渴了就去接杯水"]]
    return out


def run(base_dir):
    base = Path(base_dir)
    cfg = _load_json(base / "config.json", {})
    pcfg = cfg.get("pet") or {}
    if not pcfg.get("enabled", True):
        return
    if not acquire_lock(base):
        _log(base, "桌面宠物已经在运行，本进程退出")
        return
    try:
        import tkinter as tk
        from PIL import Image, ImageTk
        import numpy as np
    except ImportError as exc:
        _log(base, "桌面宠物缺少依赖: %r" % exc)
        return

    anim_dir = base / pcfg.get("anim_dir", "pet_anim")
    fps = float(pcfg.get("fps", 12) or 12)
    key = pcfg.get("key_color", "#ff00ff")
    port = int(cfg.get("port", 8765))
    api = "http://127.0.0.1:%d" % port
    char_h = int(pcfg.get("character_height", 151) or 151)
    try:
        bright = float(pcfg.get("brightness", 1.0) or 1.0)
    except (TypeError, ValueError):
        bright = 1.0
    facing = str(pcfg.get("facing", "right")).lower()
    speech_on = bool(pcfg.get("speech", True))
    roam_on = bool(pcfg.get("roam", True))
    span = pcfg.get("speech_interval") or [45, 110]

    # today's repertoire: same all day, different tomorrow
    if pcfg.get("daily_action_rotation", True):
        click_pool, specials, idle_pool = daily_pools(
            exclude=pcfg.get("exclude_actions") or [])
    else:
        click_pool, specials, idle_pool = list(CLICK_POOL), list(SPECIALS), list(IDLE_POOL)
    _log(base, "今日动作表：点击 %s｜彩蛋 %s｜待机 %s"
         % ("、".join(click_pool), "、".join(specials), "、".join(idle_pool)))

    root = tk.Tk()
    root.overrideredirect(True)
    root.title("学习积分管家 桌宠")
    root.attributes("-topmost", True)
    root.configure(bg=key)
    try:
        root.attributes("-transparentcolor", key)
        _log(base, "透明键控色 %s -> %r" % (key, root.attributes("-transparentcolor")))
    except Exception as exc:  # noqa: BLE001
        _log(base, "设置透明键控色失败: %r" % exc)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()

    probe = anim_dir / "idle.png"
    if not probe.exists():
        _log(base, "找不到动画素材 %s" % probe)
        root.destroy()
        return
    first = Image.open(str(probe))
    first.seek(0)
    first = first.convert("RGBA")
    alpha = np.asarray(first)[:, :, 3]
    ys, _xs = np.where(alpha > 20)
    body_h = int(ys.max() - ys.min() + 1) if len(ys) else first.height
    scale = char_h / float(body_h)
    win_w = max(40, int(round(first.width * scale)))
    win_h = max(40, int(round(first.height * scale)))
    _log(base, "宠物窗口 %dx%d（角色高 %d px，素材角色高 %d px）"
         % (win_w, win_h, char_h, body_h))

    canvas = tk.Canvas(root, width=win_w, height=win_h, bg=key,
                       highlightthickness=0, bd=0)
    canvas.pack()
    holder = canvas.create_image(win_w // 2, win_h, anchor="s")

    pos_file = base / "pet_pos.json"
    pos = _load_json(pos_file, {})
    x = int(pos.get("x", sw - win_w - 60))
    y = int(pos.get("y", sh - win_h - 90))
    x = min(max(0, x), max(0, sw - win_w))
    y = min(max(0, y), max(0, sh - win_h))
    root.geometry("%dx%d+%d+%d" % (win_w, win_h, x, y))

    try:
        import psutil
        _save_json(base / "pet.pid",
                   {"pid": os.getpid(),
                    "created": psutil.Process(os.getpid()).create_time()})
    except Exception:  # noqa: BLE001
        _save_json(base / "pet.pid", {"pid": os.getpid(), "created": 0})

    st = {"anim": "idle", "idx": 0, "once": False, "next": 0.0,
          "flip": facing == "left", "vx": 0.0, "until": 0.0, "last": time.time()}

    # PIL's APNG reader keeps a chunk sequence counter that drifts when the same
    # file is seeked over and over, so every animation is decoded to plain frames
    # once (in a worker thread) and the loop only ever indexes the result.
    frames_cache = {}
    frames_start = {}
    frames_order = []
    decoding = set()

    def decode_all(name):
        path = anim_dir / (name + ".png")
        if not path.exists():
            return None, 0
        im = Image.open(str(path))
        n = getattr(im, "n_frames", 1)
        out = []
        first = None
        diffs = []
        for i in range(n):
            im.seek(i)
            fr = im.convert("RGBA")
            arr = np.asarray(fr).astype(np.int16)
            if first is None:
                first = arr
                diffs.append(0.0)
            else:
                diffs.append(float(np.abs(arr - first).mean()))
            buf = io.BytesIO()
            fr.save(buf, format="PNG", compress_level=1)
            out.append(buf.getvalue())
        im.close()
        # These clips open with a near-identical neutral pose; a reaction should
        # not make the user sit through that lead-in. Find where it actually moves.
        peak = max(diffs) if diffs else 0.0
        start = 0
        if peak > 1.0:
            for i, d in enumerate(diffs):
                if d >= peak * 0.3:
                    start = max(0, i - 2)
                    break
        return out, start

    def ensure_frames(name):
        if name in frames_cache or name in decoding:
            return
        decoding.add(name)

        def worker():
            try:
                frames, start = decode_all(name)
                if frames:
                    frames_cache[name] = frames
                    frames_start[name] = start
                    frames_order.append(name)
                    while len(frames_order) > ANIM_CACHE_LIMIT:
                        victim = next((c for c in frames_order
                                       if c not in (st["anim"], name)), None)
                        if victim is None:
                            break
                        frames_order.remove(victim)
                        frames_cache.pop(victim, None)
                        frames_start.pop(victim, None)
            except Exception as exc:  # noqa: BLE001
                _log(base, "解码动画 %s 失败: %r" % (name, exc))
            finally:
                decoding.discard(name)

        threading.Thread(target=worker, daemon=True).start()

    def play(name, once=False):
        if not (anim_dir / (name + ".png")).exists():
            name = "idle"
        ensure_frames(name)
        st["anim"] = name
        st["idx"] = -1          # -1 = jump to the action start once frames land
        st["once"] = once
        st["next"] = 0.0

    def show(im):
        if im.size != (win_w, win_h):
            im = im.resize((win_w, win_h), Image.BILINEAR)
        # Hard alpha: a soft edge blends with the chroma key colour, and the
        # blended pixel no longer matches the key, leaving a magenta fringe.
        im.putalpha(im.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
        if bright != 1.0:
            # the pack's webm is about 10% darker than the user's own artwork;
            # lift only the colour channels so the alpha stays a clean 0/255
            from PIL import ImageEnhance
            r, g, b, a = im.split()
            rgb = ImageEnhance.Brightness(Image.merge("RGB", (r, g, b))).enhance(bright)
            im = Image.merge("RGBA", (rgb.getchannel("R"), rgb.getchannel("G"),
                                      rgb.getchannel("B"), a))
        if st["flip"]:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        photo = ImageTk.PhotoImage(im)
        canvas.itemconfig(holder, image=photo)
        canvas.image = photo

    def frame_of(name, idx):
        frames = frames_cache.get(name)
        if not frames:
            ensure_frames(name)
            return None
        return Image.open(io.BytesIO(frames[idx % len(frames)]))

    cache_data = {"d": None, "t": 0.0}

    def fetch_state():
        """Blocking fetch — only ever called from the background thread."""
        try:
            import urllib.request
            with urllib.request.urlopen(api + "/api/state", timeout=3) as r:
                cache_data["d"] = json.loads(r.read().decode("utf-8"))
                cache_data["t"] = time.time()
        except Exception:  # noqa: BLE001
            pass

    def poller():
        while True:
            fetch_state()
            time.sleep(2.0)

    poller_respawns = []

    def ensure_poller():
        """轮询线程卡住/退出了就补一个：状态框宁可重连一次，也别停在旧数据上。

        2026-10-13 23:10 前后出过这次事故：桌宠进程活着、动画照跑、余额（另一个
        线程）也在刷新，但取状态的那个线程不动了，状态框一直显示几十分钟前的
        行动点和学习计时，看起来就像"数据不对"。
        """
        if not cache_data["t"]:
            return                     # 还没成功读到过一次，别乱补
        idle = time.time() - cache_data["t"]
        if idle <= 25:
            return
        now = time.time()
        recent = [t for t in poller_respawns if now - t < 300]
        poller_respawns[:] = recent    # 5 分钟内最多补 3 个，避免无限开线程
        if len(recent) >= 3:
            return
        poller_respawns.append(now)
        threading.Thread(target=poller, daemon=True).start()
        _log(base, "状态已经 %.0f 秒没更新，重开一个轮询线程" % idle)

    def state_now(force=False):
        """Never blocks: the animation loop must not wait on the network."""
        return cache_data["d"]

    import threading
    threading.Thread(target=poller, daemon=True).start()

    # ---- balance polling: DeepSeek + Zhipu, refreshed once a minute ----------
    bcfg = cfg.get("balance") or {}
    b_every = max(30.0, float(bcfg.get("refresh_seconds", 60) or 60))
    b_keys = load_keys(bcfg.get("key_file") or KEYFILE_DEFAULT)
    providers = [
        ("deepseek", "deepseek", resolve_key(
            bcfg.get("deepseek") or {}, b_keys, "DEEPSEEK_API_KEY"), fetch_deepseek),
        ("glm", "GLM", resolve_key(
            bcfg.get("glm") or {}, b_keys, "ZHIPU_API_KEY"), fetch_glm),
    ]
    # text printed just before the 峰/谷 tag, e.g. "梁文" -> 梁文峰 / 梁文谷
    prefixes = {pid: str((bcfg.get(pid) or {}).get("label_prefix") or "")
                for pid in ("deepseek", "glm")}
    bal = {"at": 0.0}
    for _pid, _label, _key, _fn in providers:
        bal[_pid] = None
        bal[_pid + "_err"] = "" if _key else "未找到密钥"
        bal[_pid + "_delta"] = None

    def balance_loop():
        if not bcfg.get("enabled", True):
            _log(base, "余额查询已在 config.json 里关闭")
            return
        _log(base, "余额查询已启动（每 %.0f 秒一次）：%s" % (
            b_every, "、".join("%s=%s" % (l, "有密钥" if k else "缺密钥")
                               for _i, l, k, _f in providers)))
        while True:
            for pid, label, key, fn in providers:
                if not key:
                    continue
                try:
                    val = fn(key)
                except Exception as exc:  # noqa: BLE001
                    bal[pid + "_err"] = short_error(exc)
                    bal[pid + "_delta"] = None
                    continue
                old = bal[pid]
                if old is not None and abs(val - old) > 5e-9:
                    bal[pid + "_delta"] = val - old
                    _log(base, "%s 余额变化 %.4f -> %.4f 元（%+.4f）"
                         % (label, old, val, val - old))
                else:
                    bal[pid + "_delta"] = None
                if old is None:
                    _log(base, "%s 余额 %.4f 元" % (label, val))
                bal[pid] = val
                bal[pid + "_err"] = ""
            bal["at"] = time.time()
            time.sleep(b_every)

    if bcfg.get("enabled", True) and any(k for _i, _l, k, _f in providers):
        threading.Thread(target=balance_loop, daemon=True).start()

    bubble = tk.Toplevel(root)
    bubble.overrideredirect(True)
    bubble.title("学习积分管家 桌宠说话")
    bubble.attributes("-topmost", True)
    bubble.configure(bg="#0b1117")
    bframe = tk.Frame(bubble, bg=DEFAULT_TONE[0], padx=2, pady=2)
    bframe.pack()
    btext = tk.Label(bframe, text="", justify="left", anchor="w", wraplength=250,
                     font=("Microsoft YaHei", 10, "bold"), fg=DEFAULT_TONE[1],
                     bg=DEFAULT_TONE[0], padx=13, pady=9, bd=0)
    btext.pack()
    bubble.withdraw()
    bhide_at = [0.0]

    def place_bubble():
        bubble.update_idletasks()
        bw, bh = bubble.winfo_reqwidth(), bubble.winfo_reqheight()
        bx = min(max(0, root.winfo_x() + (win_w - bw) // 2), max(0, sw - bw))
        by = root.winfo_y() - bh - 6
        if by < 0:
            by = min(root.winfo_y() + win_h + 6, max(0, sh - bh))
        bubble.geometry("%dx%d+%d+%d" % (bw, bh, bx, by))

    def say(text=None, tone=None, animate=None):
        if text is None:
            text, tone = random.choice(speech_lines(state_now()))
        bg, fg = TONE_COLOR.get(tone or "", DEFAULT_TONE)
        bframe.configure(bg=bg)
        btext.configure(text=text, bg=bg, fg=fg)
        place_bubble()
        bubble.deiconify()
        bubble.lift()
        bhide_at[0] = time.time() + 6.5
        # Only switch to the talking animation when nothing else is playing —
        # otherwise the bubble would overwrite a click reaction mid-move.
        if animate is None:
            animate = st["anim"] in ("idle", "talk")
        if animate:
            play("talk", once=True)
        else:
            _log(base, "显示台词，保留正在播的 %s" % st["anim"])

    tip = tk.Toplevel(root)
    tip.overrideredirect(True)
    tip.title("学习积分管家 桌宠信息")
    tip.attributes("-topmost", True)
    tip.configure(bg="#0b1117")
    tcard = tk.Frame(tip, bg="#ffffff", padx=2, pady=2)
    tcard.pack()
    tinner = tk.Frame(tcard, bg="#ffffff", padx=12, pady=10)
    tinner.pack()
    trows = []
    for _i in range(7):             # 5 state rows + deepseek + GLM
        _row = tk.Frame(tinner, bg="#ffffff")
        _row.pack(anchor="w", fill="x")
        # fixed-width key column so the balance rows line up with the rest
        _kbox = tk.Frame(_row, bg="#ffffff", width=76, height=20)
        _kbox.pack(side="left")
        _kbox.pack_propagate(False)
        # the two balance providers keep their name bold, the state rows do not
        _kfont = ("Microsoft YaHei", 9, "bold") if _i >= 5 else ("Microsoft YaHei", 9)
        _k = tk.Label(_kbox, text="", font=_kfont, fg="#5b6b7f", bg="#ffffff",
                      anchor="w")
        _k.pack(side="left")
        _vbox = tk.Frame(_row, bg="#ffffff")
        _vbox.pack(side="left")
        _vals = []
        for _ in range(4):          # slots let one row carry several colours
            _v = tk.Label(_vbox, text="", font=("Microsoft YaHei", 11, "bold"),
                          fg="#111827", bg="#ffffff", anchor="w")
            _v.pack(side="left")
            _vals.append(_v)
        trows.append((_k, _vals))
    tip.withdraw()

    def balance_pairs():
        """deepseek / GLM rows: blue name, red 峰-谷 tag, grey change badge."""
        out = []
        for pid, label, _key, _fn in providers:
            val = bal.get(pid)
            name_color = "#1d4ed8" if pid == "deepseek" else "#000000"
            if val is None:
                why = bal.get(pid + "_err") or "查询中…"
                out.append((label, name_color, [(why, "#9ca3af")]))
                continue
            slots = [("余额：%.2f 元" % val, "#111827")]
            if pid == "deepseek":
                pre = prefixes.get("deepseek") or ""
                slots.append((" %s" % pre if pre else " ", "#111827"))
                slots.append(("峰" if deepseek_is_peak() else "谷", "#dc2626"))
            delta = bal.get(pid + "_delta")
            if delta:
                slots.append(("  %+.2f" % delta, "#6b7280"))
            out.append((label, name_color, slots))
        return out

    last_tip = [None]

    def fill_tip(d):
        if not d:
            pairs = [("状态", "#5b6b7f", [("连不上守护进程", "#dc2626")])]
        else:
            names = {t.get("id"): t.get("name") for t in (d.get("study_tasks") or [])}
            sa = d.get("study_active")
            if sa:
                secs = int(sa.get("active_seconds") or 0)
                study = "%s %02d:%02d" % (
                    sa.get("name") or names.get(sa.get("task")) or "—",
                    secs // 60, secs % 60)
                if sa.get("paused"):
                    study += "（暂停）"
            else:
                study = "未计时 · %s" % (names.get(d.get("study_task")) or "—")
            mult = float(d.get("multiplier") or 0)
            mcol = "#059669" if mult >= 1.0 else ("#d97706" if mult >= 0.6 else "#dc2626")
            pairs = [
                ("行动点", "#5b6b7f", [("%s AP" % d.get("ap", 0),
                                      "#dc2626" if int(d.get("ap") or 0) < 0
                                      else "#b45309"),
                                     ("（欠账）" if int(d.get("ap") or 0) < 0 else "",
                                      "#dc2626")]),
                ("学习", "#5b6b7f", [(study, "#047857")]),
                ("乘数", "#5b6b7f", [("%.2f" % mult, mcol)]),
            ]
            if d.get("fun_shared"):
                # 共用一个池子：先看总量，再拆开看各自用了多少
                pairs += [
                    ("娱乐", "#5b6b7f", [("%s / %s 分钟"
                                          % (d.get("fun_minutes", 0),
                                             d.get("fun_cap", 0)),
                                          "#6d28d9")]),
                    ("明细", "#5b6b7f", [("游戏 %s · B站 %s"
                                          % (d.get("game_minutes", 0),
                                             d.get("bili_minutes", 0)),
                                          "#db2777")]),
                ]
            else:
                pairs += [
                    ("游戏", "#5b6b7f", [("%s / %s 分钟"
                                          % (d.get("game_minutes", 0),
                                             d.get("game_cap", 0)),
                                          "#6d28d9")]),
                    ("B站", "#5b6b7f", [("%s / %s 分钟"
                                         % (d.get("bili_minutes", 0),
                                            d.get("bili_cap", 0)),
                                         "#db2777")]),
                ]
        pairs = pairs + balance_pairs()
        sig = tuple((k, kc, tuple(s)) for k, kc, s in pairs)
        if sig == last_tip[0]:
            return
        last_tip[0] = sig
        for i, (k, v) in enumerate(trows):
            if i < len(pairs):
                kk, kcol, slots = pairs[i]
                k.config(text=kk, fg=kcol)
                for j, lab in enumerate(v):
                    if j < len(slots):
                        lab.config(text=slots[j][0], fg=slots[j][1])
                    else:
                        lab.config(text="")
            else:
                k.config(text="")
                for lab in v:
                    lab.config(text="")

    def place_tip():
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        px, py = root.winfo_x(), root.winfo_y()
        tx = px - tw - 10
        if tx < 0:
            tx = min(px + win_w + 10, max(0, sw - tw))
        ty = min(max(0, py + (win_h - th) // 2), max(0, sh - th))
        tip.geometry("%dx%d+%d+%d" % (tw, th, tx, ty))

    def show_tip(_e=None):
        fill_tip(state_now(True))
        place_tip()
        tip.deiconify()
        tip.lift()

    def hide_tip(_e=None):
        tip.withdraw()

    drag = {"x": 0, "y": 0, "moved": False}
    dragging = [False]
    tap = {"at": 0.0, "last": None, "rapid": 0}

    def on_press(e):
        drag["x"], drag["y"], drag["moved"] = e.x, e.y, False

    def on_drag(e):
        nx = min(max(0, root.winfo_x() + e.x - drag["x"]), max(0, sw - win_w))
        ny = min(max(0, root.winfo_y() + e.y - drag["y"]), max(0, sh - win_h))
        root.geometry("+%d+%d" % (nx, ny))
        drag["moved"] = True
        if not dragging[0]:
            dragging[0] = True
            play("drag")
        if tip.winfo_viewable():
            place_tip()
        if bubble.winfo_viewable():
            place_bubble()

    def on_release(_e):
        if dragging[0]:
            dragging[0] = False
            play("idle")
        if drag["moved"]:
            _save_json(pos_file, {"x": root.winfo_x(), "y": root.winfo_y()})
            return
        now = time.time()
        tap["rapid"] = tap["rapid"] + 1 if now - tap["at"] < 1.2 else 1
        tap["at"] = now
        if tap["rapid"] >= 4:
            pick = "click_angry"          # poked too many times in a row
            tap["rapid"] = 0
        elif random.random() < 0.12:
            pick = random.choice(specials)
        else:
            pool = [c for c in click_pool if c != tap["last"]]
            pick = random.choice(pool)
        tap["last"] = pick
        play(pick, once=True)
        _log(base, "点击回应：%s" % pick)
        if speech_on:
            lines = MOOD_LINES.get(pick)
            say(random.choice(lines) if lines else None, tone=pick)

    def go_home():
        hx = max(0, sw - win_w - 60)
        hy = max(0, sh - win_h - 90)
        root.geometry("+%d+%d" % (hx, hy))
        _save_json(pos_file, {"x": hx, "y": hy})

    def start_roam(_e=None):
        st["vx"] = random.choice([-1, 1]) * random.uniform(26.0, 42.0)
        st["until"] = time.time() + random.uniform(2.2, 4.0)
        st["flip"] = (st["vx"] < 0) if facing == "right" else (st["vx"] > 0)
        play("walk")

    def on_hide():
        try:
            import urllib.request
            req = urllib.request.Request(
                api + "/api/action",
                data=json.dumps({"action": "hide_pet"}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=6).read()
        except Exception:  # noqa: BLE001
            pass
        root.destroy()

    def open_panel():
        """打开积分面板。

        桌宠是提权进程，自己拉起浏览器会把浏览器也变成管理员权限；交给桌面壳
        （explorer.exe）去开，打开的才是平时那个正常权限的浏览器。
        """
        url = api + "/"
        how = "explorer"
        exe = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                           "explorer.exe")
        try:
            subprocess.Popen([exe, url])
        except Exception as exc:  # noqa: BLE001
            _log(base, "交给 explorer 打开面板失败（%r），改用 webbrowser" % exc)
            how = "webbrowser"
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception as exc2:  # noqa: BLE001
                _log(base, "打开积分面板失败：%r" % exc2)
                say("面板打不开…", tone="bad")
                return
        _log(base, "从右键菜单打开积分面板（%s，%s）" % (how, url))
        if speech_on:
            say("面板给你打开了", tone="info")

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="打开积分面板", command=open_panel)
    menu.add_separator()
    menu.add_command(label="说句话", command=lambda: say())
    menu.add_command(label="走两步", command=start_roam)
    menu.add_command(label="回到右下角", command=go_home)
    menu.add_separator()
    menu.add_command(label="收起", command=on_hide)

    def on_right(e):
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Button-3>", on_right)
    for widget in (root, canvas):
        widget.bind("<Enter>", show_tip)
        widget.bind("<Leave>", hide_tip)

    next_speech = [time.time() + random.uniform(span[0], span[1])]
    idle_span = pcfg.get("idle_action_interval") or [50, 120]
    next_idle = [time.time() + random.uniform(idle_span[0], idle_span[1])]
    next_roam = [time.time() + random.uniform(20, 50)]
    last_blocks = [None]
    frame_dt = 1.0 / max(1.0, fps)

    def tick():
        try:
            _tick_once()
        except Exception:  # noqa: BLE001
            import traceback
            _log(base, "宠物循环出错:\n" + traceback.format_exc())
            root.after(500, tick)

    def _tick_once():
        now = time.time()
        frames = frames_cache.get(st["anim"])
        if frames is None:
            ensure_frames(st["anim"])
        else:
            if st["idx"] < 0:
                st["idx"] = frames_start.get(st["anim"], 0) if st["once"] else 0
        if frames is not None and now >= st["next"]:
            st["next"] = now + frame_dt
            n = len(frames)
            frame = frame_of(st["anim"], st["idx"])
            if frame is not None:
                show(frame)
            st["idx"] += 1
            if st["idx"] >= n:
                if st["once"]:
                    play("idle")
                else:
                    st["idx"] = 0
        dt = min(0.25, max(0.0, now - st["last"]))
        st["last"] = now
        if st["vx"] and now < st["until"]:
            nx = root.winfo_x() + int(round(st["vx"] * dt))
            if nx < 0 or nx > sw - win_w:
                st["vx"] = -st["vx"]
                st["flip"] = not st["flip"]
                nx = min(max(0, nx), max(0, sw - win_w))
            root.geometry("+%d+%d" % (nx, root.winfo_y()))
            if tip.winfo_viewable():
                place_tip()
            if bubble.winfo_viewable():
                place_bubble()
        elif st["vx"]:
            st["vx"] = 0.0
            play("idle")
            _save_json(pos_file, {"x": root.winfo_x(), "y": root.winfo_y()})

        d = state_now()
        if d is not None:
            # wait for the first real reading, otherwise a cold start looks like
            # a brand new study block and the pet celebrates on launch
            blocks = int(d.get("study_blocks") or 0)
            if last_blocks[0] is None:
                last_blocks[0] = blocks
            elif blocks > last_blocks[0]:
                last_blocks[0] = blocks
                play("celebrate", once=True)
                say("学习块完成，漂亮", tone="good")

        if bubble.winfo_viewable() and now > bhide_at[0]:
            bubble.withdraw()
            if st["anim"] == "talk":
                play("idle")
        if speech_on and now >= next_speech[0] and not dragging[0]:
            next_speech[0] = now + random.uniform(span[0], span[1])
            if not bubble.winfo_viewable() and st["anim"] == "idle":
                say()
        # idle animations run on their own timer so they are not skipped by the
        # speech roll; the pet does something small every minute or two
        if (now >= next_idle[0] and not dragging[0] and st["anim"] == "idle"
                and not bubble.winfo_viewable()):
            next_idle[0] = now + random.uniform(idle_span[0], idle_span[1])
            pick = random.choice(IDLE_POOL)
            play(pick, once=True)
            _log(base, "待机小动作：%s" % pick)
        if roam_on and now >= next_roam[0] and st["anim"] == "idle" and not dragging[0]:
            next_roam[0] = now + random.uniform(45, 120)
            if random.random() < 0.5:
                start_roam()
        ensure_poller()
        if tip.winfo_viewable():
            fill_tip(state_now())
            place_tip()
        root.after(30, tick)

    play("idle")
    # pre-decode the reactions so the first click answers immediately
    for _i, _name in enumerate(["idle"] + CLICK_POOL + SPECIALS + IDLE_POOL):
        root.after(1100 * _i, lambda n=_name: ensure_frames(n))
    tick()
    _log(base, "桌面宠物已显示（%dx%d）" % (win_w, win_h))
    try:
        root.mainloop()
    except Exception:  # noqa: BLE001
        pass
    try:
        (base / "pet.pid").unlink()
    except OSError:
        pass
    release_lock(base)
    _log(base, "桌面宠物已关闭")
