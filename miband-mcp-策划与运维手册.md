# miband-mcp 策划与运维手册

> 版本：v0.2（真机验证版） · 日期：2026-08-21
> 搭建：Qwen（千问办公助理） · 精调/验证：晨曦 · 评审：用户 × 晨曦
> 定位：本文档是本项目的"总账本"——后期维护、查看、辨别、判断都以它为准。
> 依据文档：《miband-mcp-规格书-Qwen版.md》《回复Qwen-4问评审.md》

---

## 1. 项目一句话与当前状态

**一句话**：用零第三方依赖的 Python MCP Server，读取 Gadgetbridge 导出的
SQLite 里的小米手环 9 健康数据（心率/睡眠/步数），通过 MCP 协议暴露给
Reasonix AI，让 AI 能回答"用户今天睡得好吗"这类问题。

**当前状态（2026-08-21）**：

| 项 | 状态 |
|---|---|
| 代码骨架（4 个模块 + 主入口） | ✅ 完成 |
| 测试（34 个用例，全部通过） | ✅ 完成 |
| 规格书 §7 验收清单（7 项） | ✅ 全部实测通过（见 §7） |
| **真机数据接入** | ✅ **已完成（2026-08-21）**——用户手环 9 真实数据经 Gadgetbridge 导出，get_daily 实测返回 239 步/平均心率 77 |
| 真机列名适配 | ✅ 已完成——db.py/aggregator.py 字段对齐大写列名（TIMESTAMP/HEART_RATE/STEPS/RAW_KIND/WAKEUP_TIME/STAGE） |
| Reasonix 侧 MCP 注册 | ✅ 已完成——reasonix.toml 注册 miband 插件（stdio + auto_start），待 /new 生效 |
| GitHub 开源 | ✅ 已上线——github.com/ChenxiDawn/miband-mcp（公开仓库，健康数据已排除） |

**给晨曦的提示**：数据链路已跑通，`get_hr` 暂为空是因为手环 9 未连续测心率（HEART_RATE=0）；等手环产生有效心率采样后自动填充。下一目标：Gadgetbridge 自动导出 → 电脑自动同步的自动化链路。

---

## 2. 技术路线与架构

### 2.1 全链路数据流

```
小米手环 9 ──蓝牙──> 安卓手机 Gadgetbridge ──导出──> SQLite 文件
                                                          │
                                     复制/挂载到电脑（路径配置见 §3.3）
                                                          │
miband_mcp.py（stdio JSON-RPC）<── db.py（查询）<── aggregator.py（聚合）
                                                          │
                                              MCP 协议（initialize/tools/list/tools/call）
                                                          │
                                                     Reasonix AI
```

### 2.2 路线选择回顾（为什么是这条路）

三份参考方案对比后，规格书选定的是**方案 3（Gadgetbridge 链路）的
"文件读取变体"**：

- 不采用云端桥接（Mi Fitness Data Bridge 类）：需要小米账号 passToken，
  等同登录态，且依赖小米私有接口，随时可能失效；
- 不采用 Termux + Flask HTTP 桥（小红书教程原版）：多一层常驻 HTTP 服务，
  还涉及 FRP 内网穿透，运维面太大；
- 采用"Gadgetbridge 导出 SQLite → MCP Server 直接读文件"：链路最短、
  零网络面、零凭据，且与 Reasonix 现有 xit MCP 的手写 stdio 风格完全一致。
- **代价（已知且接受）**：数据新鲜度取决于 Gadgetbridge 的同步与导出
  频率，不是实时数据；`get_hr` 返回的是"最近 N 条已同步样本"，返回结构
  里的 `stale` 字段与 `source: "gadgetbridge"` 标注就是为此设计的。

### 2.3 模块分层（严格对应规格书 §4 原则）

```
miband_mcp.py   协议层：手写 JSON-RPC 2.0 循环 + 工具注册/分发 + 错误兜底
config.py       配置层：DB 路径解析（--db > MIBAND_DB 环境变量 > 默认路径）
db.py           数据层：只查原始 dict/list，不做聚合；缺库/缺表抛明确异常
aggregator.py   聚合层：原始样本 → 规格书 §3 定义的摘要 JSON
```

**边界纪律**：db.py 里出现任何"算平均/求和/格式化时间"都属于越界；
aggregator.py 里出现任何 SQL 都属于越界。精调时请守住这条线，
否则测试与维护都会乱。

---

## 3. 关键设计决策（含 4 问评审落点）

