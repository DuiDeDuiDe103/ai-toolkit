# ai-toolkit 技术方案

## 一、项目背景

### 1.1 问题
- AI agent 处理用户问题时，需要多轮对话收集信息，消耗大量 token
- 系统扫描、文本处理等任务，AI 需要反复提问、分析，效率低
- 现有工具要么绑定特定平台，要么不够轻量

### 1.2 目标
做一个通用的 AI 工具包，让 agent 少问多答，省 token。

### 1.3 核心逻辑
```
传统方式：用户提问 → AI 反复询问 → AI 分析 → AI 建议（多轮对话，费 token）
工具方式：用户提问 → 工具收集信息 → 返回结构化数据 → AI 直接建议（一次调用，省 token）
```

---

## 二、方案选型

### 2.1 脚本语言选择

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **PowerShell** | Windows 自带、系统操作原生 | 跨平台差、文本处理弱 | ❌ 放弃 |
| **Python** | 跨平台、生态丰富、文本处理强 | 需要 Python 环境 | ✅ 采用 |
| **Node.js** | 跨平台、MCP 生态支持 | 需要 Node 环境、AI 领域不通用 | ❌ 放弃 |

### 2.2 输出格式选择

| 方案 | 格式 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| **方案 A** | 纯文本 `KEY|VALUE|DETAILS` | 最省 token、通用 | 需要 agent 解析 | ❌ 放弃 |
| **方案 B** | JSON 结构化 | 易解析、结构清晰 | 比纯文本多占 token | ✅ 采用 |
| **方案 C** | 两种都支持 | 灵活 | 增加复杂度 | ❌ 放弃 |

### 2.3 架构选择

| 方案 | 架构 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| **A** | Claude Code Plugin | 集成度高 | 仅 Claude Code 可用 | ❌ 放弃 |
| **B** | MCP Server | 标准化、多 agent 支持 | 需要 Node 环境 | 🔜 后续扩展 |
| **C** | 独立脚本 | 最通用、零依赖 | 需要手动调用 | ✅ 采用 |
| **B+C** | 脚本 + MCP 包装 | 兼顾通用和集成 | 增加复杂度 | ✅ 采用 |

### 2.4 多线程策略

| 策略 | 说明 | 结论 |
|------|------|------|
| **全局锁** | 一把大锁保护所有共享资源 | ❌ 性能差 |
| **visited 锁** | 用 set 记录已访问文件夹，锁保护 set | ✅ 采用 |
| **线程本地计数** | 每个线程维护本地计数，最后合并 | ✅ 采用 |

---

## 三、技术方案

### 3.1 目录结构

```
ai-toolkit/
├── scripts/                    # 核心脚本
│   ├── __init__.py
│   └── disk_clean.py           # 磁盘扫描
│
├── mcp-server/                 # 可选：MCP Server 包装
│
├── requirements.txt            # 依赖（尽量少）
├── README.md                   # 使用说明
├── CHANGELOG.md                # 版本记录
└── TECH_PLAN.md                # 本文档
```

### 3.2 脚本设计规范

| 规范 | 要求 |
|------|------|
| 语言 | Python 3.8+ |
| 依赖 | 尽量零依赖，必要时用标准库 |
| 输出 | JSON 格式，结构化 |
| 行为 | 只读，不修改文件 |
| 跨平台 | Windows/Linux/macOS |

### 3.3 输出格式标准

#### 摘要模式（--summary）

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

**Token 消耗：~500**

#### 详情模式（--detail large --top 10）

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

**Token 消耗：~400（使用 --short-path）**

### 3.4 文件分类

| 类别 | 说明 | 可优化性 |
|------|------|----------|
| system | 系统文件 | 🔴 不可删 |
| cache | 缓存文件 | 🟢 可删，应用会自动重建 |
| temp | 临时文件 | 🟢 可删，不影响功能 |
| log | 日志文件 | 🟢 可删，不影响功能 |
| installer | 安装包 | 🟡 需确认后删 |
| archive | 压缩包 | 🟡 需确认后删 |
| large | 大文件（>100MB） | 🟡 需确认后删 |
| other | 其他用户文件 | 🟡 需确认后删 |

### 3.5 多线程扫描策略

