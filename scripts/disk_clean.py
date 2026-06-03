#!/usr/bin/env python3
"""
disk_clean.py - 磁盘扫描工具（只读，高性能，分层输出）

特性：
- 多线程扫描，visited 锁防重复
- 分层输出：摘要 + 详情
- 对比模式：显示新增/删除/变化
- 实时进度显示

使用：
    # 扫描并保存索引
    python disk_clean.py -p D:

    # 查看摘要
    python disk_clean.py --summary -p D:

    # 查看详情
    python disk_clean.py --detail large --top 10 -p D:

    # 对比模式
    python disk_clean.py --diff -p D:
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

# 按文件类型的大文件阈值（字节）
# 视频素材 1GB 很正常，日志 100MB 就可疑
TYPE_LARGE_THRESHOLDS = {
    'large_video': 1024 * 1024 * 1024,    # 视频：1GB
    'large_game': 512 * 1024 * 1024,      # 游戏数据：512MB
    'large_vm': 512 * 1024 * 1024,        # 虚拟机：512MB
    'large_model': 512 * 1024 * 1024,     # 模型/设计：512MB
    'large_image': 1024 * 1024 * 1024,    # 镜像：1GB
    'large_other': 512 * 1024 * 1024,     # 其他大文件：512MB
    'installer': 50 * 1024 * 1024,        # 安装包：50MB
    'archive': 100 * 1024 * 1024,         # 压缩包：100MB
    'log': 10 * 1024 * 1024,              # 日志：10MB
    'temp': 10 * 1024 * 1024,             # 临时文件：10MB
}
DEFAULT_LARGE_THRESHOLD = 100 * 1024 * 1024  # 默认：100MB


def get_large_threshold(cat: str) -> int:
    """获取某类别的大文件阈值"""
    return TYPE_LARGE_THRESHOLDS.get(cat, DEFAULT_LARGE_THRESHOLD)


def get_size_str(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f}PB"


def get_drives() -> list:
    """获取可用磁盘/挂载点"""
    if platform.system() == 'Windows':
        return [f"{l}:\\" for l in 'CDEFGHIJKLMNOPQRSTUVWXYZ' if os.path.exists(f"{l}:\\")]
    elif platform.system() == 'Darwin':  # macOS
        volumes = Path('/Volumes')
        if volumes.exists():
            return [str(v) for v in volumes.iterdir() if v.is_dir()]
        return ['/']
    else:  # Linux
        return ['/']


def normalize_path(path_str: str) -> str:
    """标准化路径，统一使用 /"""
    return os.path.normpath(path_str).replace('\\', '/')


def guess_app_name(path: str) -> str:
    """根据路径段猜测应用名"""
    p = normalize_path(path).lower()
    parts = set(p.split('/'))

    # 跨平台应用映射
    app_markers = {
        # 通用
        'steam': 'Steam', 'steamapps': 'Steam',
        'epic games': 'Epic Games', 'epicgames': 'Epic Games',
        'wegame': 'WeGame', 'docker': 'Docker',
        'nvidia': 'NVIDIA', 'obs-studio': 'OBS',
        'minecraft': 'Minecraft', 'blender': 'Blender',
        'python': 'Python', 'node': 'Node.js', 'nodejs': 'Node.js',
        'visual studio code': 'VSCode', 'vscode': 'VSCode',
        'visual studio': 'Visual Studio',
        'google': 'Google', 'chrome': 'Chrome',
        'mozilla': 'Mozilla', 'firefox': 'Firefox',
        'spotify': 'Spotify', 'slack': 'Slack', 'discord': 'Discord',
        # Linux/macOS 特定
        'libreoffice': 'LibreOffice', 'gimp': 'GIMP',
        'vlc': 'VLC', 'telegram': 'Telegram',
        'thunderbird': 'Thunderbird',
    }

    # 逐段匹配
    for part in parts:
        for marker, name in app_markers.items():
            if part == marker or part.startswith(marker):
                return name

    # 兜底：取第一个有意义的路径段
    skip = {'Users', 'user', 'home', 'AppData', 'Local', 'Roaming',
            'Temp', 'Cache', 'common', 'steamapps', 'workshop', 'content',
            'Program Files', 'Program Files (x86)', 'ProgramData',
            'Downloads', 'Desktop', 'Documents', '.config', '.local',
            'opt', 'usr', 'var', 'tmp'}
    for part in p.split('/'):
        if part and part not in skip and len(part) > 2 and not part.startswith(('.', '$')):
            return part
    return 'Unknown'


def get_system_prefixes(drive: str) -> list:
    """获取系统路径前缀（跨平台）"""
    d = drive.lower().rstrip('/')
    system = platform.system()

    if system == 'Windows':
        return [
            f'{d}/windows', f'{d}/program files',
            f'{d}/program files (x86)', f'{d}/programdata',
            f'{d}/recovery',
        ]
    elif system == 'Darwin':  # macOS
        return [
            '/system', '/library', '/usr', '/bin', '/sbin',
            '/private', '/cores',
        ]
    else:  # Linux
        return [
            '/usr', '/bin', '/sbin', '/lib', '/lib64',
            '/etc', '/boot', '/dev', '/proc', '/sys',
            '/var/lib', '/var/cache',
        ]


def classify_file(path_str: str, size: int, drive: str) -> str:
    """
    分类文件，大文件按类型细分（跨平台）

    返回类别：
    - system: 系统文件
    - cache: 缓存文件
    - temp: 临时文件
    - log: 日志文件
    - installer: 安装包
    - archive: 压缩包
    - large_video: 大视频文件
    - large_game: 大游戏数据
    - large_vm: 虚拟机文件
    - large_model: 模型文件
    - large_image: 镜像文件
    - large_other: 其他大文件
    - other: 其他文件
    """
    p = normalize_path(path_str).lower()
    suffix = Path(path_str).suffix.lower()
    parent = Path(path_str).parent.name.lower()
    stem = Path(path_str).stem.lower()

    # 系统文件（跨平台）
    sys_prefixes = get_system_prefixes(drive)
    if any(p.startswith(s) for s in sys_prefixes):
        return 'system'

    # 缓存
    if 'cache' in parent or 'cached' in parent:
        return 'cache'

    # 临时文件
    if suffix in {'.tmp', '.temp', '.bak', '.old', '.swp'} or 'temp' in parent or 'tmp' in parent:
        return 'temp'

    # 日志
    if suffix in {'.log', '.log.1', '.log.2', '.log.3'}:
        return 'log'

    # 安装包识别（多条件，跨平台）
    installer_keywords = {'setup', 'install', 'uninstall', 'update', 'upgrade',
                         'patch', 'downloader', 'bootstrapper', 'wizard', 'deploy'}

    # Windows 安装包
    if suffix in {'.msi', '.msix', '.appx'}:
        return 'installer'
    if suffix == '.exe':
        if any(kw in stem for kw in installer_keywords):
            return 'installer'
        if any(d in p for d in ['/downloads', '/desktop']) and size > 10 * 1024 * 1024:
            return 'installer'

    # Linux 包管理
    if suffix in {'.deb', '.rpm', '.pkg', '.appimage', '.snap', '.flatpak'}:
        return 'installer'

    # macOS 安装包
    if suffix in {'.dmg', '.pkg'}:
        return 'installer'

    # 压缩包
    if suffix in {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.zst'}:
        return 'archive'

    # 大文件细分（≥100MB）
    if size >= 100 * 1024 * 1024:
        # 视频文件
        if suffix in {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'}:
            return 'large_video'

        # 游戏数据
        game_suffixes = {'.pak', '.vpk', '.cpk', '.bdt', '.bhd', '.bhf', '.arc'}
        game_dirs = ['steam', 'epic', 'wegame', 'origin', 'ubisoft', 'blizzard',
                     'games', 'steamapps', 'common']
        if suffix in game_suffixes:
            return 'large_game'
        if suffix in {'.bin', '.dat', '.idx'} and any(d in p for d in game_dirs):
            return 'large_game'

        # 虚拟机文件
        if suffix in {'.vmdk', '.vhdx', '.vdi', '.qcow2', '.vhd', '.hdd'}:
            return 'large_vm'

        # 模型/设计文件
        if suffix in {'.obj', '.fbx', '.stl', '.blend', '.3ds', '.max', '.ma', '.mb', '.dwg', '.dxf'}:
            return 'large_model'

        # 镜像文件
        if suffix in {'.img', '.iso', '.dd', '.raw', '.dmg'}:
            return 'large_image'

        # 其他大文件
        return 'large_other'

    return 'other'


def get_index_path(drive: str) -> Path:
    """获取索引文件路径（保存到用户目录）"""
    # 使用用户主目录下的 .ai-toolkit 目录
    home = Path.home()
    index_dir = home / '.ai-toolkit' / 'indexes'
    index_dir.mkdir(parents=True, exist_ok=True)

    # 将路径转换为文件名（替换特殊字符）
    safe_name = drive.replace('\\', '_').replace('/', '_').replace(':', '').replace('.', '_')
    return index_dir / f'{safe_name}.json'


def get_history_path(drive: str) -> Path:
    """获取历史索引路径"""
    home = Path.home()
    history_dir = home / '.ai-toolkit' / 'history'
    history_dir.mkdir(parents=True, exist_ok=True)

    safe_name = drive.replace('\\', '_').replace('/', '_').replace(':', '').replace('.', '_')
    return history_dir / f'{safe_name}.json'


class DiskScanner:
    def __init__(self, large_threshold: int):
        self.large_threshold = large_threshold
        self.visited = set()
        self.visited_lock = threading.Lock()

    def scan(self, path: str) -> dict:
        drive_path = Path(path)
        # 跨平台获取 drive 标识
        if platform.system() == 'Windows':
            drive = drive_path.drive[0] if drive_path.drive else ''
        else:
            drive = '/'  # Linux/macOS 根目录

        # 跨平台跳过的目录
        skip_dirs = {
            # Windows
            '$Recycle.Bin', '$WINDOWS', 'System Volume Information',
            'Windows', 'Program Files', 'Program Files (x86)',
            'ProgramData', 'Recovery', 'Boot',
            # Linux/macOS
            'proc', 'sys', 'dev', 'snap',
            # 通用
            '.git', '.svn', 'node_modules', '__pycache__', '$WinREAgent'
        }

        def worker(dir_path: Path, depth: int):
            local_files = 0
            local_cats = {c: 0 for c in ['system', 'cache', 'temp', 'log', 'installer', 'archive',
                                          'large_video', 'large_game', 'large_vm', 'large_model',
                                          'large_image', 'large_other', 'other']}
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

                        if size >= get_large_threshold(cat):
                            local_large[cat].append({
                                "path": str(item),
                                "name": name,
                                "app": guess_app_name(str(item)),
                                "size": size,
                                "size_str": get_size_str(size),
                                "suffix": item.suffix,
                                "mtime": time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
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

        all_cats = {c: 0 for c in ['system', 'cache', 'temp', 'log', 'installer', 'archive',
                                   'large_video', 'large_game', 'large_vm', 'large_model',
                                   'large_image', 'large_other', 'other']}
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
                    if size >= get_large_threshold(cat):
                        local_large[cat].append({
                            "path": str(item),
                            "name": item.name,
                            "app": guess_app_name(str(item)),
                            "size": size,
                            "size_str": get_size_str(size),
                            "suffix": item.suffix,
                            "mtime": time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
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

        for c in all_large:
            all_large[c].sort(key=lambda x: x["size"], reverse=True)

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
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def load_index(index_path: Path) -> dict:
    with open(index_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_history(result: dict, history_path: Path):
    """保存历史索引（用于对比）"""
    history = []
    if history_path.exists():
        try:
            with open(history_path, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[WARN] history.json 格式损坏: {e}", file=sys.stderr)
            backup_path = history_path.with_suffix('.json.bak')
            history_path.rename(backup_path)
            print(f"[INFO] 已备份损坏文件到: {backup_path}", file=sys.stderr)
        except (IOError, OSError) as e:
            print(f"[WARN] 读取 history.json 失败: {e}", file=sys.stderr)
            raise

    # 添加新记录
    history.append({
        "scan_time": result["scan_time"],
        "total_files": result["total_files"],
        "total_size": result["total_size"],
        "total_size_str": result["total_size_str"]
    })

    # 只保留最近 10 次
    history = history[-10:]

    with open(history_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def output_summary(result: dict):
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
        # 小文件显示提示字符串
        if data["count"] > 0 and data["size"] == 0:
            size_display = "文件大小较小"
        else:
            size_display = data["size_str"]

        cat_info = {
            "count": data["count"],
            "size_str": size_display,
            "large_file_count": len(data["large_files"])
        }
        summary["categories"][cat] = cat_info

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def output_detail(result: dict, category: str, top: int = 10, short_path: bool = False):
    if category not in result["categories"]:
        print(json.dumps({"error": f"Category '{category}' not found"}, ensure_ascii=False))
        return

    cat_data = result["categories"][category]
    files = cat_data["large_files"][:top]

    if short_path:
        files = [{k: v for k, v in f.items() if k != 'path'} for f in files]

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


def output_diff(current: dict, old: dict):
    """输出对比结果"""
    # 对比各类别
    diff_categories = {}
    for cat in current["categories"]:
        curr = current["categories"][cat]
        old_cat = old.get("categories", {}).get(cat, {"count": 0, "size": 0})

        count_diff = curr["count"] - old_cat.get("count", 0)
        size_diff = curr["size"] - old_cat.get("size", 0)

        diff_categories[cat] = {
            "current_count": curr["count"],
            "current_size_str": curr["size_str"],
            "old_count": old_cat.get("count", 0),
            "old_size_str": get_size_str(old_cat.get("size", 0)),
            "count_diff": count_diff,
            "size_diff": size_diff,
            "size_diff_str": get_size_str(abs(size_diff)) if size_diff != 0 else "0.00B",
            "trend": "增加" if count_diff > 0 else ("减少" if count_diff < 0 else "不变")
        }

    # 总体对比
    total_diff = current["total_files"] - old.get("total_files", 0)
    size_diff = current["total_size"] - old.get("total_size", 0)

    output = {
        "tool": "disk-clean",
        "status": "success",
        "mode": "diff",
        "drive": current["drive"],
        "current_scan_time": current["scan_time"],
        "old_scan_time": old.get("scan_time", "unknown"),
        "summary": {
            "total_files": current["total_files"],
            "total_size_str": current["total_size_str"],
            "files_diff": total_diff,
            "size_diff": size_diff,
            "size_diff_str": get_size_str(abs(size_diff)) if size_diff != 0 else "0.00B",
            "trend": "增加" if total_diff > 0 else ("减少" if total_diff < 0 else "不变")
        },
        "categories": diff_categories
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
    parser.add_argument('--short-path', action='store_true', help='只输出文件名')
    parser.add_argument('--diff', action='store_true', help='对比模式')
    args = parser.parse_args()

    if args.path:
        p = args.path.rstrip('\\').rstrip('/')
        # Windows 盘符处理
        if platform.system() == 'Windows' and len(p) == 2 and p[1] == ':':
            p += '\\'
        # Linux/macOS 根目录
        elif p == '':
            p = '/'
        drive = p
    else:
        # 默认扫描根目录
        if platform.system() == 'Windows':
            print("Error: --path is required (e.g. -p C:)", file=sys.stderr)
        else:
            drive = '/'
            print(f"[INFO] No path specified, scanning {drive}", file=sys.stderr)

    # 摘要、详情或对比模式
    if args.summary or args.detail or args.diff:
        index_path = get_index_path(drive)
        if not index_path.exists():
            print(f"Error: Index not found at {index_path}", file=sys.stderr)
            print("Please run scan first: python disk_clean.py -p D:", file=sys.stderr)
            sys.exit(1)
        result = load_index(index_path)

        if args.summary:
            output_summary(result)
        elif args.detail:
            output_detail(result, args.detail, args.top, args.short_path)
        elif args.diff:
            # 加载历史索引进行对比
            history_path = get_history_path(drive)
            if not history_path.exists():
                print(json.dumps({"error": "No history found. First scan needed."}, ensure_ascii=False))
                sys.exit(1)
            with open(history_path, 'r', encoding='utf-8') as f:
                history = json.load(f)
            if len(history) < 2:
                print(json.dumps({"error": "Need at least 2 scans for diff."}, ensure_ascii=False))
                sys.exit(1)
            # 使用上一次的历史作为对比基准
            old_record = history[-2]
            # 构造旧的 result 结构
            old_result = {
                "drive": result["drive"],
                "total_files": old_record["total_files"],
                "total_size": old_record["total_size"],
                "total_size_str": old_record["total_size_str"],
                "scan_time": old_record["scan_time"],
                "categories": {}
            }
            output_diff(result, old_result)
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

    # 保存历史
    history_path = get_history_path(drive)
    save_history(result, history_path)

    # 输出摘要
    output_summary(result)


if __name__ == "__main__":
    main()
