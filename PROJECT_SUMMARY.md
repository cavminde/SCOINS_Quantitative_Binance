# 🤖 Binance 网格交易系统 v2.0

> 基于 Binance 现货 API 的自动化网格交易系统 —— 输入币种，系统自动分析买卖点位并返回网格区间

---

## 📁 项目结构

```
Bincion/
├── __init__.py              (27行)  包入口，项目说明文档
├── run.py                   (1082行) 主入口 — GUI监控 + 交易循环 + CLI模式
├── check_ip.py              (133行)  IP白名单检测工具
├── debug_api.py             (401行)  API连接诊断工具
├── requirements.txt         (18行)   依赖清单
│
├── app/                             应用核心模块
│   ├── __init__.py          (2行)    模块入口
│   ├── authorization.py     (41行)   API密钥配置（已脱敏）
│   ├── BinanceAPI.py        (826行)  Binance现货API封装（完整REST v3）
│   ├── strategy.py          (655行)  ★ 核心战略类 — 买入/卖出决策引擎
│   └── dingding.py          (424行)  钉钉机器人消息通知
│
└── data/                             数据层
    ├── __init__.py          (2行)    模块入口
    ├── data.json            (45行)   交易币种和策略配置
    ├── runBetData.py        (506行)  运行数据管理（JSON读写/原子写入）
    └── calcIndex.py         (602行)  技术指标计算（MA/EMA/ATR/RSI/MACD/布林带/BIAS/量能）
```

**总计：14 个文件，约 4,800 行代码**

---

## 🧠 核心设计 — 策略独立化

> 买入/卖出的核心决策逻辑已从 `run.py` 中独立到 `app/strategy.py`，采用**策略模式**设计。

### GridStrategy 战略类工作流程

```
输入币种 (如 BTCUSDT)
    │
    ▼
Step 1 → 获取实时行情数据 (当前价格 + 5m K线)
    │
    ▼
Step 2 → 计算技术指标
    │   ├── MA(5/20)   — 移动平均线
    │   ├── ATR(20)    — 平均真实波幅
    │   └── 趋势判断    — 上涨 / 下跌 / 横盘
    │
    ▼
Step 3 → 策略分析引擎
    │   ├── 买入价 = 基准价 × (1 − 补仓比率%)
    │   ├── 卖出价 = 基准价 × (1 + 止盈比率%)
    │   ├── ATR 动态调整网格间距
    │   ├── 止损价 = 买入价 × (1 − 止损比率%)
    │   └── 趋势过滤 (下跌趋势不买入)
    │
    ▼
Step 4 → 输出 GridResult 决策结果
    │   ├── signal:           BUY / SELL / HOLD / STOP_LOSS
    │   ├── buy_price:        买入触发价
    │   ├── sell_price:       卖出触发价
    │   ├── grid_interval:    网格区间宽度 (价格差 + 百分比)
    │   ├── confidence:       信号置信度 (0.0 ~ 1.0)
    │   ├── reason:           决策理由说明 (便于人工复核)
    │   └── next_grid_prices: 后续各层网格买入价格列表
```

### 关键枚举定义

| 枚举 | 值 | 含义 |
|------|-----|------|
| `SignalType.BUY` | 买入 | 当前价格 ≤ 买入触发价 |
| `SignalType.SELL` | 卖出 | 当前价格 ≥ 卖出触发价 |
| `SignalType.HOLD` | 持仓 | 价格在网格区间内，继续等待 |
| `SignalType.STOP_LOSS` | 止损 | 价格跌破止损线，强制卖出 |
| `SignalType.ERROR` | 异常 | 数据获取失败 |
| `MarketTrend.UPTREND` | 上涨 | MA5 > MA20 且价格在 MA20 上方 |
| `MarketTrend.DOWNTREND` | 下跌 | MA5 < MA20 且价格在 MA20 下方 |
| `MarketTrend.SIDEWAYS` | 横盘 | 价格在 MA 附近震荡 |

### 策略分析自测结果