```
visited = set()  # 记录已访问的文件夹
visited_lock = threading.Lock()

def scan_dir(path):
    # 检查是否已访问
    with visited_lock:
        if path in visited:
            return  # 已访问，跳过
        visited.add(path)

    # 处理文件夹（不加锁，线程独立）
    local_count = 0
    for item in path.iterdir():
        if item.is_file():
            local_count += 1

    # 返回本地结果，最后合并
    return local_count
```

**关键点：**
1. `visited` 用一把锁保护（竞争不大，简单可靠）
2. 计数器用线程本地变量，不加锁
3. 大文件列表线程本地收集，最后合并

---

## 四、分层输出设计

### 4.1 设计思路

**核心问题：**
- 完整输出 ~60,000 tokens，太浪费
- agent 需要的是摘要，不是完整列表

**解决方案：分层输出**
- 第一次调用：摘要（~500 tokens）
- 第二次调用：按需详情（~400 tokens/次）
- 总计 ~1,300 tokens，节省 98%

### 4.2 使用方式

```bash
# 第一次：扫描并保存索引（25秒）
python scripts/disk_clean.py -p D:

# 查看摘要（瞬间）
python scripts/disk_clean.py --summary -p D:

# 查看详情（瞬间）
python scripts/disk_clean.py --detail large --top 10 -p D:
python scripts/disk_clean.py --detail installer --top 5 -p D:

# 短路径模式（省 token）
python scripts/disk_clean.py --detail large --top 10 --short-path -p D:
```

### 4.3 索引文件

- 位置：`{盘符}:/.ai-toolkit/index.json`
- 有效期：当天有效，过期重新扫描
- 用途：第二次调用读取，不重新扫描

---

## 五、Token 优化

### 5.1 优化措施

| 优化 | 效果 |
|------|------|
| 分层输出 | 60,000 → 1,300 tokens（节省 98%） |
| 去掉 mtime 字段 | 单文件节省 ~20% |
| --short-path 选项 | 单文件节省 ~50% |
| **总计** | **180 → 90 tokens/文件** |

### 5.2 Token 消耗对比

| 模式 | Token |
|------|-------|
| 完整输出（之前） | ~60,000 |
| summary（现在） | ~500 |
| detail --short-path（现在） | ~400 |
| **总计** | **~1,300** |
| **节省** | **98%** |

---

## 六、开发计划

### 6.1 已完成

| 功能 | 状态 | 说明 |
|------|------|------|
| 项目骨架 | ✅ | 目录结构、README、CHANGELOG |
| 磁盘扫描 | ✅ | 多线程扫描，visited 防重复 |
| 文件分类 | ✅ | system/cache/temp/log/installer/archive/large/other |
| 分层输出 | ✅ | summary + detail |
| 索引保存 | ✅ | 扫描结果保存到本地 |
| Token 优化 | ✅ | 去掉 mtime，--short-path |

### 6.2 待实现

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 评分系统 | P1 | 时间+类型+大小加权 |
| 分组排序 | P1 | 按类别分组，组内按分数排序 |
| 访问时间 | P2 | 用 atime 判断"用户以为删除了" |
| 遇到特定文件夹直接标记 | P2 | 不递归，直接加入类别 |
| MCP Server 包装 | P3 | 可选扩展 |

---

## 七、风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 用户没 Python 环境 | 无法使用 | README 说明安装方式 |
| 扫描大磁盘耗时长 | 用户等待 | 多线程优化，索引缓存 |
| 索引过期 | 数据不准确 | 提示用户重新扫描 |

---

## 八、总结

### 8.1 技术选型

| 维度 | 选择 |
|------|------|
| 语言 | Python 3.8+ |
| 输出 | JSON 结构化 |
| 架构 | 独立脚本 + 可选 MCP 包装 |
| 平台 | 跨平台（Windows/Linux/macOS） |
| 依赖 | 零依赖 |

### 8.2 核心价值

1. **省 Token**：分层输出，节省 98%
2. **通用性**：任何 agent 都能调用
3. **可扩展**：按需添加新工具
4. **轻量级**：零依赖、跨平台

### 8.3 使用示例

```bash
# 用户：D盘能清理多少？
# Agent 调用：python scripts/disk_clean.py --summary -p D:
# Agent 看到：large 617个文件，499GB
# Agent 调用：python scripts/disk_clean.py --detail large --top 10 --short-path -p D:
# Agent 给用户建议："前10个大文件占300GB，主要是游戏录像..."
```

### 8.4 Token 消耗

- 完整输出：~60,000 tokens
- 分层输出：~1,300 tokens
- **节省 98%**
