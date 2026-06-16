# -*- coding: utf-8 -*-
"""
网格交易系统 v3.0 - 主入口（架构重构版）
========================================

**架构说明**
------------
```
        ┌────────────────────────────────────┐
        │      GUI（用户输入币种）             │
        └──────────────┬─────────────────────┘
                       │ 添加/移除币种
                       ↓
        ┌────────────────────────────────────┐
        │  TradingEngine (run.py)            │
        │  - 轮询每个币种                     │
        │  - 调 strategy.decide() 获信号     │
        │  - 调 BinanceAPI 下单              │
        │  - 状态写入 data.json              │
        └──────────────┬─────────────────────┘
                       │ decide(context)
                       ↓
        ┌────────────────────────────────────┐
        │  Strategy (strategy/builtin/)      │
        │  - BaseStrategy 子类               │
        │  - 输出 StrategyDecision           │
        └────────────────────────────────────┘
```

**关键变化**
------------
1. **不再人工触发**：币种添加即自动启动监控，strategy 给出买卖点位
2. **策略插件化**：新增策略只需在 `strategy/builtin/` 下实现 BaseStrategy
3. **data.json 简化**：仅存储持仓与策略配置，触发价由 strategy 实时计算
4. **GUI 输入币种即上车**：不再需要手动编辑 data.json

启动方式：
    python run.py              # 启动 GUI
    python run.py --no-gui     # CLI 模式
    python run.py --analyze    # 仅分析模式
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

# ============================================================
# 第三方 / 内部模块
# ============================================================
from app.BinanceAPI import BinanceAPI
from app.authorization import api_key, api_secret, dingding_token
from app.dingding import Message, DingDingNotifier
from data.runBetData import DataStore
from data.calcIndex import CalcIndex

# 标准策略接口
from strategy.base import (
    ActionType,
    BaseStrategy,
    MarketContext,
    MarketTrend,
    OrderRecord,
    StrategyDecision,
)
from strategy.registry import registry, auto_load

# 启动时立即触发内置策略自动注册
auto_load()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("GridTrader")

DATA_PATH: Path = Path(__file__).parent / "data" / "data.json"


# ============================================================
# 策略管理器
# ============================================================

class StrategyManager:
    """
    **策略管理器**：维护 ``symbol -> strategy_instance`` 映射

    当 data.json 中新增币种时自动创建对应策略实例；
    当移除币种时自动销毁实例。
    """

    def __init__(self, data: DataStore) -> None:
        self._data = data
        self._strategies: dict[str, BaseStrategy] = {}
        self._lock = threading.RLock()
        self.reload_all()

    def reload_all(self) -> None:
        """根据 data.json 重新加载全部策略"""
        with self._lock:
            symbols = self._data.get_coin_list()
            # 移除已不存在的
            for s in list(self._strategies.keys()):
                if s not in symbols:
                    del self._strategies[s]
            # 加载新增/变化的
            for s in symbols:
                state = self._data.get_state(s)
                if s not in self._strategies or self._need_reload(s, state):
                    self._load_strategy(s, state)

    def _need_reload(self, symbol: str, state: dict) -> bool:
        """检查策略是否需要重新加载（参数或类型变化）"""
        existing = self._strategies.get(symbol)
        if existing is None:
            return True
        return (
            existing.name != state["strategy"]
            or existing.params != state["params"]
        )

    def _load_strategy(self, symbol: str, state: dict) -> None:
        """加载单个币种的策略实例"""
        name = state["strategy"]
        try:
            cls = registry.get(name)
            inst: BaseStrategy = cls(symbol, state["params"])
            inst.on_init()
            self._strategies[symbol] = inst
            logger.info("已加载策略: %s -> %s", symbol, cls.__name__)
        except KeyError as exc:
            logger.error("策略 %s 未注册: %s。可用: %s",
                         name, exc, registry.names())

    def get(self, symbol: str) -> BaseStrategy | None:
        return self._strategies.get(symbol)

    def all(self) -> dict[str, BaseStrategy]:
        return dict(self._strategies)

    def reload_one(self, symbol: str) -> None:
        """重新加载单个币种的策略"""
        with self._lock:
            self._strategies.pop(symbol, None)
            state = self._data.get_state(symbol)
            self._load_strategy(symbol, state)

    def add_symbol(self, symbol: str, strategy_name: str = "grid",
                   params: dict | None = None) -> None:
        """添加币种并自动加载策略"""
        with self._lock:
            self._data.add_coin(symbol, strategy_name=strategy_name, params=params)
            self.reload_all()

    def remove_symbol(self, symbol: str) -> None:
        """移除币种并销毁策略实例"""
        with self._lock:
            self._data.remove_coin(symbol)
            self._strategies.pop(symbol, None)


# ============================================================
# 交易引擎
# ============================================================

class TradingEngine:
    """
    **交易引擎** — 按币种轮询 strategy，自动执行交易

    工作流程（每轮）：
        for 币种 in coinList:
            1. 获取当前价格 + K线
            2. 构造 MarketContext
            3. 调 strategy.decide() 获取决策
            4. 根据决策执行买卖
            5. 更新状态到 data.json
            6. 通知 GUI
    """

    def __init__(self) -> None:
        self.api = BinanceAPI(api_key, api_secret)
        self.data = DataStore(DATA_PATH)
        self.msg = Message(self.api)
        self.calc = CalcIndex()
        self.strategies = StrategyManager(self.data)

        # ── 回调（GUI 使用）──
        self.on_log: callable | None = None
        self.on_signal: callable | None = None      # (symbol, decision)
        self.on_price: callable | None = None       # (symbol, price)
        self.on_status: callable | None = None      # (status)

        # ── 运行状态 ──
        self._running = False
        self._paused = False
        self._thread: threading.Thread | None = None

        self._log(f"API 连接成功，端点: {self.api.base_url}")
        self._log(f"已加载 {len(self.strategies.all())} 个交易对策略")

    # ----------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._log("🚀 交易引擎已启动")
        self._notify_status("运行中")

    def stop(self) -> None:
        self._running = False
        self._log("⏹️ 交易引擎已停止")
        self._notify_status("已停止")

    def pause(self) -> None:
        self._paused = not self._paused
        status = "已暂停" if self._paused else "已恢复"
        self._log(f"{'⏸️' if self._paused else '▶️'} 交易引擎{status}")
        self._notify_status(status)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ----------------------------------------------------------
    # 主循环
    # ----------------------------------------------------------

    def _loop(self) -> None:
        self._log("交易循环启动，开始监控所有币种...")
        while self._running:
            if self._paused:
                time.sleep(1)
                continue

            symbols = self.data.get_coin_list()
            if not symbols:
                self._log("⚠️ 监控列表为空，请在 GUI 添加币种")
                time.sleep(5)
                continue

            for s in symbols:
                if not self._running:
                    break
                try:
                    self._process_symbol(s)
                except Exception as exc:
                    logger.exception("处理 %s 异常", s)
                    self._log(f"⚠️ {s} 异常: {exc}")
                time.sleep(1)
            time.sleep(2)

    def _process_symbol(self, symbol: str) -> None:
        """处理单个币种：拉取数据 → 调 strategy → 执行交易"""
        # 1) 价格 + K线
        current_price = self.api.get_ticker_price(symbol)
        if current_price is None:
            self._log(f"⚠️ {symbol} 价格获取失败")
            return
        if self.on_price:
            self.on_price(symbol, current_price)

        try:
            klines = self.api.get_klines(symbol, "5m", 50) or []
        except Exception:
            klines = []

        # 2) 读取状态 + 构造 context
        state = self.data.get_state(symbol)
        strategy = self.strategies.get(symbol)
        if strategy is None:
            self.strategies.reload_one(symbol)
            strategy = self.strategies.get(symbol)
            if strategy is None:
                return

        context = MarketContext(
            symbol=symbol,
            current_price=current_price,
            klines=klines,
            interval="5m",
            step=state["step"],
            last_buy_price=state["last_buy_price"],
            position_qty=self._estimate_position_qty(symbol),
            balance=0.0,
        )

        # 3) 决策
        try:
            decision = strategy.decide(context)
        except Exception as exc:
            logger.exception("策略 %s.decide() 异常", symbol)
            self._log(f"❌ {symbol} 策略异常: {exc}")
            return

        if self.on_signal:
            self.on_signal(symbol, decision)

        # 4) 执行
        self._execute(symbol, decision, current_price)

    def _execute(self, symbol: str, decision: StrategyDecision,
                 current_price: float) -> None:
        """根据策略决策执行交易"""
        action = decision.action

        if action == ActionType.BUY:
            quantity = decision.quantity or self.data.get_state(symbol)["params"].get("quantity", 9.1)
            self._log(
                f"📈 {symbol} {decision.reason} | "
                f"数量={quantity} 价格={current_price}"
            )
            res = self.msg.buy_market_msg(symbol, quantity)
            if isinstance(res, dict) and "orderId" in res:
                try:
                    fill_price = float(res["fills"][0]["price"])
                except (KeyError, IndexError, ValueError):
                    fill_price = current_price
                self.data.record_buy(symbol, fill_price)
                self.strategies.get(symbol).on_order_filled(
                    OrderRecord(symbol, ActionType.BUY, fill_price, quantity, time.time())
                )
                self._log(f"✅ {symbol} 买入成功！成交价 {fill_price}")
                self._cool_down(60)
            else:
                self._log(f"❌ {symbol} 买入失败: {res}")

        elif action == ActionType.SELL:
            quantity = decision.quantity or self._estimate_position_qty(symbol)
            if quantity <= 0:
                self._log(f"⚠️ {symbol} 卖出信号但无持仓")
                return
            last_buy = self.data.get_state(symbol)["last_buy_price"]
            profit = (current_price - last_buy) * quantity if last_buy > 0 else 0
            self._log(
                f"📉 {symbol} {decision.reason} | "
                f"数量={quantity} 预计盈利={profit:.2f} USDT"
            )
            res = self.msg.sell_market_msg(symbol, quantity, profit)
            if isinstance(res, dict) and "orderId" in res:
                self.data.record_sell(symbol)
                self.strategies.get(symbol).on_order_filled(
                    OrderRecord(symbol, ActionType.SELL, current_price, quantity,
                                time.time(), profit=profit)
                )
                self._log(f"✅ {symbol} 卖出成功！盈利 {profit:.2f} USDT")
                self._cool_down(30)
            else:
                self._log(f"❌ {symbol} 卖出失败: {res}")

        elif action == ActionType.STOP_LOSS:
            quantity = decision.quantity or self._estimate_position_qty(symbol)
            if quantity <= 0:
                return
            last_buy = self.data.get_state(symbol)["last_buy_price"]
            loss = (current_price - last_buy) * quantity if last_buy > 0 else 0
            self._log(f"🚨 {symbol} 止损！{decision.reason}")
            res = self.msg.sell_market_msg(symbol, quantity, loss)
            if isinstance(res, dict) and "orderId" in res:
                self.data.record_sell(symbol)
                self._log(f"止损成交，亏损 {loss:.2f} USDT")
                self._cool_down(30)

        else:
            # HOLD / ERROR：仅记录
            pass

    # ----------------------------------------------------------
    # 便捷方法
    # ----------------------------------------------------------

    def _estimate_position_qty(self, symbol: str) -> float:
        """估算当前持仓数量（优先用 state.params.quantity）"""
        try:
            state = self.data.get_state(symbol)
            return float(state["params"].get("quantity", 0.0)) if state["step"] > 0 else 0.0
        except Exception:
            return 0.0

    def _cool_down(self, seconds: int) -> None:
        waited = 0
        while waited < seconds and self._running:
            time.sleep(1)
            waited += 1

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self.on_log:
            self.on_log(msg)

    def _notify_status(self, status: str) -> None:
        if self.on_status:
            self.on_status(status)

    # ----------------------------------------------------------
    # 公共 API（GUI 调用）
    # ----------------------------------------------------------

    def add_coin(self, symbol: str, strategy: str = "grid",
                 params: dict | None = None) -> bool:
        """GUI 添加币种"""
        symbol = symbol.upper().strip()
        if not symbol:
            return False
        try:
            # 先验证交易对是否存在
            price = self.api.get_ticker_price(symbol)
            if price is None or price <= 0:
                self._log(f"❌ {symbol} 不存在或价格无效")
                return False
            self.strategies.add_symbol(symbol, strategy, params)
            self._log(f"✅ 已添加 {symbol}（策略={strategy}，当前价={price}）")
            return True
        except Exception as exc:
            self._log(f"❌ 添加 {symbol} 失败: {exc}")
            return False

    def remove_coin(self, symbol: str) -> None:
        self.strategies.remove_symbol(symbol)
        self._log(f"已移除 {symbol}")

    def analyze_coin(self, symbol: str) -> StrategyDecision | None:
        """分析指定币种（不实际交易）"""
        symbol = symbol.upper().strip()
        try:
            price = self.api.get_ticker_price(symbol)
            if not price:
                return None
            try:
                klines = self.api.get_klines(symbol, "5m", 50) or []
            except Exception:
                klines = []
            state = self.data.get_state(symbol)
            strategy = self.strategies.get(symbol)
            if strategy is None:
                self.strategies.add_symbol(symbol)
                strategy = self.strategies.get(symbol)
                if strategy is None:
                    return None
            ctx = MarketContext(
                symbol=symbol,
                current_price=price,
                klines=klines,
                interval="5m",
                step=state["step"],
                last_buy_price=state["last_buy_price"],
                position_qty=self._estimate_position_qty(symbol),
            )
            return strategy.decide(ctx)
        except Exception as exc:
            self._log(f"❌ 分析 {symbol} 失败: {exc}")
            return None


# ============================================================
# GUI 监控面板
# ============================================================

class TradingMonitorGUI:
    """
    **网格交易监控面板**（基于 Tkinter）

    改进点（相对 v2.0）：
    - **币种输入框升级为主操作入口**：输入 + 回车即添加
    - 表格列增加"策略"列，显示当前币种使用的策略名
    - "分析"按钮可对任意币种做单次分析
    """
    def __init__(self, root, engine: TradingEngine) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.ttk = ttk

        self.root = root
        self.engine = engine
        self.root.title("Binance 网格交易系统 v3.0（标准策略接口）")
        self.root.geometry("1300x780")

        # 注册回调
        engine.on_log = self._append_log
        engine.on_signal = self._update_signal
        engine.on_price = self._update_price
        engine.on_status = self._update_status

        self._build_ui()
        self._refresh_coin_list()
        self._append_log("✅ 监控系统初始化完成")
        self._append_log(f"已加载 {len(self.engine.strategies.all())} 个交易对")
        self._append_log(f"已注册策略: {', '.join(registry.names())}")

    # ----------------------------------------------------------
    # 界面构建
    # ----------------------------------------------------------

    def _build_ui(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        # ── 标题 ──
        title = ttk.Frame(self.root)
        title.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(
            title, text="🤖 Binance 网格交易系统 v3.0",
            font=("Microsoft YaHei", 18, "bold"),
        ).pack(side=tk.LEFT)

        # ── 币种输入区（** 主入口 **）──
        input_frame = ttk.LabelFrame(self.root, text="添加交易对（回车即可添加）", padding=8)
        input_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(input_frame, text="交易对:", font=("Microsoft YaHei", 10)).pack(
            side=tk.LEFT, padx=3
        )
        self._input_coin_var = tk.StringVar()
        coin_entry = ttk.Entry(input_frame, textvariable=self._input_coin_var,
                               width=15, font=("Consolas", 11))
        coin_entry.pack(side=tk.LEFT, padx=3)
        coin_entry.bind("<Return>", lambda e: self._on_add_coin())

        ttk.Label(input_frame, text="策略:", font=("Microsoft YaHei", 10)).pack(
            side=tk.LEFT, padx=(15, 3)
        )
        self._strategy_var = tk.StringVar(value="grid")
        strategy_cb = ttk.Combobox(
            input_frame, textvariable=self._strategy_var,
            values=registry.names(), state="readonly", width=10,
        )
        strategy_cb.pack(side=tk.LEFT, padx=3)

        ttk.Button(
            input_frame, text="➕ 添加并启动",
            command=self._on_add_coin, width=14,
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            input_frame, text="🔍 分析",
            command=self._on_analyze, width=8,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            input_frame, text="🗑️ 移除",
            command=self._on_remove_coin, width=8,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Label(
            input_frame,
            text="💡 输入交易对名称（如 BTCUSDT）回车即可自动监控",
            font=("Microsoft YaHei", 9), foreground="gray",
        ).pack(side=tk.LEFT, padx=10)

        # ── 控制面板 ──
        ctrl = ttk.Frame(self.root)
        ctrl.pack(fill=tk.X, padx=10, pady=5)

        self._btn_start = ttk.Button(ctrl, text="▶ 启动", command=self._on_start, width=10)
        self._btn_start.pack(side=tk.LEFT, padx=3)
        self._btn_pause = ttk.Button(ctrl, text="⏸ 暂停", command=self._on_pause,
                                      state=tk.DISABLED, width=10)
        self._btn_pause.pack(side=tk.LEFT, padx=3)
        self._btn_stop = ttk.Button(ctrl, text="⏹ 停止", command=self._on_stop,
                                     state=tk.DISABLED, width=10)
        self._btn_stop.pack(side=tk.LEFT, padx=3)
        ttk.Button(ctrl, text="🔄 刷新", command=self._refresh_coin_list, width=10).pack(
            side=tk.LEFT, padx=3
        )

        self._status_var = tk.StringVar(value="状态: 就绪")
        ttk.Label(ctrl, textvariable=self._status_var,
                  font=("Microsoft YaHei", 11, "bold"), foreground="blue").pack(
            side=tk.RIGHT, padx=15)

        # ── 主表格 ──
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        columns = ("交易对", "策略", "当前价", "买入触发", "卖出触发",
                   "区间%", "信号", "步数", "建议")
        self._tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            yscrollcommand=scrollbar.set, height=10,
        )
        scrollbar.config(command=self._tree.yview)

        widths = {
            "交易对": 100, "策略": 80, "当前价": 110,
            "买入触发": 110, "卖出触发": 110, "区间%": 70,
            "信号": 80, "步数": 50, "建议": 380,
        }
        for c in columns:
            self._tree.heading(c, text=c)
            self._tree.column(c, width=widths.get(c, 100), anchor="center")
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._tree.tag_configure("buy", background="#90EE90")
        self._tree.tag_configure("sell", background="#FFB6C1")
        self._tree.tag_configure("hold", background="#FFFACD")
        self._tree.tag_configure("stop", background="#FF6347")
        self._tree.tag_configure("trading", background="#ADD8E6")

        # ── 分析结果区 ──
        result_frame = ttk.LabelFrame(self.root, text="策略分析结果", padding=5)
        result_frame.pack(fill=tk.X, padx=10, pady=3)
        self._analyze_text = tk.Text(result_frame, height=5, font=("Consolas", 9),
                                      state=tk.DISABLED, bg="#f5f5f5")
        self._analyze_text.pack(fill=tk.X)

        # ── 日志区 ──
        log_frame = ttk.LabelFrame(self.root, text="交易日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=5)
        self._log_text = tk.Text(log_frame, height=8, font=("Consolas", 9),
                                  bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.config(yscrollcommand=log_scroll.set)
        self._log_text.tag_config("buy", foreground="#4ec94e")
        self._log_text.tag_config("sell", foreground="#ff6b6b")
        self._log_text.tag_config("warn", foreground="#ffd93d")

    # ----------------------------------------------------------
    # 事件处理
    # ----------------------------------------------------------

    def _on_add_coin(self) -> None:
        """添加币种：输入即上车"""
        symbol = self._input_coin_var.get().strip().upper()
        if not symbol:
            self._append_log("⚠️ 请输入交易对名称")
            return
        strategy = self._strategy_var.get() or "grid"
        self._append_log(f"⏳ 正在添加 {symbol}（策略={strategy}）...")
        threading.Thread(
            target=self._do_add_coin, args=(symbol, strategy), daemon=True
        ).start()

    def _do_add_coin(self, symbol: str, strategy: str) -> None:
        ok = self.engine.add_coin(symbol, strategy)
        self.root.after(0, lambda: self._refresh_coin_list())
        if ok:
            self.root.after(0, lambda: self._input_coin_var.set(""))

    def _on_remove_coin(self) -> None:
        sel = self._tree.selection()
        if not sel:
            self._append_log("⚠️ 请先在表格中选中要移除的交易对")
            return
        symbol = self._tree.item(sel[0], "values")[0]
        self.engine.remove_coin(symbol)
        self._refresh_coin_list()

    def _on_analyze(self) -> None:
        symbol = self._input_coin_var.get().strip().upper() or \
                 self._get_selected_symbol()
        if not symbol:
            self._append_log("⚠️ 请输入或选中要分析的交易对")
            return
        self._show_analysis(f"⏳ 正在分析 {symbol}...")

        def do():
            decision = self.engine.analyze_coin(symbol)
            if decision is None:
                self.root.after(0, lambda: self._show_analysis(
                    f"❌ {symbol} 分析失败（请检查名称是否正确）"))
                return
            text = (
                f"📊 {symbol} 策略分析\n"
                f"{'─' * 55}\n"
                f"  当前价格:     {decision.price}\n"
                f"  交易信号:     {decision.action.name} ({_action_cn(decision.action)})\n"
                f"  期望数量:     {decision.quantity}\n"
                f"  止损建议:     {decision.stop_loss}\n"
                f"  止盈建议:     {decision.take_profit}\n"
                f"  网格区间:     {decision.grid_buy} ~ {decision.grid_sell} "
                f"({decision.grid_interval_pct}%)\n"
                f"  决策理由:     {decision.reason}\n"
            )
            self.root.after(0, lambda: self._show_analysis(text))

        threading.Thread(target=do, daemon=True).start()

    def _get_selected_symbol(self) -> str | None:
        sel = self._tree.selection()
        if not sel:
            return None
        return self._tree.item(sel[0], "values")[0]

    def _show_analysis(self, text: str) -> None:
        import tkinter as tk
        self._analyze_text.config(state=tk.NORMAL)
        self._analyze_text.delete("1.0", tk.END)
        self._analyze_text.insert("1.0", text)
        self._analyze_text.config(state=tk.DISABLED)

    def _on_start(self) -> None:
        self.engine.start()
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._btn_pause.config(state="normal")

    def _on_stop(self) -> None:
        self.engine.stop()
        self._btn_start.config(state="normal")
        self._btn_stop.config(state="disabled")
        self._btn_pause.config(state="disabled", text="⏸ 暂停")

    def _on_pause(self) -> None:
        self.engine.pause()
        self._btn_pause.config(
            text="▶ 恢复" if self.engine.is_paused else "⏸ 暂停"
        )

    # ----------------------------------------------------------
    # 数据刷新
    # ----------------------------------------------------------

    def _refresh_coin_list(self) -> None:
        """根据 data.json 重建表格"""
        for item in self._tree.get_children():
            self._tree.delete(item)
        for s in self.engine.data.get_coin_list():
            state = self.engine.data.get_state(s)
            strat_name = state.get("strategy", "?")
            self._tree.insert(
                "", "end",
                values=(s, strat_name, "---", "---", "---", "---",
                        "等待", state.get("step", 0), "未启动" if not self.engine.is_running else "监控中"),
            )

    def _update_price(self, symbol: str, price: float) -> None:
        self.root.after(0, lambda: self._set_tree_value(
            symbol, 2, f"{price:.6f}"))

    def _update_signal(self, symbol: str, decision: StrategyDecision) -> None:
        def do():
            self._set_tree_value(symbol, 3, f"{decision.grid_buy:.6f}" if decision.grid_buy else "---")
            self._set_tree_value(symbol, 4, f"{decision.grid_sell:.6f}" if decision.grid_sell else "---")
            self._set_tree_value(symbol, 5, f"{decision.grid_interval_pct}%" if decision.grid_interval_pct else "---")
            self._set_tree_value(symbol, 6, _action_cn(decision.action))
            self._set_tree_value(symbol, 8, decision.reason[:60])

            state = self.engine.data.get_state(symbol)
            self._set_tree_value(symbol, 7, str(state.get("step", 0)))

            tag_map = {
                ActionType.BUY: "buy",
                ActionType.SELL: "sell",
                ActionType.HOLD: "hold",
                ActionType.STOP_LOSS: "stop",
            }
            for item in self._tree.get_children():
                if self._tree.item(item, "values")[0] == symbol:
                    self._tree.item(item, tags=(tag_map.get(decision.action, "hold"),))
                    break
        self.root.after(0, do)

    def _update_status(self, status: str) -> None:
        self.root.after(0, lambda: self._status_var.set(f"状态: {status}"))

    def _set_tree_value(self, symbol: str, col: int, value: str) -> None:
        for item in self._tree.get_children():
            v = self._tree.item(item, "values")
            if v[0] == symbol:
                new_v = list(v)
                new_v[col] = value
                self._tree.item(item, values=tuple(new_v))
                break

    def _append_log(self, message: str) -> None:
        import tkinter as tk
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        def do():
            self._log_text.insert(tk.END, line)
            self._log_text.see(tk.END)

        self.root.after(0, do)


# ============================================================
# 辅助函数
# ============================================================

def _action_cn(action: ActionType) -> str:
    return {
        ActionType.BUY: "📈 买入",
        ActionType.SELL: "📉 卖出",
        ActionType.HOLD: "⏸ 观望",
        ActionType.STOP_LOSS: "🚨 止损",
        ActionType.ERROR: "❌ 异常",
    }.get(action, str(action.name))


# ============================================================
# CLI 模式
# ============================================================

def run_cli_mode(analyze_only: bool = False) -> None:
    print("=" * 60)
    print("Binance 网格交易系统 v3.0 - CLI 模式")
    print("=" * 60)
    print(f"已注册策略: {registry.names()}")

    engine = TradingEngine()
    engine.on_log = lambda m: print(f"[{datetime.now():%H:%M:%S}] {m}")

    if analyze_only:
        for s in engine.data.get_coin_list():
            decision = engine.analyze_coin(s)
            if decision:
                print(f"  [{s}] {decision.action.name} | {decision.reason}")
            time.sleep(1)
    else:
        engine.on_signal = lambda s, d: print(
            f"  [{s}] {d.action.name} | {d.reason}"
        )
        engine.start()
        try:
            while engine.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            engine.stop()


# ============================================================
# 主入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Binance 网格交易系统 v3.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
    python run.py              # 启动 GUI
    python run.py --no-gui     # CLI 模式
    python run.py --analyze    # 仅分析
    python run.py BTCUSDT      # 快速分析
        """,
    )
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("coin", nargs="?", default=None)
    args = parser.parse_args()

    # 单币种快速分析
    if args.coin:
        engine = TradingEngine()
        engine.on_log = lambda m: print(f"[{datetime.now():%H:%M:%S}] {m}")
        d = engine.analyze_coin(args.coin.upper())
        if d:
            print(f"\n📊 {args.coin} 分析结果:")
            print(f"  信号: {d.action.name}")
            print(f"  理由: {d.reason}")
            print(f"  数量: {d.quantity}")
            print(f"  止损: {d.stop_loss}")
            print(f"  网格: {d.grid_buy} ~ {d.grid_sell} ({d.grid_interval_pct}%)")
        return

    if args.no_gui or args.analyze:
        run_cli_mode(analyze_only=args.analyze)
        return

    # ── GUI 模式 ──
    try:
        import tkinter as tk
    except ImportError:
        print("⚠️ Tkinter 不可用，切换到 CLI 模式")
        run_cli_mode()
        return

    engine = TradingEngine()
    root = tk.Tk()
    TradingMonitorGUI(root, engine)
    root.mainloop()


if __name__ == "__main__":
    main()
