# -*- coding: utf-8 -*-
"""
经典网格策略（标准接口版）
==========================

基于 ``BaseStrategy`` 实现的网格交易策略。

策略参数（params 字典）：
    - profit_ratio:        止盈比率 %（默认 5.0）
    - double_throw_ratio:  补仓比率 %（默认 5.0）
    - stop_loss_ratio:     止损比率 %（默认 6.0）
    - quantity:            单次买入数量（默认 9.1）
    - grid_levels:         网格层数（默认 4）
    - atr_period:          ATR 周期（默认 20）
    - atr_multiplier:      ATR 调整倍数（默认 2.0）
    - ma_period:           均线周期（默认 20）
    - enable_trend_filter: 是否启用趋势过滤（默认 True）

信号逻辑：
    1. 空仓：买入价 = current × (1 - double_throw_ratio/100)
    2. 持仓：买入价 = last_buy × (1 - double_throw_ratio/100)
    3. 卖出价 = (当前/上次买入价) × (1 + profit_ratio/100)
    4. 止损价 = 上次买入价 × (1 - stop_loss_ratio/100)
"""

from __future__ import annotations

import logging
from typing import Any

from strategy.base import (
    ActionType,
    BaseStrategy,
    MarketContext,
    MarketTrend,
    StrategyDecision,
)
from strategy.registry import registry

logger = logging.getLogger(__name__)


