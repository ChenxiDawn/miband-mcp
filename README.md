# miband-mcp

> 晨熹 · 让 AI 读懂你的手腕

零第三方依赖的 Python MCP Server：读取 **Gadgetbridge** 导出的 SQLite 数据库（小米手环健康数据），通过 MCP 协议暴露给任意支持 MCP 的 AI 客户端（Reasonix / Claude / Cursor / Kimi / GLM 等），让 AI 能回答"今天走了几步""昨晚睡得怎么样"。

## 架构

```
小米手环 9 / 9 Pro / 10 → Gadgetbridge（手机端采集）→ SQLite 导出 → miband-mcp（读库+聚合）→ MCP 协议 → AI
```

## 提供的工具

| 工具 | 参数 | 返回 |
|---|---|---|
| `get_hr` | `count`（默认 10，最大 100） | 最近 N 条心率（时间 + bpm） |
| `get_sleep` | `date`（默认今天） | 入睡/醒来时间、睡眠分期（light/deep/rem）、总时长 |
| `get_daily` | `date`（默认今天） | 步数、平均/最高/最低心率 |

## 快速开始

### 1. 准备数据源

- 手机安装 [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge/releases)（官方仓库在 Codeberg，不是 GitHub）
- 按官方 [配对指南](https://gadgetbridge.org/basics/pairing/) 连接手环（小米手环 9 需**手环主动发起连接**：手环设置 → 系统操作 → 连接新手机）
- 从**小米运动健康**日志中提取 32 位 `encryptKey` 作为配对密钥（注意：**绑定后不可解绑**，否则密钥失效）
- Gadgetbridge 设置 → 自动化 → 开启 **自动导出数据库**

### 2. 运行 MCP Server

```bash
# 零依赖，纯标准库
python miband_mcp.py --db /path/to/gadgetbridge.db
```

### 3. 接入 AI（以 Reasonix 为例）

```toml
[[plugins]]
name    = "miband"
type    = "stdio"
command = "python"
args    = ["<路径>/miband_mcp.py", "--db", "<路径>/gadgetbridge.db"]
auto_start = true
```

然后 `/new` 新会话即可生效。

## ⚠️ 当前限制

**数据同步为手动模式**：本项目读取的是 Gadgetbridge 导出的 SQLite 数据库快照，**不会自动从手环拉取数据**。

当前工作流：
1. 手环数据由 Gadgetbridge 采集（需先同步）
2. 手动将导出的数据库文件（`.db`）发送到运行 miband-mcp 的电脑
3. AI 通过 MCP 工具读取数据库中的最新数据

**因此**：AI 回答的是"最近一次同步的数据"，不是实时数据。若需要实时读取，可结合 Termux 脚本 + 定时同步，将 `.db` 文件自动推送到电脑（本项目暂未实现，欢迎 PR）。

## 开发

```bash
python -m unittest discover -s tests -v   # 34 个测试，零依赖
```

## 许可

MIT © ChenxiDawn · 晨熹
