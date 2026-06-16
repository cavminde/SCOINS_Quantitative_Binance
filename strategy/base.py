# -*- coding: utf-8 -*-
"""
策略标准样板模型
================

本文件定义**所有策略必须遵守的接口契约**，是策略开发的标准样板。

**为什么需要样板？**
-------------------
1. 不同的策略（WLD波动性、网格、套利等）必须使用统一接口，
   run.py 才能以同一套代码轮询调度；
2. 便于策略的独立测试、回测与热替换；
3. 降低新策略接入门槛——开发者只需要实现 ``decide()``。

**策略开发 4 步走**
------------------
1. 继承 ``BaseStrategy``；
2. 设置类属性 ``name``（唯一标识）和 ``description``；
3. 在 ``__init__`` 中定义可调参数（通过 ``self.params`` 访问）；
4. 实现 ``decide(context: MarketContext) -> StrategyDecision``。

**最小示例**
----------
::

    from strategy import BaseStrategy, StrategyDecision, ActionType

    class MyStrategy(BaseStrategy):
        name = "my_strategy"
        description = "示例策略：跌破均线买入"

        def __init__(self, symbol: str, params: dict | None = None):
            super().__init__(symbol, params)
            self.ma_period = int(self.params.get("ma_period", 20))

        def decide(self, context: MarketContext) -> StrategyDecision:
            close_prices = [k[4] for k in context.klines[-self.ma_period:]]
            if not close_prices:
                return self._hold_decision("数据不足")
            ma = sum(close_prices) / len(close_prices)

            if context.current_price < ma * 0.98:
                return StrategyDecision(
                    action=ActionType.BUY,
                    symbol=self.symbol,
                    price=context.current_price,
                    quantity=self.params.get("quantity", 1.0),
                    reason=f"价格 {context.current_price} < MA{self.ma_period}={ma:.4f}",
                    stop_loss=context.current_price * 0.95,
                )
            return self._hold_decision(f"价格 {context.current_price} 在 MA{self.ma_period}={ma:.4f} 附近")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


# ============================================================
# 枚举定义
# ============================================================

class ActionType(Enum):
    """策略决策动作类型"""
    BUY = auto()        # 买入
    SELL = auto()       # 卖出
    HOLD = auto()       # 持仓等待
    STOP_LOSS = auto()  # 触发止损
    ERROR = auto()      # 数据异常


class MarketTrend(Enum):
    """市场趋势方向"""
    UPTREND = auto()
    DOWNTREND = auto()
    SIDEWAYS = auto()
    UNKNOWN = auto()


# ============================================================
# 数据契约
# ============================================================

@dataclass
class MarketContext:
    """
    **市场上下文**：策略分析所需的全部输入数据

    由 ``run.py`` 在每次轮询时构造，传入 ``decide()``。
    策略不应当自行调用 API，全部数据均应来自 context。

    字段：
        symbol:         交易对（如 "BTCUSDT"）
        current_price:  当前市价
        klines:         K线列表，格式 [open_time, open, high, low, close, volume, ...]
        interval:       K线周期（如 "5m"、"1h"）
        step:           当前持仓步数（0=空仓）
        last_buy_price: 最近一次买入价格（0=无）
        position_qty:   当前持仓数量（0=空仓）
        balance:        账户可用余额（USDT）
        extra:          策略自定义扩展字段
    """
    symbol: str
    current_price: float
    klines: list = field(default_factory=list)
    interval: str = "5m"
    step: int = 0
    last_buy_price: float = 0.0
    position_qty: float = 0.0
    balance: float = 0.0
    extra: dict = field(default_factory=dict)

    def close_prices(self, n: int | None = None) -> list[float]:
        """返回最近 N 根 K 线的收盘价列表（K线索引 4）"""
        if not self.klines:
            return []
        arr = [float(k[4]) for k in self.klines]
        return arr[-n:] if n else arr


@dataclass
class OrderRecord:
    """
    **订单成交记录**：成交后由 run.py 回调给策略

    策略可基于历史订单动态调整参数（如：盈利后收紧止损）。
    """
    symbol: str
    action: ActionType
    price: float
    quantity: float
    timestamp: float
    profit: float = 0.0
    order_id: str = ""


@dataclass
class StrategyDecision:
    """
    **策略决策输出**：策略 decide() 必须返回的标准化结果

    字段：
        action:       动作类型（BUY/SELL/HOLD/STOP_LOSS）
        symbol:       交易对
        price:        期望成交价格（市价单时 = current_price）
        quantity:     期望数量（0 表示由 run.py 决定）
        reason:       决策理由（写入日志 / GUI 展示）
        confidence:   置信度 0~1（仅供 GUI 颜色参考）
        stop_loss:    建议止损价（可选）
        take_profit:  建议止盈价（可选）
        grid_buy:     网格策略专用：下一档买入触发价（GUI 展示用）
        grid_sell:    网格策略专用：下一档卖出触发价（GUI 展示用）
        grid_interval_pct: 网格区间百分比（GUI 展示用）
        metadata:     任意附加元数据
    """
    action: ActionType
    symbol: str
    price: float = 0.0
    quantity: float = 0.0
    reason: str = ""
    confidence: float = 0.5
    stop_loss: float = 0.0
    take_profit: float = 0.0
    grid_buy: float = 0.0
    grid_sell: float = 0.0
    grid_interval_pct: float = 0.0
    metadata: dict = field(default_factory=dict)


# ============================================================
# 策略注册表
# ============================================================

class StrategyRegistry:
    """
    **策略注册表**：以字符串名映射到策略类

    使用方式：
        >>> from strategy import BaseStrategy, registry
        >>> class MyStrat(BaseStrategy):
        ...     name = "my_strat"
        >>> registry.register(MyStrat)
        >>> cls = registry.get("my_strat")
        >>> instance = cls("BTCUSDT", {})
    """
    def __init__(self) -> None:
        self._strategies: dict[str, type[BaseStrategy]] = {}

    def register(self, strategy_cls: type[BaseStrategy]) -> type[BaseStrategy]:
        """注册策略类（可作为装饰器）"""
        if not strategy_cls.name:
            raise ValueError(f"策略类 {strategy_cls.__name__} 必须定义 name 类属性")
        if strategy_cls.name in self._strategies:
            raise ValueError(f"策略名称 {strategy_cls.name!r} 已被注册")
        self._strategies[strategy_cls.name] = strategy_cls
        return strategy_cls

    def get(self, name: str) -> type[BaseStrategy]:
        """按名称获取策略类"""
        if name not in self._strategies:
            raise KeyError(f"未注册的策略: {name!r}，可用: {list(self._strategies.keys())}")
        return self._strategies[name]

    def all(self) -> dict[str, type[BaseStrategy]]:
        """获取全部已注册策略（名称 → 类）"""
        return dict(self._strategies)

    def names(self) -> list[str]:
        """获取全部策略名称"""
        return list(self._strategies.keys())


# 全局注册表实例
registry = StrategyRegistry()


# ============================================================
# 抽象基类（标准样板）
# ============================================================

class BaseStrategy(ABC):
    """
    **策略抽象基类** — 所有自定义策略必须继承此类

    子类必须实现：
        - decide(context): 核心决策方法

    子类可选重写：
        - on_init():        初始化时调用（获取历史数据等）
        - on_order_filled(): 订单成交回调
        - update_params():  热更新参数
    """
    # ── 子类必须设置 ──
    name: str = ""               # 唯一标识（用于注册表 / data.json）
    description: str = ""        # 人类可读描述

    def __init__(self, symbol: str, params: dict | None = None) -> None:
        """
        初始化策略

        :param symbol: 交易对（如 "BTCUSDT"）
        :param params: 参数字典（如 {"profit_ratio": 5.0, "quantity": [9.1]}）
        """
        if not self.name:
            raise ValueError(f"{type(self).__name__} 必须定义 name 类属性")
        self.symbol: str = symbol
        self.params: dict[str, Any] = dict(params or {})

    # ----------------------------------------------------------
    # 生命周期方法（子类按需重写）
    # ----------------------------------------------------------

    def on_init(self) -> None:
        """
        **初始化回调**：策略实例创建后调用一次

        可用于：拉取长周期历史数据、预计算指标、初始化状态。
        抛异常不会影响策略注册，但会写入日志。
        """

    def on_order_filled(self, order: OrderRecord) -> None:
        """
        **订单成交回调**：每次订单成交后由 run.py 调用

        可用于：更新最近买入价、累计盈亏、移动止损等。
        """

    def update_params(self, **kwargs: Any) -> None:
        """热更新策略参数（运行中调整）"""
        self.params.update(kwargs)

    # ----------------------------------------------------------
    # 核心决策方法（子类必须实现）
    # ----------------------------------------------------------

    @abstractmethod
    def decide(self, context: MarketContext) -> StrategyDecision:
        """
        **核心决策**：根据市场上下文返回交易动作

        调用时机：run.py 每轮询一次（建议 1~5 秒）调用一次。

        :param context: MarketContext，包含价格、K线、持仓等
        :return:        StrategyDecision，描述应当执行的动作
        """
        raise NotImplementedError

    # ----------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------

    def _hold_decision(self, reason: str = "持仓等待") -> StrategyDecision:
        """便捷方法：构造 HOLD 决策"""
        return StrategyDecision(
            action=ActionType.HOLD,
            symbol=self.symbol,
            reason=reason,
        )

    def _error_decision(self, reason: str) -> StrategyDecision:
        """便捷方法：构造 ERROR 决策"""
        return StrategyDecision(
            action=ActionType.ERROR,
            symbol=self.symbol,
            reason=reason,
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__} symbol={self.symbol} params={self.params}>"
