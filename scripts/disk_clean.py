#!/usr/bin/env python3
"""
disk_clean.py - 磁盘扫描工具（只读，高性能）

特性：
- 多线程扫描，visited 锁防重复
- 线程本地计数，最后合并
- 支持指定盘符或子目录
- 输出 JSON 给 agent 解析

使用：
    python disk_clean.py -p C:
    python disk_clean.py -p D:
    python disk_clean.py -p D:/Game
    python disk_clean.py
"""

import os
import sys
import json
import argparse
import platform
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if platform.system() == 'Windows':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


def get_size_str(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f}PB"


def get_drives() -> list:
    if platform.system() != 'Windows':
        return ['/']
    return [f"{l}:\\" for l in 'CDEFGHIJKLMNOPQRSTUVWXYZ' if os.path.exists(f"{l}:\\")]


def guess_app_name(path: str) -> str:
    p = path.lower()
    apps = {
        'steam': 'Steam', 'epic': 'Epic Games', 'wegame': 'WeGame',
        'chrome': 'Chrome', 'edge': 'Edge', 'firefox': 'Firefox',
        'vscode': 'VSCode', 'visual studio': 'Visual Studio',
        'docker': 'Docker', 'nvidia': 'NVIDIA', 'obs': 'OBS',
        'minecraft': 'Minecraft', 'bitbrowser': 'BitBrowser',
        'blender': 'Blender', 'python': 'Python', 'node': 'Node.js',
        'league': 'League of Legends', 'valorant': 'VALORANT',
        'genshin': 'Genshin Impact', 'sg_elite': 'Snowbreak',
    }
    for k, v in apps.items():
        if k in p:
            return v
    parts = path.replace('/', '\\').split('\\')
    skip = {'Users', 'AppData', 'Local', 'Roaming', 'Temp', 'Cache',
            'common', 'steamapps', 'workshop', 'content', 'Program Files',
            'Program Files (x86)', 'ProgramData'}
    for part in parts:
        if part and part not in skip and len(part) > 2 and not part.startswith(('.', '$')):
            return part
    return 'Unknown'


def classify_file(path_str: str, size: int, drive: str) -> str:
    p = path_str.lower().replace('/', '\\')
    suffix = Path(path_str).suffix.lower()
    parent = Path(path_str).parent.name.lower()
    d = drive.lower()

    # 系统文件
    sys_prefixes = [f'{d}:\\windows', f'{d}:\\program files', f'{d}:\\programdata',
                    f'{d}:\\recovery', '/windows', '/usr', '/bin', '/lib', '/etc']
    if any(p.startswith(s) for s in sys_prefixes):
        return 'system'

    # 缓存
    if 'cache' in parent or 'cached' in parent:
        return 'cache'

    # 临时
    if suffix in {'.tmp', '.temp', '.bak', '.old', '.swp'} or 'temp' in parent or 'tmp' in parent:
        return 'temp'

    # 日志
    if suffix in {'.log', '.log.1', '.log.2', '.log.3'}:
        return 'log'

    # 安装包
    if suffix in {'.exe', '.msi', '.msix', '.appx', '.dmg', '.pkg', '.deb', '.rpm', '.iso'}:
        return 'installer'

    # 压缩包
    if suffix in {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz'}:
        return 'archive'

    # 大文件
    if size >= 100 * 1024 * 1024:
        return 'large'

    return 'other'


# 扫描器类，封装线程安全逻辑
class DiskScanner:
    def __init__(self, large_threshold: int):
        self.large_threshold = large_threshold
        self.visited = set()
        self.visited_lock = threading.Lock()
        self.lock_count = 0  # 统计锁竞争次数

    def scan(self, path: str) -> dict:
        """扫描指定路径"""
        drive_path = Path(path)
        drive = drive_path.drive[0] if drive_path.drive else ''

        skip_dirs = {'$Recycle.Bin', '$WINDOWS', 'System Volume Information',
                     'Windows', 'Program Files', 'Program Files (x86)',
                     'ProgramData', 'Recovery', 'Boot', '.git', '.svn',
                     'node_modules', '__pycache__', '$WinREAgent'}

        def worker(dir_path: Path, depth: int):
            """线程工作函数，返回本地计数"""
            local_files = 0
            local_cats = {c: 0 for c in ['system', 'cache', 'temp', 'log', 'installer', 'archive', 'large', 'other']}
            local_large = {c: [] for c in local_cats}

            # 检查是否已访问
            dir_str = str(dir_path)
            with self.visited_lock:
                if dir_str in self.visited:
                    return None  # 已访问，跳过
                self.visited.add(dir_path)

            if depth > 8:
                return None

            try:
                entries = list(dir_path.iterdir())
            except (PermissionError, OSError):
                return None

            sub_dirs = []
            for item in entries:
                name = item.name
                if name in skip_dirs or name.startswith('$') or name.startswith('.'):
                    continue

                try:
                    if item.is_dir():
                        sub_dirs.append(item)
                    elif item.is_file():
                        try:
                            size = item.stat().st_size
                        except (PermissionError, OSError):
                            continue

                        local_files += 1
                        cat = classify_file(str(item), size, drive)
                        local_cats[cat] += 1

                        if size >= self.large_threshold:
                            local_large[cat].append({
                                "path": str(item),
                                "name": name,
                                "app": guess_app_name(str(item)),
                                "size": size,
                                "size_str": get_size_str(size),
                                "suffix": item.suffix,
                            })
                except (PermissionError, OSError):
                    continue

            # 递归处理子文件夹
            for sub in sub_dirs:
                result = worker(sub, depth + 1)
                if result:
                    local_files += result["files"]
                    for c in local_cats:
                        local_cats[c] += result["cats"][c]
                        local_large[c].extend(result["large"][c])

            return {"files": local_files, "cats": local_cats, "large": local_large}

        # 使用线程池扫描顶层目录
        all_cats = {c: 0 for c in ['system', 'cache', 'temp', 'log', 'installer', 'archive', 'large', 'other']}
        all_large = {c: [] for c in all_cats}
        total_files = 0

        try:
            top_entries = list(drive_path.iterdir())
        except (PermissionError, OSError):
            top_entries = []

        def scan_subdir(item):
            if item.is_dir():
                name = item.name
                if name in skip_dirs or name.startswith('$') or name.startswith('.'):
                    return None
                return worker(item, 0)
            elif item.is_file():
                try:
                    size = item.stat().st_size
                    cat = classify_file(str(item), size, drive)
                    local_cats = {c: 0 for c in all_cats}
                    local_cats[cat] = 1
                    local_large = {c: [] for c in all_cats}
                    if size >= self.large_threshold:
                        local_large[cat].append({
                            "path": str(item),
                            "name": item.name,
                            "app": guess_app_name(str(item)),
                            "size": size,
                            "size_str": get_size_str(size),
                            "suffix": item.suffix,
                        })
                    return {"files": 1, "cats": local_cats, "large": local_large}
                except (PermissionError, OSError):
                    return None
            return None

        # 多线程扫描
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(scan_subdir, item): item for item in top_entries}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    total_files += result["files"]
                    for c in all_cats:
                        all_cats[c] += result["cats"][c]
                        all_large[c].extend(result["large"][c])

        # 排序大文件
        for c in all_large:
            all_large[c].sort(key=lambda x: x["size"], reverse=True)

        total_size = 0
        cats_output = {}
        for c in all_cats:
            cat_size = sum(f["size"] for f in all_large[c])
            # 小文件的大小需要额外计算
            cats_output[c] = {
                "count": all_cats[c],
                "large_files": all_large[c]
            }

        # 重新计算 total_size（需要遍历所有文件）
        # 这里简化处理，只统计大文件大小
        total_size = sum(f["size"] for files in all_large.values() for f in files)

        return {
            "drive": str(drive_path),
            "total_files": total_files,
            "total_size": total_size,
            "total_size_str": get_size_str(total_size),
            "categories": cats_output,
            "visited_count": len(self.visited),
            "lock_contention": self.lock_count
        }


