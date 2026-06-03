# disk_clean - 磁盘扫描工具

## 触发条件

当用户提到以下关键词时使用：
- 磁盘、空间、清理、C盘、D盘、E盘
- 释放空间、删除文件、垃圾清理
- 大文件、缓存、临时文件
- 存储、占用、满

## 使用方式

### 1. 扫描磁盘（首次使用）

```bash
python scripts/disk_clean.py -p {盘符}
```

示例：
```bash
python scripts/disk_clean.py -p D:
python scripts/disk_clean.py -p C:
python scripts/disk_clean.py -p D:/Game  # 扫描子目录
```

**输出：** 摘要信息 + 保存索引文件

### 2. 查看摘要

```bash
python scripts/disk_clean.py --summary -p {盘符}
```

示例：
```bash
python scripts/disk_clean.py --summary -p D:
```

**输出：** 各类别的文件数量和大小统计

### 3. 查看详情

```bash
python scripts/disk_clean.py --detail {类别} --top {数量} -p {盘符}
```

示例：
```bash
python scripts/disk_clean.py --detail large --top 10 -p D:
python scripts/disk_clean.py --detail installer --top 5 -p D:
python scripts/disk_clean.py --detail cache --top 20 -p D:
```

### 4. 短路径模式（省 token）

```bash
python scripts/disk_clean.py --detail {类别} --top {数量} --short-path -p {盘符}
```

示例：
```bash
python scripts/disk_clean.py --detail large --top 10 --short-path -p D:
```

## 类别说明

| 类别 | 说明 | 可优化性 | 建议 |
|------|------|----------|------|
| cache | 缓存文件 | 🟢 可删 | 应用会自动重建 |
| temp | 临时文件 | 🟢 可删 | 不影响功能 |
| log | 日志文件 | 🟢 可删 | 不影响功能 |
| installer | 安装包 | 🟡 需确认 | 确认已安装后可删 |
| archive | 压缩包 | 🟡 需确认 | 确认已解压后可删 |
| large | 大文件（>100MB） | 🟡 需确认 | 确认是否为重要数据 |
| system | 系统文件 | 🔴 不可删 | 删除可能导致系统崩溃 |
| other | 其他文件 | 🟡 需确认 | 用户文件 |

## 输出格式

### 摘要模式（summary）

```json
{
  "tool": "disk-clean",
  "status": "success",
  "mode": "summary",
  "drive": "D:\\",
  "total_files": 150874,
  "total_size_str": "509.51GB",
  "categories": {
    "cache": {"count": 597, "size_str": "0.00B", "large_file_count": 0},
    "temp": {"count": 622, "size_str": "0.00B", "large_file_count": 0},
    "large": {"count": 617, "size_str": "499.62GB", "large_file_count": 617}
  }
}
```

### 详情模式（detail）

```json
{
  "tool": "disk-clean",
  "status": "success",
  "mode": "detail",
  "drive": "D:\\",
  "category": "large",
  "total_count": 617,
  "total_size_str": "499.62GB",
  "showing": 10,
  "files": [
    {
      "name": "The Binding of Isaac Rebirth 2025.11.29 - 19.35.38.01.mp4",
      "app": "The Binding of Isaac Rebirth",
      "size": 81579148282,
      "size_str": "75.98GB",
      "suffix": ".mp4",
      "mtime": "2025-11-29 21:32"
    }
  ]
}
```

## 完整工作流程

```
用户："D盘能清理多少？"
  ↓
Agent 调用：python scripts/disk_clean.py --summary -p D:
  ↓
Agent 看到：large 617个文件，499GB
  ↓
Agent 调用：python scripts/disk_clean.py --detail large --top 10 --short-path -p D:
  ↓
Agent 给用户建议："前10个大文件占300GB，主要是游戏录像..."
```

## 注意事项

- **只读操作**：脚本不会删除任何文件
- **首次扫描**：需要 20-30 秒
- **索引缓存**：扫描结果保存在 `{盘符}:/.ai-toolkit/index.json`
- **索引有效期**：当天有效，过期需要重新扫描
- **Token 消耗**：summary ~500 tokens，detail ~400 tokens

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-p, --path` | 扫描路径（必填） | - |
| `-l, --large-size` | 大文件阈值（MB） | 100 |
| `-t, --threads` | 线程数 | 8 |
| `--summary` | 输出摘要 | - |
| `--detail` | 输出详情，指定类别 | - |
| `--top` | 详情数量 | 10 |
| `--short-path` | 只输出文件名 | false |