| # | 决策 | 依据 | 代码位置 |
|---|---|---|---|
| ① | 手写 JSON-RPC 2.0 循环，不用 FastMCP | 评审①：零依赖是硬约束；与 xit/xiv 实践一致 | `miband_mcp.py` 全文 |
| ② | 时间戳默认 Unix 秒级 + 常量开关 | 评审②：Gadgetbridge 惯例；真机若是毫秒，改 `db.py` 的 `TS_UNIT="ms"` 即可 | `db.TS_UNIT` |
| ③ | 测试 fixture 动态生成、跑完自清 | 评审③：测试数据不污染仓库 | `tests/fixture_builder.py` + `tests/conftest.py` |
| ④ | 睡眠跨天：HH:MM 本地时区 + `total_min` + `fell_asleep_date` | 评审④补丁：`total_min` 必须实现；`fell_asleep_date` 防 AI 把 23:40 误会成查询当天 | `aggregator.aggregate_sleep` |

其他重要决策（规格书要求或骨架自定，供精调时辨别）：

1. **无效心率哨兵 255**：Gadgetbridge 用 255 表示"未测到心率"，
   `aggregator.valid_bpm()` 统一过滤（同时排除 0、负数、≥250）。
2. **睡眠分期编码**：`0=awake 1=light 2=deep 3=rem`（规格书 §4），
   awake 不计入 stages 分钟数。映射在 `db.SLEEP_STAGE_MAP`。
3. **跨天睡眠归属规则**：优先按"醒来日期 == 查询日"匹配睡眠段，
   其次按"入睡日期"兜底（`aggregator.pick_sleep_interval`）。
4. **错误不炸协议**：工具内部任何异常都转成 `isError=true` 的正常
   MCP 结果（如 `{"error": "database not found: <path>"}`），进程不退出；
   只有协议级错误才走 JSON-RPC error 对象。
5. **空数据带 stale 标记**：三个工具在无数据时返回空列表/零值 +
   `"stale": true`（规格书 §5.2），AI 可据此判断数据新鲜度。
6. **只读模式打开数据库**：`sqlite3` 用 `file:...?mode=ro` URI，
   物理上杜绝误写（规格书 §5.3 幂等只读的硬保证）。

### 骨架假设清单（精调时逐项确认/修改）

| 假设 | 取值 | 位置 | 备注 |
|---|---|---|---|
| 睡眠分期采样间隔 | 每条 5 分钟 | `aggregator.SLEEP_STAGE_MIN` | Gadgetbridge 惯例，待过夜数据确认 |
| 活动采样间隔 | 每条 1 分钟 | `aggregator.ACTIVITY_SAMPLE_MIN` | 用于折算 active_min，待过夜数据确认 |
| 活动状态 raw_kind | `{1}` 视为活动中 | `db.ACTIVE_RAW_KINDS` | Gadgetbridge ActivityKind 惯例，待过夜数据确认 |
| 每日汇总 date 列 | 真机为 TIMESTAMP 秒级时间戳 | `aggregator._daily_row_date` | ✅ 已按真机确认 |
| quality 字段 | 占位启发式（有完整睡眠段即 normal） | `aggregator.aggregate_sleep` | 真实评分规则待晨曦定义 |

---

## 4. MCP 工具接口（对齐规格书 §3 + 评审④补丁）

### 4.1 `get_hr`

```json
// 入参
{ "count": 10 }                          // 默认 10，自动钳制到 [1, 100]
// 返回（samples 按时间正序，已过滤无效心率）
{ "samples": [{"ts": "2026-08-19T08:30:00", "bpm": 72}],
  "source": "gadgetbridge", "stale": false }
```

### 4.2 `get_sleep`

```json
// 入参
{ "date": "2026-08-18" }                 // 默认今天
// 返回（评审④补丁：total_min 与 fell_asleep_date 均已实现）
{ "date": "2026-08-18",
  "fell_asleep": "23:40",
  "fell_asleep_date": "2026-08-17",      // 跨天归属，防 AI 误读
  "woke_up": "07:20",
  "total_min": 460,
  "stages": {"light": 300, "deep": 100, "rem": 60},
  "quality": "normal",
  "stale": false }
```

无数据时：所有字段置空/零值，`"stale": true`。

### 4.3 `get_daily`

```json
// 入参
{ "date": "2026-08-18" }                 // 默认今天
// 返回
{ "date": "2026-08-18", "steps": 8234, "active_min": 45,
  "hr_avg": 71, "hr_min": 55, "hr_max": 128, "stale": false }
```

