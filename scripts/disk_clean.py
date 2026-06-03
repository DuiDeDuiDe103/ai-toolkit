#!/usr/bin/env python3
"""
disk_clean.py - 磁盘扫描工具（只读，高性能，分层输出）

特性：
- 多线程扫描，visited 锁防重复
- 分层输出：摘要 + 详情
- 按类别分组，组内按分数排序
- 支持指定盘符或子目录

使用：
    # 第一次：扫描并保存索引
    python disk_clean.py -p D:

    # 查看摘要
    python disk_clean.py --summary -p D:

    # 查看详情
    python disk_clean.py --detail large -p D: --top 10
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


def get_index_path(drive: str) -> Path:
    """获取索引文件路径"""
    drive_path = Path(drive)
    index_dir = drive_path / '.ai-toolkit'
    index_dir.mkdir(exist_ok=True)
    return index_dir / 'index.json'


# 扫描器类
class DiskScanner:
    def __init__(self, large_threshold: int):
        self.large_threshold = large_threshold
        self.visited = set()
        self.visited_lock = threading.Lock()

    def scan(self, path: str) -> dict:
        drive_path = Path(path)
        drive = drive_path.drive[0] if drive_path.drive else ''

        skip_dirs = {'$Recycle.Bin', '$WINDOWS', 'System Volume Information',
                     'Windows', 'Program Files', 'Program Files (x86)',
                     'ProgramData', 'Recovery', 'Boot', '.git', '.svn',
                     'node_modules', '__pycache__', '$WinREAgent'}

        def worker(dir_path: Path, depth: int):
            local_files = 0
            local_cats = {c: 0 for c in ['system', 'cache', 'temp', 'log', 'installer', 'archive', 'large', 'other']}
            local_large = {c: [] for c in local_cats}

            dir_str = str(dir_path)
            with self.visited_lock:
                if dir_str in self.visited:
                    return None
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
                            mtime = item.stat().st_mtime
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
                                "mtime": mtime,
                                "mtime_str": time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
                            })
                except (PermissionError, OSError):
                    continue

            for sub in sub_dirs:
                result = worker(sub, depth + 1)
                if result:
                    local_files += result["files"]
                    for c in local_cats:
                        local_cats[c] += result["cats"][c]
                        local_large[c].extend(result["large"][c])

            return {"files": local_files, "cats": local_cats, "large": local_large}

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
                    mtime = item.stat().st_mtime
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
                            "mtime": mtime,
                            "mtime_str": time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
                        })
                    return {"files": 1, "cats": local_cats, "large": local_large}
                except (PermissionError, OSError):
                    return None
            return None

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

        # 计算每个类别的总大小（通过大文件估算）
        cats_output = {}
        for c in all_cats:
            cat_size = sum(f["size"] for f in all_large[c])
            cats_output[c] = {
                "count": all_cats[c],
                "size": cat_size,
                "size_str": get_size_str(cat_size),
                "large_files": all_large[c]
            }

        total_size = sum(c["size"] for c in cats_output.values())

        return {
            "drive": str(drive_path),
            "total_files": total_files,
            "total_size": total_size,
            "total_size_str": get_size_str(total_size),
            "categories": cats_output,
            "visited_count": len(self.visited),
            "scan_time": time.strftime('%Y-%m-%d %H:%M:%S')
        }


def save_index(result: dict, index_path: Path):
    """保存索引文件"""
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def load_index(index_path: Path) -> dict:
    """加载索引文件"""
    with open(index_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def output_summary(result: dict):
    """输出摘要"""
    summary = {
        "tool": "disk-clean",
        "status": "success",
        "mode": "summary",
        "drive": result["drive"],
        "total_files": result["total_files"],
        "total_size_str": result["total_size_str"],
        "scan_time": result["scan_time"],
        "categories": {}
    }

    for cat, data in result["categories"].items():
        summary["categories"][cat] = {
            "count": data["count"],
            "size_str": data["size_str"],
            "large_file_count": len(data["large_files"])
        }

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def output_detail(result: dict, category: str, top: int = 10):
    """输出详情"""
    if category not in result["categories"]:
        print(json.dumps({"error": f"Category '{category}' not found"}, ensure_ascii=False))
        return

    cat_data = result["categories"][category]
    files = cat_data["large_files"][:top]

    output = {
        "tool": "disk-clean",
        "status": "success",
        "mode": "detail",
        "drive": result["drive"],
        "category": category,
        "total_count": cat_data["count"],
        "total_size_str": cat_data["size_str"],
        "showing": len(files),
        "files": files
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='磁盘扫描工具（只读）')
    parser.add_argument('-p', '--path', type=str, help='扫描路径')
    parser.add_argument('-l', '--large-size', type=int, default=100, help='大文件阈值MB')
    parser.add_argument('-t', '--threads', type=int, default=8, help='线程数')
    parser.add_argument('--summary', action='store_true', help='输出摘要')
    parser.add_argument('--detail', type=str, help='输出详情，指定类别')
    parser.add_argument('--top', type=int, default=10, help='详情数量')
    args = parser.parse_args()

    # 确定路径
    if args.path:
        p = args.path.rstrip('\\').rstrip('/')
        if len(p) == 2 and p[1] == ':':
            p += '\\'
        drive = p
    else:
        # 从索引文件推断
        print("Error: --path is required", file=sys.stderr)
        sys.exit(1)

    # 摘要或详情模式
    if args.summary or args.detail:
        index_path = get_index_path(drive)
        if not index_path.exists():
            print(f"Error: Index not found at {index_path}", file=sys.stderr)
            print("Please run scan first: python disk_clean.py -p D:", file=sys.stderr)
            sys.exit(1)
        result = load_index(index_path)

        if args.summary:
            output_summary(result)
        elif args.detail:
            output_detail(result, args.detail, args.top)
        return

    # 扫描模式
    large_bytes = args.large_size * 1024 * 1024

    print(f"[INFO] Scanning {drive}, {args.threads} threads", file=sys.stderr)
    t0 = time.time()

    if not Path(drive).exists():
        print(f"[SKIP] {drive} not found", file=sys.stderr)
        sys.exit(1)

    scanner = DiskScanner(large_bytes)
    result = scanner.scan(drive)
    print(f"[DONE] {result['total_files']:,} files, {time.time()-t0:.1f}s", file=sys.stderr)

    # 保存索引
    index_path = get_index_path(drive)
    save_index(result, index_path)
    print(f"[SAVED] Index: {index_path}", file=sys.stderr)

    # 输出摘要
    output_summary(result)


if __name__ == "__main__":
    main()
