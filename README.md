# ai-toolkit

通用 AI 工具包，让 agent 少问多答，省 token。

## 简介

ai-toolkit 是一个轻量级的 AI 工具包，为 agent 提供系统扫描能力。工具输出结构化 JSON，方便 agent 直接解析和建议。

**核心目标：** 工具收集信息 → agent 解析 → 给用户建议（省 token）

## 特性

- **省 Token**：分层输出，只输出 agent 需要的信息
- **跨平台**：支持 Windows/Linux/macOS
- **高性能**：多线程扫描，visited 锁防重复
- **零依赖**：只用 Python 标准库
- **可移植**：任何 agent 都能调用

## 安装

```bash
# 克隆仓库
git clone <repository-url> ~/ai-toolkit

# 或直接复制脚本文件
# scripts/disk_clean.py
# scripts/disk_clean.md
```

## 使用方式

### 扫描磁盘

```bash
# 扫描指定盘符
python scripts/disk_clean.py -p D:

# 扫描子目录
python scripts/disk_clean.py -p D:/Game

# 扫描 WSL
python scripts/disk_clean.py -p "//wsl.localhost/Ubuntu"

# Linux/macOS
python scripts/disk_clean.py -p /
```

### 查看摘要

```bash
python scripts/disk_clean.py --summary -p D:
```

**输出示例：**

```json
{
  "tool": "disk-clean",
  "status": "success",
  "mode": "summary",
  "drive": "D:\\",
  "total_files": 148947,
  "total_size_str": "428.37GB",
  "categories": {
    "cache": {"count": 597, "size_str": "0.00B", "large_file_count": 0, "note": "文件数据较小，未单独统计大小"},
    "large_video": {"count": 23, "size_str": "243.62GB", "large_file_count": 13},
    "large_game": {"count": 507, "size_str": "146.92GB", "large_file_count": 44}
  }
}
```

### 查看详情

```bash
# 查看大视频文件（前 10 个）
python scripts/disk_clean.py --detail large_video --top 10 -p D:

# 查看游戏数据
python scripts/disk_clean.py --detail large_game --top 5 -p D:

# 短路径模式（省 token）
python scripts/disk_clean.py --detail large_video --top 10 --short-path -p D:
```

### 对比模式

```bash
# 需要至少两次扫描
python scripts/disk_clean.py --diff -p D:
```

**输出示例：**

```json
{
  "mode": "diff",
  "summary": {
    "total_files": 150878,
    "total_size_str": "510.02GB",
    "files_diff": 0,
    "size_diff": 0,
    "trend": "不变"
  }
}
```

## 文件分类

| 类别 | 说明 | 可删除性 |
|------|------|----------|
| cache | 缓存文件 | 🟢 可删，应用会自动重建 |
| temp | 临时文件 | 🟢 可删，不影响功能 |
| log | 日志文件 | 🟢 可删，不影响功能 |
| installer | 安装包 | 🟡 需确认后删 |
| archive | 压缩包 | 🟡 需确认后删 |
| large_video | 大视频文件 | 🟢 可删（确认后） |
| large_game | 游戏数据 | 🟡 删除后游戏无法运行 |
| large_vm | 虚拟机文件 | 🟡 删除后虚拟机无法使用 |
| large_model | 模型文件 | 🟡 需确认后删 |
| large_image | 镜像文件 | 🟡 需确认后删 |
| large_other | 其他大文件 | 🟡 需用户判断 |
| system | 系统文件 | 🔴 不可删 |
| other | 其他文件 | 🟡 需确认后删 |

## Token 消耗对比

| 模式 | Token |
|------|-------|
| 完整输出 | ~60,000 |
| summary | ~500 |
| detail | ~400 |
| **总计** | **~1,300** |
| **节省** | **98%** |

## 跨平台支持

| 平台 | 状态 | 说明 |
|------|------|------|
| Windows | ✅ | 完全支持，如 `C:\`, `D:\` |
| Linux | ✅ | 支持，如 `/`, `/home` |
| macOS | ✅ | 支持，如 `/`, `/Volumes/xxx` |
| WSL | ✅ | 支持，如 `//wsl.localhost/Ubuntu` |

## Agent 使用流程

```
用户："D盘满了"
  ↓
Agent 读取说明书：scripts/disk_clean.md
  ↓
Agent 调用：python scripts/disk_clean.py --summary -p D:
  ↓
Agent 看到：large_video 23个文件，243.62GB
  ↓
Agent 调用：python scripts/disk_clean.py --detail large_video --top 10 --short-path -p D:
  ↓
Agent 给用户建议："前10个大视频占200GB，主要是游戏录像..."
```

## 索引缓存

- 索引文件保存位置：`~/.ai-toolkit/indexes/`
- 历史文件保存位置：`~/.ai-toolkit/history/`
- 索引有效期：当天有效，过期需要重新扫描

## 开发

```bash
# 零依赖，无需安装
python scripts/disk_clean.py -p D:
```

## 许可证

MIT