步数优先取每日汇总表；该行缺失时用当天活动采样累加兜底。

---

## 5. 数据源约定（真机验证总表 · 2026-08-21 已验证）

以下表名/列名均来自用户手环 9 真机导出的 Gadgetbridge 数据库，**已经逐列核对**。

| 表名（规格书口径） | 代码常量 | 依赖列 | 验证 |
|---|---|---|---|
| `XIAOMI_ACTIVITY_SAMPLE` | `db.TABLE_ACTIVITY` | TIMESTAMP, HEART_RATE, STEPS, RAW_INTENSITY, RAW_KIND, SPO2, DISTANCE_CM, ACTIVE_CALORIES, ENERGY | ✅ |
| `XIAOMI_SLEEP_STAGE_SAMPLE` | `db.TABLE_SLEEP_STAGE` | TIMESTAMP, STAGE | ✅（0 行，待过夜数据） |
| `XIAOMI_DAILY_SUMMARY_SAMPLE` | `db.TABLE_DAILY` | TIMESTAMP, STEPS, HR_RESTING, HR_AVG, HR_MIN, HR_MAX, STRESS_AVG | ✅ |
| `XIAOMI_SLEEP_TIME_SAMPLE` | `db.TABLE_SLEEP_TIME` | TIMESTAMP, WAKEUP_TIME, TOTAL_DURATION, DEEP_SLEEP_DURATION, LIGHT_SLEEP_DURATION, REM_SLEEP_DURATION | ✅（0 行，待过夜数据） |

真机验证结论（2026-08-21）：
- ✅ 时间戳为**秒级**（`db.TS_UNIT` 保持默认）
- ✅ 列名全部**大写**（与骨架初始假设的小写不同，已在 db.py/aggregator.py 修正）
- ✅ 每日汇总 TIMESTAMP 为当天 0 点的秒级时间戳（`_daily_row_date` 已按此解析）
- ⏳ 睡眠表暂为 0 行——手环 9 的过夜睡眠数据待同步后验证分期编码（0/1/2/3）

---

## 6. 真机验证指引（给晨曦的操作清单）

1. **导出数据**：在 Gadgetbridge 中把数据库导出位置设为 Download，
   手动同步一次手环，导出 SQLite，复制到电脑（建议放
   `miband-mcp\data\Gadgetbridge`，即可走默认路径）。
2. **看表结构**：
   ```bat
   python -c "import sqlite3; c=sqlite3.connect(r'数据文件路径'); print('\n'.join(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")))"
   ```
   对照 §5 总表，记录真实表名/列名差异。
3. **改代码**：差异全部集中在 `db.py`（表名常量、列名、TS_UNIT）与
   `aggregator.py`（两个采样间隔常量、ACTIVE_RAW_KINDS）——改完跑测试。
4. **跑真实数据**：
   ```bat
   python miband_mcp.py --db data\Gadgetbridge
   ```
   手工输入 `tools/call` JSON（或用任意 MCP 客户端），核对返回值与
   Gadgetbridge App 里显示的数据是否一致。
5. **同步测试基线**：如果真实结构与骨架假设差异大，先改
   `tests/fixture_builder.py` 让 fixture 逼近真实结构，再跑
   `python -m unittest discover -s tests -v`。

---

## 7. 验收记录（规格书 §7，全部实测）

| 验收项 | 结果 | 证据 |
|---|---|---|
| `python miband_mcp.py` 启动，stderr 有 banner | ✅ | 实测输出 `miband-mcp started, db=<path>` |
| JSON-RPC initialize 握手通过 | ✅ | 端到端会话返回 protocolVersion + serverInfo |
| 对 fixture SQLite 调 `get_hr` 返回样本 | ✅ | 返回 2 条样本（count=2），时间正序、无 255 |
| `get_sleep` 能聚合分期 | ✅ | 返回 fell_asleep/fell_asleep_date/woke_up/total_min/stages |
| `get_daily` 返回步数/心率区间 | ✅ | steps=8234, hr_avg/min/max 与 fixture 一致 |
| 零第三方依赖 | ✅ | 全项目 import 核查：仅 os/sys/json/sqlite3/datetime/subprocess/unittest/tempfile/shutil |
| tests 通过 | ✅ | `python -m unittest discover -s tests -v` → Ran 34 tests, OK |

端到端会话实测输出摘录（2026-08-19）：