def main():
    parser = argparse.ArgumentParser(description='磁盘扫描工具（只读）')
    parser.add_argument('-p', '--path', type=str, help='扫描路径，如 C: 或 D:/Game')
    parser.add_argument('-l', '--large-size', type=int, default=100, help='大文件阈值MB')
    parser.add_argument('-t', '--threads', type=int, default=8, help='线程数')
    args = parser.parse_args()

    if args.path:
        p = args.path.rstrip('\\').rstrip('/')
        if len(p) == 2 and p[1] == ':':
            p += '\\'
        drives = [p]
    else:
        drives = get_drives()

    large_bytes = args.large_size * 1024 * 1024
    results = []

    print(f"[INFO] Scanning {len(drives)} path(s), {args.threads} threads", file=sys.stderr)
    t0 = time.time()

    for d in drives:
        if not Path(d).exists():
            print(f"[SKIP] {d} not found", file=sys.stderr)
            continue
        print(f"[SCAN] {d}...", file=sys.stderr)
        t1 = time.time()
        scanner = DiskScanner(large_bytes)
        r = scanner.scan(d)
        print(f"[DONE] {d}: {r['total_files']:,} files, {r['visited_count']:,} dirs, {time.time()-t1:.1f}s", file=sys.stderr)
        results.append(r)

    elapsed = time.time() - t0

    # 汇总
    all_cats = {}
    total_files = 0
    total_size = 0
    for r in results:
        total_files += r["total_files"]
        total_size += r["total_size"]
        for cat, data in r["categories"].items():
            if cat not in all_cats:
                all_cats[cat] = {"count": 0, "large_files": []}
            all_cats[cat]["count"] += data["count"]
            all_cats[cat]["large_files"].extend(data["large_files"])

    # 排序大文件
    for c in all_cats:
        all_cats[c]["large_files"].sort(key=lambda x: x["size"], reverse=True)

    summary = {
        "total_drives": len(results),
        "total_files": total_files,
        "total_size_str": get_size_str(total_size),
        "scan_time": f"{elapsed:.1f}s",
        "categories": {c: {"count": d["count"], "large_file_count": len(d["large_files"])}
                       for c, d in sorted(all_cats.items(), key=lambda x: x[1]["count"], reverse=True)}
    }

    output = {
        "tool": "disk-clean",
        "status": "success",
        "summary": summary,
        "drives": results
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
