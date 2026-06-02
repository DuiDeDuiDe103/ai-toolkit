#!/usr/bin/env python3
"""
disk_clean.py - 磁盘扫描工具（只读）

功能：扫描整个盘符，输出完整结果给 agent 判断
权限：只读，绝不修改文件
输出：JSON（agent 解析后给用户建议）
特性：多线程扫描，支持指定盘符

使用方式：
    python disk_clean.py --path C:/
    python disk_clean.py --path D:/
    python disk_clean.py              # 扫描所有盘
    python disk_clean.py --threads 16  # 使用16线程
"""

import os
import sys
import json
import argparse
import platform
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

# 修复 Windows 终端编码问题
if platform.system() == 'Windows':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

lock = threading.Lock()


def get_size_str(size_bytes: int) -> str:
    """将字节数转换为可读字符串"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f}PB"


def get_drives() -> list:
    """获取所有可用磁盘盘符"""
    if platform.system() != 'Windows':
        return ['/']
    drives = []
    for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


def guess_app_name(path: str) -> str:
    """从路径推断应用名"""
    path_lower = path.lower()
    app_map = {
        'chrome': 'Google Chrome', 'edge': 'Microsoft Edge',
        'firefox': 'Mozilla Firefox', 'opera': 'Opera',
        'vscode': 'Visual Studio Code', 'visual studio': 'Visual Studio',
        'steam': 'Steam', 'epic': 'Epic Games', 'wegame': 'WeGame',
        'discord': 'Discord', 'slack': 'Slack', 'teams': 'Microsoft Teams',
        'zoom': 'Zoom', 'obs': 'OBS Studio', 'nvidia': 'NVIDIA',
        'python': 'Python', 'node': 'Node.js', 'docker': 'Docker',
        'git': 'Git', 'blender': 'Blender', 'unreal': 'Unreal Engine',
        'bitbrowser': 'BitBrowser', 'adobe': 'Adobe', 'spotify': 'Spotify',
        'minecraft': 'Minecraft', 'league': 'League of Legends',
    }
    for keyword, app_name in app_map.items():
        if keyword in path_lower:
            return app_name
    parts = path.replace('/', '\\').split('\\')
    for part in parts:
        if part and part not in ['Users', 'AppData', 'Local', 'Roaming', 'Temp', 'Cache',
                                  'Program Files', 'Program Files (x86)', 'ProgramData',
                                  'common', 'steamapps', 'workshop', 'content']:
            if len(part) > 2 and not part.startswith('.') and not part.startswith('$'):
                return part
    return 'Unknown'


def classify_file(file_path: str, file_size: int, drive_letter: str) -> dict:
    """
    分类文件，适配所有盘符

    参数：
        file_path: 文件完整路径
        file_size: 文件大小
        drive_letter: 盘符（如 C、D、E）
    """
    path_lower = file_path.lower().replace('/', '\\')
    suffix = Path(file_path).suffix.lower()
    name = Path(file_path).name.lower()
    parent_name = Path(file_path).parent.name.lower()

    # === 系统文件判断（适配所有盘符）===
    system_keywords = [
        f'{drive_letter.lower()}:\\windows',
        f'{drive_letter.lower()}:\\program files',
        f'{drive_letter.lower()}:\\program files (x86)',
        f'{drive_letter.lower()}:\\programdata',
        f'{drive_letter.lower()}:\\recovery',
        f'{drive_letter.lower()}:\\$windows',
        f'{drive_letter.lower()}:\\system volume information',
        f'{drive_letter.lower()}:\\boot',
        '/windows', '/program files', '/usr', '/bin', '/sbin', '/lib', '/etc', '/var',
    ]
    if any(path_lower.startswith(kw) for kw in system_keywords):
        return {
            "category": "system",
            "optimizable": False,
            "risk": "critical",
            "impact": "系统文件，删除可能导致系统崩溃",
            "app": "System"
        }

    # === 安装包 ===
    installer_exts = {'.exe', '.msi', '.msix', '.appx', '.dmg', '.pkg', '.deb', '.rpm', '.iso'}
    if suffix in installer_exts:
        return {
            "category": "installer",
            "optimizable": True,
            "risk": "low",
            "impact": "安装包，删除后无法重新安装，建议确认已安装或不再需要",
            "app": guess_app_name(file_path)
        }

    # === 压缩包 ===
    archive_exts = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz'}
    if suffix in archive_exts:
        return {
            "category": "archive",
            "optimizable": True,
            "risk": "low",
            "impact": "压缩包，删除后无法解压，建议确认已解压或不再需要",
            "app": guess_app_name(file_path)
        }

    # === 日志文件 ===
    if suffix in ['.log', '.log.1', '.log.2', '.log.3'] or name.endswith('.log'):
        return {
            "category": "log",
            "optimizable": True,
            "risk": "safe",
            "impact": "日志文件，删除后不影响功能",
            "app": guess_app_name(file_path)
        }

    # === 临时文件 ===
    if suffix in ['.tmp', '.temp', '.bak', '.old', '.swp'] or name.startswith('~'):
        return {
            "category": "temp",
            "optimizable": True,
            "risk": "safe",
            "impact": "临时文件，通常可安全删除",
            "app": guess_app_name(file_path)
        }

    # === 缓存文件 ===
    cache_keywords = ['cache', 'cached']
    if any(kw in parent_name for kw in cache_keywords):
        return {
            "category": "cache",
            "optimizable": True,
            "risk": "safe",
            "impact": "缓存文件，删除后应用会自动重建",
            "app": guess_app_name(file_path)
        }

    # === 大文件（用户数据）===
    if file_size >= 100 * 1024 * 1024:
        return {
            "category": "large",
            "optimizable": False,
            "risk": "medium",
            "impact": "大文件，需要用户确认是否为重要数据",
            "app": guess_app_name(file_path)
        }

    # === 其他用户文件 ===
    return {
        "category": "other",
        "optimizable": False,
        "risk": "low",
        "impact": "用户文件",
        "app": guess_app_name(file_path)
    }


def scan_drive(drive_path: Path, large_threshold: int, min_folder_size: int,
               max_workers: int) -> dict:
    """扫描指定盘符"""
    drive_letter = drive_path.drive[0] if drive_path.drive else ''

    result = {
        "drive": str(drive_path),
        "drive_letter": drive_letter,
        "total_files": 0,
        "skipped_folders": 0,
        "categories": {
            "system":   {"count": 0, "total_size": 0, "large_files": []},
            "installer": {"count": 0, "total_size": 0, "large_files": []},
            "archive":  {"count": 0, "total_size": 0, "large_files": []},
            "log":      {"count": 0, "total_size": 0, "large_files": []},
            "temp":     {"count": 0, "total_size": 0, "large_files": []},
            "cache":    {"count": 0, "total_size": 0, "large_files": []},
            "large":    {"count": 0, "total_size": 0, "large_files": []},
            "other":    {"count": 0, "total_size": 0, "large_files": []},
        }
    }

    skip_dirs = {
        '$Recycle.Bin', '$WINDOWS', 'System Volume Information',
        'Windows', 'Program Files', 'Program Files (x86)',
        'ProgramData', 'Recovery', 'Boot', 'Documents and Settings',
        '.git', '.svn', 'node_modules', '__pycache__'
    }

    def process_file(file_path: Path, file_size: int):
        classification = classify_file(str(file_path), file_size, drive_letter)
        cat = classification["category"]

        # 确保类别存在
        if cat not in result["categories"]:
            with lock:
                result["categories"][cat] = {"count": 0, "total_size": 0, "large_files": []}

        with lock:
            result["categories"][cat]["count"] += 1
            result["categories"][cat]["total_size"] += file_size

            if file_size >= large_threshold:
                result["categories"][cat]["large_files"].append({
                    "path": str(file_path),
                    "name": file_path.name,
                    "app": classification["app"],
                    "size": file_size,
                    "size_str": get_size_str(file_size),
                    "suffix": file_path.suffix,
                    "optimizable": classification["optimizable"],
                    "risk": classification["risk"],
                    "impact": classification["impact"]
                })

    def scan_dir(dir_path: Path, depth: int = 0):
        if depth > 6:
            return
        try:
            for item in dir_path.iterdir():
                try:
                    if item.is_dir():
                        if item.name in skip_dirs or item.name.startswith('$'):
                            continue
                        # 跳过小于阈值的文件夹
                        try:
                            folder_size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                            if folder_size < min_folder_size:
                                with lock:
                                    result["skipped_folders"] += 1
                                continue
                        except:
                            pass
                        scan_dir(item, depth + 1)
                    elif item.is_file():
                        try:
                            file_size = item.stat().st_size
                            process_file(item, file_size)
                            with lock:
                                result["total_files"] += 1
                        except (OSError, PermissionError):
                            pass
                except (OSError, PermissionError):
                    pass
        except (OSError, PermissionError):
            pass

    scan_dir(drive_path)

    # 按大小排序大文件
    for cat in result["categories"]:
        result["categories"][cat]["large_files"].sort(key=lambda x: x["size"], reverse=True)

    # 统计
    total_size = sum(c["total_size"] for c in result["categories"].values())
    result["total_size"] = total_size
    result["total_size_str"] = get_size_str(total_size)

    return result


def main():
    parser = argparse.ArgumentParser(description='磁盘扫描工具（只读）')
    parser.add_argument('--path', '-p', type=str, default=None,
                        help='扫描路径，如 C: 或 D:，不指定则扫描所有盘')
    parser.add_argument('--min-size', '-m', type=int, default=50,
                        help='文件夹最小大小阈值（MB），默认 50MB')
    parser.add_argument('--large-size', '-l', type=int, default=100,
                        help='大文件阈值（MB），默认 100MB')
    parser.add_argument('--threads', '-t', type=int, default=8,
                        help='多线程数量，默认 8')

    args = parser.parse_args()

    # 确定扫描路径
    if args.path:
        path = args.path.rstrip('\\').rstrip('/')
        if not path.endswith(':'):
            path += ':'
        if not path.endswith(':\\') and len(path) == 2:
            path += '\\'
        drives = [path]
    else:
        drives = get_drives()

    min_size_bytes = args.min_size * 1024 * 1024
    large_size_bytes = args.large_size * 1024 * 1024
    all_results = []

    print(f"[INFO] Scanning {len(drives)} drive(s), {args.threads} threads", file=sys.stderr)

    start_time = time.time()

    for drive in drives:
        drive_path = Path(drive)
        if not drive_path.exists():
            print(f"[WARN] Not found: {drive}", file=sys.stderr)
            continue

        print(f"[SCAN] {drive}...", file=sys.stderr)
        t0 = time.time()
        result = scan_drive(drive_path, large_size_bytes, min_size_bytes, args.threads)
        elapsed = time.time() - t0
        print(f"[DONE] {drive}: {result['total_files']:,} files, {result['total_size_str']}, {elapsed:.1f}s", file=sys.stderr)
        all_results.append(result)

    total_elapsed = time.time() - start_time

    # 汇总
    summary = {
        "total_drives": len(all_results),
        "total_files": sum(r["total_files"] for r in all_results),
        "total_size": sum(r["total_size"] for r in all_results),
        "total_size_str": get_size_str(sum(r["total_size"] for r in all_results)),
        "scan_time": f"{total_elapsed:.1f}s"
    }

    # 汇总各类别
    all_cats = {}
    for r in all_results:
        for cat, data in r["categories"].items():
            if cat not in all_cats:
                all_cats[cat] = {"count": 0, "total_size": 0, "large_files": []}
            all_cats[cat]["count"] += data["count"]
            all_cats[cat]["total_size"] += data["total_size"]
            all_cats[cat]["large_files"].extend(data["large_files"])

    # 可优化总量
    optimizable_size = 0
    for cat in ['cache', 'temp', 'log', 'installer', 'archive']:
        if cat in all_cats:
            optimizable_size += all_cats[cat]["total_size"]

    summary["optimizable_size"] = optimizable_size
    summary["optimizable_size_str"] = get_size_str(optimizable_size)

    # 分类统计
    summary["categories"] = {}
    for cat, data in sorted(all_cats.items(), key=lambda x: x[1]["total_size"], reverse=True):
        summary["categories"][cat] = {
            "count": data["count"],
            "total_size_str": get_size_str(data["total_size"]),
            "large_file_count": len(data["large_files"])
        }

    output = {
        "tool": "disk-clean",
        "status": "success",
        "drives": all_results,
        "summary": summary
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