```
id=1 initialize -> serverInfo: miband-mcp 0.1.0, protocolVersion 2024-11-05
id=3 get_hr(count=2) -> {"samples":[{"ts":"2026-08-19T20:10:00","bpm":71},
                        {"ts":"2026-08-19T20:20:00","bpm":72}],"source":"gadgetbridge","stale":false}
id=4 get_sleep(2026-08-18) -> {"fell_asleep":"23:40","fell_asleep_date":"2026-08-17",
                        "woke_up":"07:20","total_min":460,"stages":{...},"quality":"normal"}
id=5 get_daily(2026-08-18) -> {"steps":8234,"active_min":3,"hr_avg":82,"hr_min":72,"hr_max":95}
```

---

## 8. 待办清单（按优先级）

**P0（阻塞上线）**
1. 真机导出 Gadgetbridge 数据库，逐列核对 §5 总表（晨曦）
2. 按核对结果修正 db.py / aggregator.py 常量，更新 fixture 与测试（晨曦）
3. Reasonix 侧注册本 MCP Server（stdio 命令 + MIBAND_DB 环境变量配置）

**P1（体验相关）**
4. 定义 `quality` 的真实评分规则（当前是占位启发式）
5. 确认 Gadgetbridge 同步/导出的自动化方式（定时任务 or 手动），
   决定 `stale` 判定是否需要升级为"按最后样本时间戳计算滞后小时数"
6. `get_daily` 的 calories 字段当前未返回（规格书返回签名未包含），
   如 AI 需要可加一个字段

**P2（可选增强）**
7. 支持 `get_hr` 按时间窗查询（start/end 参数）
8. 支持 `get_sleep` 返回周/月趋势
9. 数据导出 JSON/CSV 工具（给健康报告类场景用）

---

## 9. 风险点（坦诚清单）

1. **表结构未验证是最大风险**：全部数据层假设来自社区口径。如果真机
   表名/列名有出入，db.py 需要小改（预计半小时内，因为映射集中）；
   如果 Gadgetbridge 对小米手环 9 的支持本身有缺口（方案 3 提到手环 10
   是"实验性支持"，固件 3.1.16 + GB 0.92.0 组合有断连记录），则属于
   上游问题，本项目无法兜底，只能换固件/GB 版本组合。
2. **数据新鲜度**：读的是"已同步并导出"的快照，不是实时数据。
   AI 回答"现在心率多少"时，答案可能滞后数小时——`stale` 标记只覆盖
   "完全无数据"，**不覆盖"有数据但很旧"**，这个口径要同步给 Reasonix
   的提示词（见 §10 配置示例）。
3. **小米手环 9 的 Gadgetbridge 支持成熟度**未在本机实测，属于方案层
   风险（见方案 3 的复现注意事项）。
4. **零依赖的代价**：MCP 协议只实现了 initialize/tools/list/tools/call，
   若 Reasonix 客户端将来要求 resources/prompts 等能力，需要扩协议层。
5. **时间语义**：全部按电脑本地时区解释。若手环/手机/电脑时区不一致，
   日期归属可能偏移。

---

## 10. Reasonix 侧接入配置（参考）

stdio 方式注册（MCP 客户端通用格式）：

```json
{
  "mcpServers": {
    "miband": {
      "command": "D:\\Python3.14\\python.exe",
      "args": ["D:\\Programs\\Workspace\\Reasonix\\helloWorld\\projects\\WeCreate\\miband-mcp\\miband_mcp.py"],
      "env": { "MIBAND_DB": "D:\\path\\to\\Gadgetbridge" }
    }
  }
}
```

建议在 Reasonix 的系统提示中说明：本数据源为 Gadgetbridge 同步快照，
非实时；回答"当前"类问题时先检查 `stale` 与样本时间戳。

---

## 11. 维护说明

- **跑测试**：`python -m unittest discover -s tests -v`（零依赖，无需 pytest）
- **手工会话**：启动进程后逐行输入 JSON-RPC（参照 §7 的会话示例）
- **排错顺序**：stderr 看 banner/错误 → 确认 DB 路径存在 →
  用 §6 第 2 步的命令看真实表结构 → 对照 §5 总表定位差异
- **改动纪律**：数据层只动 db.py，聚合逻辑只动 aggregator.py，
  协议层只动 miband_mcp.py；任何改动后必须全量跑测试

---

*本手册随代码同步更新；精调后请晨曦更新版本号与验收记录。*