@registry.register
class GridStrategy(BaseStrategy):
    """
    经典网格策略 — 按比率计算买卖点
    """
    name = "grid"
    description = "经典网格策略：按固定止盈/补仓比率分批买卖"

    def __init__(self, symbol: str, params: dict | None = None) -> None:
        super().__init__(symbol, params)
        # ── 参数解析（带默认值）──
        self.profit_ratio: float = float(self.params.get("profit_ratio", 5.0))
        self.double_throw_ratio: float = float(self.params.get("double_throw_ratio", 5.0))
        self.stop_loss_ratio: float = float(self.params.get("stop_loss_ratio", 6.0))
        self.quantity: float = float(self.params.get("quantity", 9.1))
        self.grid_levels: int = int(self.params.get("grid_levels", 4))
        self.atr_period: int = int(self.params.get("atr_period", 20))
        self.atr_multiplier: float = float(self.params.get("atr_multiplier", 2.0))
        self.ma_period: int = int(self.params.get("ma_period", 20))
        self.enable_trend_filter: bool = bool(self.params.get("enable_trend_filter", True))

        # ── 内部状态 ──
        self._atr_cache: float = 0.0
        self._ma_cache: float = 0.0
        self._trend_cache: MarketTrend = MarketTrend.UNKNOWN

    # ----------------------------------------------------------
    # 核心决策
    # ----------------------------------------------------------

    def decide(self, context: MarketContext) -> StrategyDecision:
        """
        计算当前应当执行的动作

        :param context: 市场上下文
        :return:        标准化决策
        """
        current = context.current_price
        step = context.step
        last_buy = context.last_buy_price

        # ── 计算技术指标 ──
        if context.klines:
            try:
                self._ma_cache = self._calc_ma(context.klines, self.ma_period)
                self._atr_cache = self._calc_atr(context.klines, self.atr_period)
                self._trend_cache = self._judge_trend(context.klines, current)
            except Exception as exc:
                logger.warning("[%s] 指标计算失败: %s", self.symbol, exc)
                self._atr_cache = 0.0

        # ── 1. 止损检查（仅在有持仓时）──
        if step > 0 and last_buy > 0:
            stop_loss_price = round(last_buy * (1 - self.stop_loss_ratio / 100), 6)
            if current <= stop_loss_price:
                return StrategyDecision(
                    action=ActionType.STOP_LOSS,
                    symbol=self.symbol,
                    price=current,
                    quantity=context.position_qty,
                    reason=(
                        f"触发止损！当前价 {current} ≤ 止损价 {stop_loss_price}"
                        f"（买入价 {last_buy}，止损比率 {self.stop_loss_ratio}%）"
                    ),
                    confidence=0.95,
                    stop_loss=stop_loss_price,
                )

        # ── 2. 计算网格买卖价 ──
        if step == 0:
            base = current
        else:
            base = last_buy if last_buy > 0 else current

        buy_price = round(base * (1 - self.double_throw_ratio / 100), 6)
        sell_price = round(base * (1 + self.profit_ratio / 100), 6)

        # ── 3. 决策：买入 ──
        if current <= buy_price:
            if self.enable_trend_filter and self._trend_cache == MarketTrend.DOWNTREND:
                return self._hold_decision(
                    f"价格满足买入条件（{current} ≤ {buy_price}），"
                    f"但市场处于下跌趋势，暂不买入"
                )
            confidence = 0.8 if self._trend_cache == MarketTrend.UPTREND else 0.6
            grid_pct = round((sell_price - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0.0
            return StrategyDecision(
                action=ActionType.BUY,
                symbol=self.symbol,
                price=current,
                quantity=self.quantity,
                reason=(
                    f"买入信号：当前 {current} ≤ 买入价 {buy_price}"
                    + ("（首仓）" if step == 0 else f"（第{step + 1}次补仓）")
                ),
                confidence=confidence,
                stop_loss=round(current * (1 - self.stop_loss_ratio / 100), 6),
                take_profit=sell_price,
                grid_buy=buy_price,
                grid_sell=sell_price,
                grid_interval_pct=grid_pct,
            )

        # ── 4. 决策：卖出 ──
        if current >= sell_price:
            if step == 0:
                # 空仓但价格突破卖出价 → 防踏空：上移网格
                new_buy = round(current * (1 - self.double_throw_ratio / 100), 6)
                new_sell = round(current * (1 + self.profit_ratio / 100), 6)
                return StrategyDecision(
                    action=ActionType.HOLD,
                    symbol=self.symbol,
                    price=current,
                    reason=(
                        f"空仓状态下价格 {current} 突破卖出价 {sell_price}，"
                        f"上移网格至 {new_buy}~{new_sell} 防踏空"
                    ),
                    confidence=0.4,
                    grid_buy=new_buy,
                    grid_sell=new_sell,
                    grid_interval_pct=round((new_sell - new_buy) / new_buy * 100, 2),
                )
            profit_pct = round((current - last_buy) / last_buy * 100, 2) if last_buy > 0 else 0
            return StrategyDecision(
                action=ActionType.SELL,
                symbol=self.symbol,
                price=current,
                quantity=context.position_qty,
                reason=(
                    f"卖出信号：当前 {current} ≥ 卖出价 {sell_price}，"
                    f"预计盈利 {profit_pct}%"
                ),
                confidence=0.8,
                grid_buy=buy_price,
                grid_sell=sell_price,
                grid_interval_pct=round((sell_price - buy_price) / buy_price * 100, 2),
            )

        # ── 5. 持仓观望 ──
        distance_to_buy = round((current - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0
        distance_to_sell = round((sell_price - current) / current * 100, 2) if current > 0 else 0
        return StrategyDecision(
            action=ActionType.HOLD,
            symbol=self.symbol,
            price=current,
            reason=(
                f"价格 {current} 在网格区间内，"
                f"距买入 {distance_to_buy}%，距卖出 {distance_to_sell}%，持仓等待"
            ),
            confidence=0.5,
            grid_buy=buy_price,
            grid_sell=sell_price,
            grid_interval_pct=round((sell_price - buy_price) / buy_price * 100, 2),
        )

    # ----------------------------------------------------------
    # 内部工具方法
    # ----------------------------------------------------------

    @staticmethod
    def _calc_ma(klines: list, period: int) -> float:
        """计算简单移动平均线（基于收盘价，索引 4）"""
        if not klines or len(klines) < period:
            period = len(klines)
        if period < 1:
            return 0.0
        closes = [float(k[4]) for k in klines[-period:]]
        return round(sum(closes) / len(closes), 6)

    @staticmethod
    def _calc_atr(klines: list, period: int) -> float:
        """计算 ATR（百分比），返回最近 period 根的平均真实波幅占收盘价比例"""
        if not klines or len(klines) < period + 1:
            return 0.0
        true_ranges: list[float] = []
        for i in range(-period, 0):
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i - 1][4])
            close = float(klines[i][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            if close > 0:
                true_ranges.append((tr / close) * 100)
        return round(sum(true_ranges) / len(true_ranges), 2) if true_ranges else 0.0

    @staticmethod
    def _judge_trend(klines: list, current_price: float) -> MarketTrend:
        """判断趋势：MA5 与 MA20 关系 + 价格位置"""
        if len(klines) < 25:
            return MarketTrend.UNKNOWN
        ma5 = GridStrategy._calc_ma(klines, 5)
        ma20 = GridStrategy._calc_ma(klines, 20)
        if ma5 > ma20 and current_price > ma20:
            return MarketTrend.UPTREND
        if ma5 < ma20 and current_price < ma20:
            return MarketTrend.DOWNTREND
        return MarketTrend.SIDEWAYS