```
场景1：空仓状态，价格处于买入区间 (85000)
  → 信号: HOLD (价格在网格区间内，距买入价2.04%，距卖出价1.0%)

场景2：持仓状态step=1，价格触及卖出价 (86500)
  → 信号: SELL (预计盈利 +1.76%)

场景3：持仓状态，价格跌破止损线 (80000, 买入价85000)
  → 信号: STOP_LOSS (止损价 80750)
```

---

## 🔌 API 封装 (BinanceAPI)

### 已实现的接口

| 分类 | 接口 | 方法 | 说明 |
|------|------|------|------|
| 公开 | `GET /api/v3/ping` | `ping()` | 连通性测试 |
| 公开 | `GET /api/v3/ticker/price` | `get_ticker_price()` | 最新成交价 |
| 公开 | `GET /api/v3/ticker/24hr` | `get_ticker_24hour()` | 24小时行情统计 |
| 公开 | `GET /api/v3/klines` | `get_klines()` | K线数据 |
| 公开 | `GET /api/v3/exchangeInfo` | `get_exchange_info()` | 交易对信息/精度 |
| 公开 | `GET /api/v3/depth` | `get_order_book()` | 订单簿深度 |
| 签名 | `GET /api/v3/account` | `get_account()` | 账户信息 |
| 签名 | `GET /api/v3/openOrders` | `get_open_orders()` | 当前挂单 |
| 签名 | `GET /api/v3/order` | `get_order()` | 查询订单 |
| 签名 | `GET /api/v3/myTrades` | `get_trades()` | 成交记录 |
| 签名 | `POST /api/v3/order` | `buy_market()` | 市价买入 |
| 签名 | `POST /api/v3/order` | `sell_market()` | 市价卖出 |
| 签名 | `POST /api/v3/order` | `buy_limit()` | 限价买入 |
| 签名 | `POST /api/v3/order` | `sell_limit()` | 限价卖出 |
| 签名 | `DELETE /api/v3/order` | `cancel_order()` | 撤销订单 |

### API 端点管理

```
主网端点 (自动故障切换):
  https://api.binance.com/api/v3
  https://api1.binance.com/api/v3
  https://api2.binance.com/api/v3
  https://api3.binance.com/api/v3
  https://api4.binance.com/api/v3

测试网: https://testnet.binance.vision/api/v3
```

- 启动时自动探测各端点延迟，选择最快节点
- 请求失败时自动轮换到备用端点
- HMAC-SHA256 签名自动生成
- 下单数量根据交易对 stepSize 自动格式化

---

## 📊 技术指标 (CalcIndex)

| 指标 | 方法 | 用途 |
|------|------|------|
| MA (移动平均线) | `ma()` | 趋势方向判断 |
| EMA (指数移动平均) | `ema()` | 近期价格侧重 |
| ATR (平均真实波幅) | `atr()` | 网格间距动态调整 |
| RSI (相对强弱指数) | `rsi()` | 超买超卖判断 |
| MACD | `macd()` | 金叉死叉信号 |
| 布林带 | `bollinger_bands()` | 价格区间/突破信号 |
| BIAS (乖离率) | `bias()` | 价格偏离均线程度 |
| 量比 | `volume_ratio()` | 成交量确认 |
| 综合评分 | `trend_score()` | 100分制多维度评分 |

---

## 🔔 通知模块 (DingDingNotifier)

| 方法 | 触发场景 |
|------|----------|
| `send_buy_notification()` | 买入成交 |
| `send_sell_notification()` | 卖出成交（含盈亏） |
| `send_stop_loss_alert()` | 止损触发 |
| `send_error_alert()` | API异常/系统错误 |
| `send()` | 自定义消息 |

兼容旧版 `Message` 接口，无需改动现有调用代码。

---

## 🚀 启动方式

```bash
# GUI 监控面板（默认，Windows/macOS）
python run.py

# 纯命令行模式（Linux 服务器后台运行）
python run.py --no-gui

# 仅分析模式（不执行实际交易，用于查看信号和验证策略）
python run.py --analyze

# 快速分析指定币种（输入币种 → 自动返回买卖点和网格区间）
python run.py BTCUSDT

# API 连接诊断
python debug_api.py

# 公网 IP 检测（用于配置 Binance IP 白名单）
python check_ip.py
```

