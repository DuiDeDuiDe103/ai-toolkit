# ai-toolkit

通用 AI 工具包，让 agent 少问多答，省 token。

## 简介

ai-toolkit 是一个轻量级的 AI 工具包，提供系统扫描、文本处理等功能。工具输出结构化 JSON，方便 agent 直接解析和建议。

## 特性

- **省 Token**：工具收集信息，AI 只负责建议
- **跨平台**：支持 Windows/Linux/macOS
- **轻量级**：尽量零依赖，单脚本 < 150 行
- **通用性**：任何 agent 都能调用

## 安装

```bash
# 克隆仓库
git clone <repository-url> ~/.claude/plugins/ai-toolkit

# 或直接复制到任意目录
```

## 使用方式

### 方式一：直接调用脚本

```bash
python scripts/system_info.py
python scripts/disk_clean.py --path C:\
python scripts/de_ai.py --text "AI 生成的文本"
```

### 方式二：作为 Python 模块

```python
from scripts.system_info import get_system_info
result = get_system_info()
```

### 方式三：MCP Server（后续支持）

```json
{
  "mcpServers": {
    "ai-toolkit": {
      "command": "node",
      "args": ["path/to/ai-toolkit/mcp-server/index.js"]
    }
  }
}
```

## 工具列表

| 工具 | 用途 | 状态 |
|------|------|------|
| system_info | 系统信息 | ✅ 可用 |
| disk_clean | 磁盘清理 | ✅ 可用 |
| de_ai | 去 AI 味 | ✅ 可用 |
| port_check | 端口占用 | 🔜 计划中 |
| wifi_check | 网络诊断 | 🔜 计划中 |
| text_polish | 文章润色 | 🔜 计划中 |

## 输出格式

所有工具输出 JSON 格式：

```json
{
  "tool": "system_info",
  "status": "success",
  "data": [...],
  "summary": "一句话总结"
}
```

## 开发

```bash
# 安装依赖（尽量少）
pip install -r requirements.txt

# 运行测试
python -m pytest tests/
```

## 许可证

MIT
