# -*- coding: utf-8 -*-
"""
WLD/USDT 波动性双模策略（标准接口版）
====================================

策略原理：
    针对 4h 波动性极强（ATR ≈ 5% of price）的币种，融合两种模式：

    模式一：T 交易（短线均值回归）
        - 单根 4h 涨跌幅绝对值 > 阈值 时触发
        - 配合 RSI、布林带、EMA 偏离
        - 跌幅>3% + RSI 超卖 + 接近布林下轨 → 抄底买入
        - 涨幅>3% + RSI 超买 + 接近布林上轨 → 减仓卖出

    模式二：追高（趋势跟随）
        - EMA 多头排列 + RSI 强势 + MACD 扩张 + 放量
        - 确认上升趋势后追入

策略参数（params 字典）：
    - timeframe:            K线周期，默认 "4h"
    - lookback:             K线根数，默认 100
    - ema_short:            短期EMA周期，默认 12
    - ema_long:             长期EMA周期，默认 26
    - sma_trend:            趋势SMA周期，默认 20
    - t_threshold:          T交易涨跌幅阈值（0.05 = 5%）
    - t_rsi_oversold:       T买入RSI阈值，默认 35
    - t_rsi_overbought:     T卖出RSI阈值，默认 65
    - t_boll_touch:         是否要求触及布林带
    - t_cooldown_bars:      T交易冷却K线数
    - chase_rsi_min/max:    追高RSI区间，默认 55~78
    - chase_vol_spike:      放量倍数，默认 1.2
    - stop_loss_atr_mult:   止损 ATR 倍数，默认 1.5
    - take_profit_atr_mult: 止盈 ATR 倍数，默认 2.5
    - trailing_stop_atr:    移动止损 ATR 倍数，默认 2.0
    - quantity:             买入数量，默认 9.1
    - min_volume_ratio:     最低成交量过滤，默认 0.6
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from strategy.base import (
    ActionType,
    BaseStrategy,
    MarketContext,
    StrategyDecision,
)
from strategy.registry import registry

logger = logging.getLogger(__name__)


# ============================================================
# 策略参数定义
# ============================================================

@dataclass
class WLDConfig:
    """WLD 策略可调参数（默认与原 wld_strategy.py 一致）"""
    timeframe: str = "4h"
    lookback: int = 100

    # 均线
    ema_short: int = 12
    ema_long: int = 26
    sma_trend: int = 20

    # T 交易
    t_threshold: float = 0.05
    t_rsi_oversold: float = 35.0
    t_rsi_overbought: float = 65.0
    t_boll_touch: bool = True
    t_cooldown_bars: int = 3

    # 追高
    chase_ema_cross: bool = True
    chase_rsi_min: float = 55.0
    chase_rsi_max: float = 78.0
    chase_macd_expand: bool = True
    chase_vol_spike: float = 1.2
    chase_price_above_sma: bool = True

    # 风险
    stop_loss_atr_mult: float = 1.5
    take_profit_atr_mult: float = 2.5
    trailing_stop_atr: float = 2.0

    # 过滤
    min_volume_ratio: float = 0.6


# ============================================================
# WLD 策略实现
# ============================================================

@registry.register
class WLDStrategy(BaseStrategy):
    """
    WLD/USDT 双模波动性策略

    适配标准 BaseStrategy 接口，专注于实盘信号输出（不含回测/绘图）。
    """
    name = "wld"
    description = "WLD 双模策略：T 交易（均值回归） + 追高（趋势跟随）"

    def __init__(self, symbol: str, params: dict | None = None) -> None:
        super().__init__(symbol, params)
        # ── 解析参数（仅保留 WLDConfig 支持的字段）──
        valid_fields = {f for f in WLDConfig.__dataclass_fields__}
        cfg_kwargs = {k: v for k, v in self.params.items() if k in valid_fields}
        self.cfg = WLDConfig(**cfg_kwargs)
        self.quantity: float = float(self.params.get("quantity", 9.1))

        # ── 内部状态 ──
        self._last_t_buy_bar: int = -999
        self._last_t_sell_bar: int = -999
        self._bar_index: int = 0
        self._highest_since_entry: float = 0.0
        self._entry_price: float = 0.0
        self._position_hold_bars: int = 0
        self._latest_atr: float = 0.0
        self._latest_signal_type: str = ""
        self._latest_signal_strength: float = 0.0

    # ----------------------------------------------------------
    # 决策主入口
    # ----------------------------------------------------------

    def decide(self, context: MarketContext) -> StrategyDecision:
        """
        根据最新 K线（含历史）输出交易决策

        :param context: 市场上下文（klines 应至少 30 根 4h K线）
        :return:        标准化决策
        """
        self._bar_index += 1

        if not context.klines or len(context.klines) < 30:
            return self._hold_decision("K线数据不足（< 30 根）")

        # ── 计算指标 ──
        indicators = self._calc_all_indicators(context.klines)
        self._latest_atr = indicators["atr"]
        current = context.current_price
        step = context.step
        last_buy = context.last_buy_price

        # ── 持仓状态止损/止盈检查 ──
        if step > 0 and last_buy > 0 and self._latest_atr > 0:
            self._position_hold_bars += 1
            self._highest_since_entry = max(self._highest_since_entry, current)

            # 硬止损
            stop_price = last_buy - self.cfg.stop_loss_atr_mult * self._latest_atr
            if current <= stop_price:
                return StrategyDecision(
                    action=ActionType.STOP_LOSS,
                    symbol=self.symbol,
                    price=current,
                    quantity=context.position_qty,
                    reason=(
                        f"硬止损触发：当前 {current:.4f} ≤ "
                        f"止损价 {stop_price:.4f}（ATR={self._latest_atr:.4f}）"
                    ),
                    confidence=0.95,
                    stop_loss=stop_price,
                )

            # 止盈目标
            tp_price = last_buy + self.cfg.take_profit_atr_mult * self._latest_atr
            if current >= tp_price:
                return StrategyDecision(
                    action=ActionType.SELL,
                    symbol=self.symbol,
                    price=current,
                    quantity=context.position_qty,
                    reason=(
                        f"止盈触发：当前 {current:.4f} ≥ "
                        f"止盈价 {tp_price:.4f}（ATR={self._latest_atr:.4f}）"
                    ),
                    confidence=0.85,
                    take_profit=tp_price,
                )

            # 移动止损（盈利>3% 后启动）
            if self._highest_since_entry > last_buy * 1.03:
                trail_stop = self._highest_since_entry - self.cfg.trailing_stop_atr * self._latest_atr
                if current <= trail_stop:
                    return StrategyDecision(
                        action=ActionType.SELL,
                        symbol=self.symbol,
                        price=current,
                        quantity=context.position_qty,
                        reason=(
                            f"移动止损：当前 {current:.4f} ≤ 移动止损 {trail_stop:.4f}"
                            f"（最高 {self._highest_since_entry:.4f}）"
                        ),
                        confidence=0.85,
                        stop_loss=trail_stop,
                    )

        # ── 空仓时生成开仓信号 ──
        if step == 0:
            signal_type, score = self._evaluate_entry(indicators)
            if signal_type:
                # 计算止损止盈
                atr = self._latest_atr if self._latest_atr > 0 else current * 0.05
                stop_loss = current - self.cfg.stop_loss_atr_mult * atr
                take_profit = current + self.cfg.take_profit_atr_mult * atr

                self._latest_signal_type = signal_type
                self._latest_signal_strength = score

                return StrategyDecision(
                    action=ActionType.BUY,
                    symbol=self.symbol,
                    price=current,
                    quantity=self.quantity,
                    reason=(
                        f"WLD {signal_type} 信号（强度 {score:.2f}）"
                        f"RSI={indicators['rsi']:.1f}, "
                        f"ATR%={indicators['atr_pct']*100:.2f}%, "
                        f"BB位置={indicators['bb_position']:.2f}"
                    ),
                    confidence=min(0.5 + score / 2, 0.95),
                    stop_loss=round(stop_loss, 6),
                    take_profit=round(take_profit, 6),
                )

        # ── 持仓时生成 T 卖出信号 ──
        if step > 0:
            t_sell_signal, t_sell_score = self._evaluate_t_sell(indicators)
            if t_sell_signal:
                return StrategyDecision(
                    action=ActionType.SELL,
                    symbol=self.symbol,
                    price=current,
                    quantity=context.position_qty,
                    reason=(
                        f"WLD T卖出信号（强度 {t_sell_score:.2f}）："
                        f"涨幅 {indicators['pct_change']*100:.2f}%, "
                        f"RSI={indicators['rsi']:.1f}"
                    ),
                    confidence=min(0.5 + t_sell_score / 2, 0.9),
                )

        # ── 持仓观望 ──
        return self._hold_decision(
            f"无触发信号 | RSI={indicators['rsi']:.1f}, "
            f"ATR%={indicators['atr_pct']*100:.2f}%, "
            f"BB位置={indicators['bb_position']:.2f}"
        )

    # ----------------------------------------------------------
    # 订单成交回调
    # ----------------------------------------------------------

    def on_order_filled(self, order) -> None:
        """订单成交后更新内部状态（用于移动止损跟踪）"""
        if order.action == ActionType.BUY:
            self._entry_price = order.price
            self._highest_since_entry = order.price
            self._position_hold_bars = 0
        elif order.action in (ActionType.SELL, ActionType.STOP_LOSS):
            self._entry_price = 0.0
            self._highest_since_entry = 0.0
            self._position_hold_bars = 0

    # ----------------------------------------------------------
    # 信号评估
    # ----------------------------------------------------------

    def _evaluate_entry(self, ind: dict) -> tuple[str, float]:
        """
        评估开仓信号（T 买入 / 追高）

        :return: (signal_type, score) 或 ("", 0.0)
        """
        # ── 成交量过滤 ──
        if ind["volume_ratio"] < self.cfg.min_volume_ratio:
            return "", 0.0

        # ── 模式一：T 买入（抄底）──
        drop_pct = abs(ind["pct_change"]) if ind["pct_change"] < 0 else 0.0
        cooldown_ok = (self._bar_index - self._last_t_buy_bar) > self.cfg.t_cooldown_bars

        t_score = 0.0
        if drop_pct >= self.cfg.t_threshold:
            t_score += 0.35 * min(drop_pct / 0.10, 1.0)

        rsi_oversold = ind["rsi"] < self.cfg.t_rsi_oversold
        near_bb_low = ind["bb_position"] < 0.25 if self.cfg.t_boll_touch else True
        below_ema = ind["deviation_ema_short"] < -0.02

        if rsi_oversold:
            t_score += 0.25
        if near_bb_low:
            t_score += 0.20
        if below_ema:
            t_score += 0.10

        t_buy_trigger = (
            drop_pct > 0 and cooldown_ok and (rsi_oversold or near_bb_low)
        )
        if t_buy_trigger:
            self._last_t_buy_bar = self._bar_index
            return "T_BUY", t_score

        # ── 模式二：追高 ──
        chase_score = 0.0
        ema_bullish = ind["ema_short"] > ind["ema_long"] if self.cfg.chase_ema_cross else True
        if ema_bullish:
            chase_score += 0.20
        else:
            chase_score -= 0.30

        if ind["ema_spread_pct"] > 0:
            chase_score += 0.08

        if self.cfg.chase_rsi_min <= ind["rsi"] <= self.cfg.chase_rsi_max:
            chase_score += 0.18
        elif ind["rsi"] > self.cfg.chase_rsi_max:
            chase_score -= 0.15

        if ind["macd_hist"] > 0:
            chase_score += 0.15 if ind["macd_hist_delta"] > 0 else 0.06

        if ind["volume_ratio"] >= self.cfg.chase_vol_spike:
            chase_score += 0.15

        if self.cfg.chase_price_above_sma and ind["deviation_sma"] > 0:
            chase_score += 0.10

        if ema_bullish and chase_score >= 0.45 and (self._bar_index - self._last_t_sell_bar) > 2:
            return "CHASE_BUY", chase_score

        return "", 0.0

    def _evaluate_t_sell(self, ind: dict) -> tuple[bool, float]:
        """评估 T 卖出信号（仅在持仓状态下调用）"""
        surge_pct = ind["pct_change"] if ind["pct_change"] > 0 else 0.0
        cooldown_ok = (self._bar_index - self._last_t_sell_bar) > self.cfg.t_cooldown_bars
        if surge_pct < self.cfg.t_threshold or not cooldown_ok:
            return False, 0.0

        rsi_overbought = ind["rsi"] > self.cfg.t_rsi_overbought
        near_bb_high = ind["bb_position"] > 0.75 if self.cfg.t_boll_touch else True
        above_ema = ind["deviation_ema_short"] > 0.03

        if rsi_overbought or near_bb_high:
            self._last_t_sell_bar = self._bar_index
            score = 0.35 * min(surge_pct / 0.10, 1.0)
            if rsi_overbought:
                score += 0.25
            if near_bb_high:
                score += 0.20
            if above_ema:
                score += 0.10
            return True, score
        return False, 0.0

    # ----------------------------------------------------------
    # 指标计算（纯 K线数据，无外部依赖）
    # ----------------------------------------------------------

    def _calc_all_indicators(self, klines: list) -> dict[str, Any]:
        """
        计算策略所需的全部指标

        K线格式（Binance 标准）：[open_time, open, high, low, close, volume, ...]
        """
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        vols = [float(k[5]) for k in klines]
        n = len(closes)
        cur = closes[-1]
        prev = closes[-2] if n >= 2 else cur

        # ── 均线 ──
        ema_s = self._ema(closes, self.cfg.ema_short)
        ema_l = self._ema(closes, self.cfg.ema_long)
        sma_trend = self._sma(closes, self.cfg.sma_trend)

        # ── RSI ──
        rsi = self._rsi(closes, 14)

        # ── MACD ──
        macd = self._macd(closes, 12, 26, 9)
        macd_hist = macd["histogram"]
        macd_hist_delta = macd_hist - macd["prev_histogram"]

        # ── 布林带 ──
        bb = self._boll(closes, 20, 2.0)
        bb_width = (bb["upper"] - bb["lower"]) / bb["middle"] if bb["middle"] > 0 else 0
        bb_position = (
            (cur - bb["lower"]) / (bb["upper"] - bb["lower"])
            if bb["upper"] > bb["lower"] else 0.5
        )

        # ── ATR ──
        atr = self._atr_pct(klines, 14)

        # ── 派生指标 ──
        return {
            "rsi": rsi,
            "ema_short": ema_s,
            "ema_long": ema_l,
            "sma_trend": sma_trend,
            "macd_hist": macd_hist,
            "macd_hist_delta": macd_hist_delta,
            "bb_upper": bb["upper"],
            "bb_lower": bb["lower"],
            "bb_width": bb_width,
            "bb_position": bb_position,
            "atr": atr,
            "atr_pct": (atr / cur) if cur > 0 else 0.0,
            "volume_ratio": vols[-1] / (sum(vols[-20:]) / 20) if len(vols) >= 20 else 1.0,
            "pct_change": (cur - prev) / prev if prev > 0 else 0.0,
            "deviation_ema_short": (cur - ema_s) / ema_s if ema_s > 0 else 0.0,
            "deviation_ema_long": (cur - ema_l) / ema_l if ema_l > 0 else 0.0,
            "deviation_sma": (cur - sma_trend) / sma_trend if sma_trend > 0 else 0.0,
            "ema_spread_pct": (ema_s - ema_l) / ema_l if ema_l > 0 else 0.0,
        }

    @staticmethod
    def _sma(arr: list[float], period: int) -> float:
        if len(arr) < period:
            period = len(arr)
        return round(sum(arr[-period:]) / period, 6) if period > 0 else 0.0

    @staticmethod
    def _ema(arr: list[float], period: int) -> float:
        if len(arr) < period:
            return 0.0
        alpha = 2.0 / (period + 1)
        ema = sum(arr[:period]) / period
        for v in arr[period:]:
            ema = alpha * v + (1 - alpha) * ema
        return round(ema, 6)

    @staticmethod
    def _rsi(arr: list[float], period: int = 14) -> float:
        if len(arr) < period + 1:
            return 50.0
        gains = losses = 0.0
        for i in range(-period, 0):
            diff = arr[i] - arr[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_g = gains / period
        avg_l = losses / period
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return round(100.0 - 100.0 / (1.0 + rs), 2)

    @staticmethod
    def _macd(arr: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        if len(arr) < slow + signal:
            return {"histogram": 0.0, "prev_histogram": 0.0}

        def ema_series(data, n):
            if len(data) < n:
                return []
            a = 2.0 / (n + 1)
            out = [sum(data[:n]) / n]
            for v in data[n:]:
                out.append(a * v + (1 - a) * out[-1])
            return out

        ef = ema_series(arr, fast)
        es = ema_series(arr, slow)
        offset = len(es) - len(ef)
        dif_series = [ef[i] - es[i + offset] for i in range(len(ef))]
        dea_series = ema_series(dif_series, signal)
        if not dea_series:
            return {"histogram": 0.0, "prev_histogram": 0.0}
        dif = dif_series[-1]
        dea = dea_series[-1]
        hist = round((dif - dea) * 2, 6)
        prev_hist = round((dif_series[-2] - dea_series[-2]) * 2, 6) if len(dea_series) >= 2 else hist
        return {"histogram": hist, "prev_histogram": prev_hist}

    @staticmethod
    def _boll(arr: list[float], period: int = 20, std_mult: float = 2.0) -> dict:
        if len(arr) < period:
            return {"upper": 0.0, "lower": 0.0, "middle": 0.0}
        window = arr[-period:]
        mid = sum(window) / period
        var = sum((c - mid) ** 2 for c in window) / period
        std = var ** 0.5
        return {
            "upper": round(mid + std_mult * std, 6),
            "lower": round(mid - std_mult * std, 6),
            "middle": round(mid, 6),
        }

    @staticmethod
    def _atr_pct(klines: list, period: int = 14) -> float:
        """返回绝对 ATR（不是百分比）"""
        if len(klines) < period + 1:
            return 0.0
        trs = []
        for i in range(-period, 0):
            h = float(klines[i][2])
            l = float(klines[i][3])
            pc = float(klines[i - 1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return round(sum(trs) / len(trs), 6) if trs else 0.0