### 命令行参数

```
python run.py -h

usage: run.py [-h] [--no-gui] [--analyze] [coin]

positional arguments:
  coin        要分析的交易对（如 BTCUSDT）

optional arguments:
  --no-gui    纯命令行模式
  --analyze   仅分析模式（不执行交易）
```

---

## ⚙️ 配置文件说明 (data/data.json)

```json
{
    "coinList": ["BTCUSDT"],        // 交易币种列表
    "BTCUSDT": {
        "benchmark": "BTCUSDT",
        "chain": "Binance",
        "runBet": {                  // 运行状态（系统自动维护）
            "next_buy_price": 85000,   // 下次买入触发价
            "grid_sell_price": 87000,  // 当前卖出触发价
            "step": 0,                // 当前步数（0=空仓）
            "recorded_price": []      // 历史买入价格记录
        },
        "config": {                  // 策略参数（用户配置）
            "profit_ratio": 1.0,       // 止盈比率 %
            "double_throw_ratio": 2.0, // 补仓比率 %
            "stop_loss_ratio": 5.0,    // 止损比率 %
            "quantity": [0.001, 0.002, 0.004, 0.008]  // 各层买入数量
        }
    }
}
```

---

## 📈 相比 COIN 原版的改进

| 方面 | COIN 原版 | Binance v2.0 |
|------|-----------|--------------|
| **Python 标准** | 旧式风格，无类型提示 | 完整类型提示 + dataclass + enum |
| **策略逻辑** | 散落在 `run.py` 的 `Run_Main` 中 | **独立 `GridStrategy` 战略类**，策略模式，支持扩展 |
| **API 封装** | 部分方法缺失，错误处理弱 | 完整 REST v3 封装 + 自定义异常 `BinanceAPIException` |
| **端点管理** | 手动轮换 | 自动延迟探测 + 故障切换 |
| **中文注释** | 极少 | **详尽中文文档注释**，每个方法、参数、返回值均有说明 |
| **通知模块** | 紧耦合 | `DingDingNotifier` 独立 + `Message` 兼容桥接 |
| **技术指标** | 仅 MA 斜率 | MA/EMA/ATR/RSI/MACD/布林带/BIAS/量能/综合评分 |
| **启动方式** | 仅 GUI | GUI + CLI + 分析模式 + 单币种快速查询 |
| **配置文件** | 密钥明文硬编码 | 占位符替换，提醒脱敏 |
| **数据安全** | 直接写入 | 原子写入（先写临时文件再替换），防止断电损坏 |
| **错误处理** | print + 粗糙 | logging 分级 + 自定义异常 + 钉钉告警 |
| **测试自检** | 无 | strategy / runBetData / calcIndex 均有自测代码 |

---

## 🔐 安全提示

1. **修改密钥**：编辑 `app/authorization.py`，填入你的真实 API Key 和 Secret
2. **IP 白名单**：运行 `python check_ip.py` 获取公网 IP，添加到 Binance API 管理后台
3. **权限最小化**：API Key 仅开启「现货交易」和「读取」权限
4. **勿提交密钥**：确保 `authorization.py` 已加入 `.gitignore`
5. **先测试后实盘**：先用 `--analyze` 模式观察信号，确认无误后再启动交易
6. **测试网先行**：在 `https://testnet.binance.vision/` 注册测试网密钥，用测试网验证

---

## 🧪 快速验证

```bash
# 1. 检查 Python 版本 (推荐 3.10+)
python --version

# 2. 安装依赖
pip install requests

# 3. 测试策略引擎（无需网络）
python app/strategy.py

# 4. 检查 API 连接（需要网络 + 密钥）
python debug_api.py --public

# 5. 分析一个币种（查看买卖点和网格区间）
python run.py BTCUSDT
```

---

*文档生成日期：2026-06-15*
