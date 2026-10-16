#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 dsh-pet 的 webm 素材重建桌宠动画（APNG）。

背景
----
仓库里**不包含** pet_anim/*.png（18 段动画合计约 239 MB），只放了一份清单
pet_anim/manifest.json。这个脚本按清单把素材重新生成一遍。

素材来源
--------
https://github.com/PC2005-cloud/dsh-pet  （MIT License, Copyright (c) 2026 PC2005-cloud）

    git clone https://github.com/PC2005-cloud/dsh-pet.git
    # 需要的文件在 dsh-pet/dsh-pet/assets/webm/ 下，共 100 多段 .webm（VP9 + alpha）

用法
----
    python make_pet_anim.py --dsh-pet "D:\\dsh-pet\\dsh-pet"
    python make_pet_anim.py --dsh-pet ... --only idle,click_happy   # 只生成几段
    python make_pet_anim.py --dsh-pet ... --ffmpeg "D:\\ffmpeg\\bin\\ffmpeg.exe"

需要系统里有 ffmpeg（或 --ffmpeg 指定路径）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANIM_DIR = HERE.parent / "pet_anim"
MANIFEST = ANIM_DIR / "manifest.json"


def build_one(ffmpeg: str, src: Path, dst: Path, size: tuple[int, int],
              frames: int, fps: int) -> None:
    w, h = size
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"scale={w}:{h},fps={fps},format=rgba",
        "-frames:v", str(frames),
        "-plays", "0",          # APNG 无限循环
        "-f", "apng",
        str(dst),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="重建桌宠 APNG 动画")
    ap.add_argument("--dsh-pet", required=True,
                    help="dsh-pet 仓库的根目录（里面要有 assets/webm）")
    ap.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 可执行文件路径")
    ap.add_argument("--fps", type=int, default=12, help="输出帧率，默认 12")
    ap.add_argument("--only", default="",
                    help="逗号分隔的动画键名，只生成这些（默认全部）")
    ap.add_argument("--force", action="store_true", help="已存在的文件也重新生成")
    args = ap.parse_args()

    webm_dir = Path(args.dsh_pet).expanduser() / "assets" / "webm"
    if not webm_dir.is_dir():
        print(f"找不到素材目录：{webm_dir}", file=sys.stderr)
        print("请确认 --dsh-pet 指向 dsh-pet 仓库的根目录（clone 后里面还有一层 dsh-pet/）。",
              file=sys.stderr)
        return 1

    if shutil.which(args.ffmpeg) is None and not Path(args.ffmpeg).exists():
        print(f"找不到 ffmpeg：{args.ffmpeg}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    ANIM_DIR.mkdir(parents=True, exist_ok=True)
    ok = skip = fail = 0
    for key, spec in manifest.items():
        if only and key not in only:
            continue
        src = webm_dir / f"{spec['source']}.webm"
        dst = ANIM_DIR / spec["file"]
        if dst.exists() and not args.force:
            print(f"[跳过] {key:16s} {dst.name} 已存在")
            skip += 1
            continue
        if not src.is_file():
            print(f"[缺失] {key:16s} 找不到 {src.name}", file=sys.stderr)
            fail += 1
            continue
        try:
            build_one(args.ffmpeg, src, dst, tuple(spec["size"]),
                      int(spec["frames"]), args.fps)
        except subprocess.CalledProcessError as exc:
            print(f"[失败] {key:16s} ffmpeg 返回 {exc.returncode}", file=sys.stderr)
            fail += 1
            continue
        mb = dst.stat().st_size / 1024 / 1024
        print(f"[完成] {key:16s} {dst.name}  {mb:.1f} MB")
        ok += 1

    print(f"\n完成 {ok} 个，跳过 {skip} 个，失败 {fail} 个。")
    if ok or skip:
        print("现在直接启动 guard.py，桌宠就会用上这些动画。")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
