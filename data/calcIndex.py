# -*- coding: utf-8 -*-
"""
技术指标计算模块
================
提供网格交易所需的各项技术指标计算，包括：

    - MA（移动平均线）
    - EMA（指数移动平均线）
    - ATR（平均真实波幅）
    - RSI（相对强弱指数）
    - MACD（指数平滑异同移动平均线）
    - 布林带（Bollinger Bands）
    - 趋势斜率计算
    - 乖离率计算

所有指标基于 Binance K 线数据格式：
    [开盘时间, 开盘价, 最高价, 最低价, 收盘价, 成交量, 收盘时间,
     成交额, 成交笔数, 主动买入成交量, 主动买入成交额, 忽略]

索引常量:
    K_OPEN_TIME = 0    # 开盘时间
    K_OPEN = 1         # 开盘价
    K_HIGH = 2         # 最高价
    K_LOW = 3          # 最低价
    K_CLOSE = 4        # 收盘价
    K_VOLUME = 5       # 成交量
"""

from __future__ import annotations

import logging
from typing import Callable

from app.BinanceAPI import BinanceAPI
from app.authorization import api_key, api_secret

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger(__name__)

# ============================================================
# K 线数据索引常量（增强可读性）
# ============================================================
K_OPEN_TIME = 0
K_OPEN = 1
K_HIGH = 2
K_LOW = 3
K_CLOSE = 4
K_VOLUME = 5
K_CLOSE_TIME = 6
K_QUOTE_VOLUME = 7
K_TRADES = 8
K_TAKER_BUY_VOLUME = 9
K_TAKER_BUY_QUOTE = 10


class CalcIndex:
    """
    技术指标计算器

    封装常用的技术指标计算逻辑，支持：
        - 移动平均线（MA / EMA）
        - 波动率指标（ATR）
        - 动量指标（RSI / MACD）
        - 趋势判断（斜率、布林带）
        - 乖离率

    所有方法均为静态方法或独立方法，
    可以在不实例化的情况下直接调用进行批量计算。

    使用示例：
        >>> calc = CalcIndex()
        >>> klines = api.get_klines("BTCUSDT", "1h", 100)
        >>> ma20 = calc.ma(klines, 20)
        >>> print(f"MA20: {ma20}")
        >>> rsi = calc.rsi(klines, 14)
        >>> print(f"RSI(14): {rsi}")
    """

    def __init__(self):
        """初始化技术指标计算器"""
        self._api: BinanceAPI | None = None

    def _get_api(self) -> BinanceAPI:
        """懒加载 API 实例（仅在需要获取实时数据时创建）"""
        if self._api is None:
            self._api = BinanceAPI(api_key, api_secret)
        return self._api

    # ============================================================
    # 移动平均线（MA）
    # ============================================================

    @staticmethod
    def ma(klines: list[list], period: int) -> float:
        """
        计算**简单移动平均线（SMA）**

        公式：SMA = SUM(收盘价, N) / N

        :param klines: K 线数据（至少 period 根）
        :param period: 计算周期（如 5/10/20/60）
        :return:       MA 值
        """
        if not klines or len(klines) < period:
            return 0.0
        closes = [float(k[K_CLOSE]) for k in klines[-period:]]
        return round(sum(closes) / len(closes), 6)

    @staticmethod
    def ema(klines: list[list], period: int) -> float:
        """
        计算**指数移动平均线（EMA）**

        公式：EMA(t) = α × Price(t) + (1 - α) × EMA(t-1)
        其中 α = 2 / (N + 1)

        EMA 相比 SMA 更侧重近期价格，反应更快。

        :param klines: K 线数据
        :param period: 计算周期
        :return:       EMA 值
        """
        if not klines or len(klines) < period:
            return 0.0

        alpha = 2.0 / (period + 1.0)
        closes = [float(k[K_CLOSE]) for k in klines]

        # 初始 EMA = 前 N 个收盘价的 SMA
        ema_val = sum(closes[:period]) / period

        # 逐根 K 线递推计算
        for close in closes[period:]:
            ema_val = alpha * close + (1 - alpha) * ema_val

        return round(ema_val, 6)

    @staticmethod
    def ma_slope(
        klines: list[list], period: int, interval: str = "5m"
    ) -> tuple[float, float]:
        """
        计算 **MA 斜率**（用于判断趋势方向变化）

        比较前一根 MA 和当前 MA 的值，判断 MA 是上升还是下降。

        :param klines:   K 线数据（至少 period + 1 根）
        :param period:   计算周期
        :param interval: K 线间隔（用于日志）
        :return:         (前一根 MA 值, 当前 MA 值)
        """
        if not klines or len(klines) < period + 1:
            return 0.0, 0.0

        prev_ma = CalcIndex.ma(klines[:-1], period)
        curr_ma = CalcIndex.ma(klines, period)

        return round(prev_ma, 6), round(curr_ma, 6)

    # ============================================================
    # 平均真实波幅（ATR）
    # ============================================================

    @staticmethod
    def atr(klines: list[list], period: int = 14, as_percentage: bool = True) -> float:
        """
        计算 **ATR（Average True Range，平均真实波幅）**

        True Range = max(
            最高价 - 最低价,
            |最高价 - 昨收|,
            |最低价 - 昨收|
        )

        ATR 反映市场波动剧烈程度：
            - ATR 大 → 波动剧烈 → 网格间距应加宽
            - ATR 小 → 波动平缓 → 网格间距应收窄

        :param klines:        K 线数据（至少 period + 1 根）
        :param period:        计算周期（默认 14）
        :param as_percentage: True = 返回百分比 ATR（除以收盘价）
                              False = 返回绝对 ATR
        :return:              ATR 值
        """
        if not klines or len(klines) < period + 1:
            return 0.0

        true_ranges: list[float] = []
        for i in range(-period, 0):
            high = float(klines[i][K_HIGH])
            low = float(klines[i][K_LOW])
            prev_close = float(klines[i - 1][K_CLOSE])
            close = float(klines[i][K_CLOSE])

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )

            if as_percentage and close > 0:
                true_ranges.append((tr / close) * 100)
            else:
                true_ranges.append(tr)

        return round(sum(true_ranges) / len(true_ranges), 2)

    # ============================================================
    # RSI（相对强弱指数）
    # ============================================================

    @staticmethod
    def rsi(klines: list[list], period: int = 14) -> float:
        """
        计算 **RSI（Relative Strength Index，相对强弱指数）**

        RSI = 100 - 100 / (1 + RS)
        其中 RS = 周期内平均涨幅 / 周期内平均跌幅

        判断标准（传统）：
            - RSI > 70: 超买区域（可能回调）
            - RSI < 30: 超卖区域（可能反弹）
            - RSI = 50: 多空平衡线

        :param klines: K 线数据（至少 period + 1 根）
        :param period: 计算周期（默认 14）
        :return:       RSI 值（0 ~ 100）
        """
        if not klines or len(klines) < period + 1:
            return 50.0

        gains: float = 0.0
        losses: float = 0.0

        closes = [float(k[K_CLOSE]) for k in klines[-(period + 1):]]

        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            if change > 0:
                gains += change
            else:
                losses += abs(change)

        avg_gain = gains / period
        avg_loss = losses / period

        if avg_loss == 0:
            return 100.0
        if avg_gain == 0:
            return 0.0

        rs = avg_gain / avg_loss
        rsi_val = 100.0 - (100.0 / (1.0 + rs))

        return round(rsi_val, 2)

    # ============================================================
    # MACD
    # ============================================================

    @staticmethod
    def macd(
        klines: list[list],
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> dict[str, float]:
        """
        计算 **MACD（Moving Average Convergence Divergence）**

        MACD 线 = EMA(fast) - EMA(slow)
        信号线 = EMA(MACD, signal)
        柱状图 = MACD - 信号线

        判断标准：
            - MACD > 信号线: 金叉信号（看涨）
            - MACD < 信号线: 死叉信号（看跌）
            - 柱状图翻红: 多头动能增强

        :param klines: K 线数据
        :param fast:   快线周期（默认 12）
        :param slow:   慢线周期（默认 26）
        :param signal: 信号线周期（默认 9）
        :return:       {"dif": DIF值, "dea": DEA值, "histogram": 柱状图值}
        """
        if not klines or len(klines) < slow + signal:
            return {"dif": 0.0, "dea": 0.0, "histogram": 0.0}

        closes = [float(k[K_CLOSE]) for k in klines]

        # 计算 EMA 序列
        def calc_ema_series(data: list[float], period: int) -> list[float]:
            """计算完整 EMA 序列"""
            if len(data) < period:
                return []
            alpha = 2.0 / (period + 1.0)
            result = [sum(data[:period]) / period]  # 初始 SMA
            for val in data[period:]:
                result.append(alpha * val + (1 - alpha) * result[-1])
            return result

        ema_fast = calc_ema_series(closes, fast)
        ema_slow = calc_ema_series(closes, slow)

        # 对齐两条 EMA 序列
        offset = len(ema_slow) - len(ema_fast)
        dif_series = [
            ema_fast[i] - ema_slow[i + offset]
            for i in range(len(ema_fast))
        ]

        # DEA = DIF 的 EMA
        dea_series = calc_ema_series(dif_series, signal)

        dif = round(dif_series[-1], 6)
        dea = round(dea_series[-1], 6)
        histogram = round((dif - dea) * 2, 6)  # 中国习惯：柱状图 × 2

        return {"dif": dif, "dea": dea, "histogram": histogram}

    # ============================================================
    # 布林带（Bollinger Bands）
    # ============================================================

    @staticmethod
    def bollinger_bands(
        klines: list[list],
        period: int = 20,
        std_multiplier: float = 2.0,
    ) -> dict[str, float]:
        """
        计算**布林带（Bollinger Bands）**

        中轨 = MA(period)
        上轨 = 中轨 + std_multiplier × 标准差
        下轨 = 中轨 - std_multiplier × 标准差

        应用：
            - 价格触及上轨 → 可能超买（卖出信号）
            - 价格触及下轨 → 可能超卖（买入信号）
            - 带宽收窄 → 即将突破
            - 带宽扩张 → 趋势加速

        :param klines:          K 线数据
        :param period:          计算周期（默认 20）
        :param std_multiplier:  标准差倍数（默认 2.0）
        :return:                {"upper": 上轨, "middle": 中轨, "lower": 下轨, "width": 带宽%}
        """
        if not klines or len(klines) < period:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0, "width": 0.0}

        closes = [float(k[K_CLOSE]) for k in klines[-period:]]
        middle = sum(closes) / len(closes)

        # 计算标准差
        variance = sum((c - middle) ** 2 for c in closes) / len(closes)
        std_dev = variance ** 0.5

        upper = round(middle + std_multiplier * std_dev, 6)
        lower = round(middle - std_multiplier * std_dev, 6)
        width = round((upper - lower) / middle * 100, 2) if middle > 0 else 0.0

        return {
            "upper": upper,
            "middle": round(middle, 6),
            "lower": lower,
            "width": width,
        }

    # ============================================================
    # 乖离率
    # ============================================================

    @staticmethod
    def bias(klines: list[list], period: int = 20) -> float:
        """
        计算**乖离率（BIAS）**

        BIAS = (收盘价 - MA(N)) / MA(N) × 100

        反映价格偏离移动平均线的程度：
            - 正乖离过大 → 价格过高，可能回调
            - 负乖离过大 → 价格过低，可能反弹

        :param klines: K 线数据
        :param period: MA 周期（默认 20）
        :return:       乖离率（%）
        """
        if not klines or len(klines) < period:
            return 0.0

        current_close = float(klines[-1][K_CLOSE])
        ma_val = CalcIndex.ma(klines, period)

        if ma_val == 0:
            return 0.0

        return round((current_close - ma_val) / ma_val * 100, 2)

    # ============================================================
    # 量能分析
    # ============================================================

    @staticmethod
    def volume_ratio(klines: list[list], period: int = 5) -> float:
        """
        计算**量比**（当前成交量与均量的比值）

        量比 = 当前成交量 / 过去 N 根 K 线平均成交量

        :param klines: K 线数据
        :param period: 均量周期（默认 5）
        :return:       量比值
        """
        if not klines or len(klines) < period:
            return 1.0

        current_volume = float(klines[-1][K_VOLUME])
        avg_volume = sum(float(k[K_VOLUME]) for k in klines[-period:]) / period

        if avg_volume == 0:
            return 1.0

        return round(current_volume / avg_volume, 2)

    # ============================================================
    # 趋势综合判断
    # ============================================================

    @staticmethod
    def trend_score(klines: list[list]) -> dict[str, float]:
        """
        对多个技术指标进行**综合评分**

        评分维度（各维度满分 20 分，总分 100 分）：
            1. MA 多头/空头排列（20 分）
            2. MACD 金叉/死叉（20 分）
            3. RSI 超买/超卖（20 分）
            4. 布林带位置（20 分）
            5. 量能确认（20 分）

        :param klines: K 线数据（至少 30 根）
        :return:       各维度得分和总分
        """
        if not klines or len(klines) < 30:
            return {"total": 50.0, "ma_score": 10, "macd_score": 10,
                    "rsi_score": 10, "bb_score": 10, "volume_score": 10}

        scores: dict[str, float] = {}

        # 1. MA 排列 (20分)
        ma5 = CalcIndex.ma(klines, 5)
        ma20 = CalcIndex.ma(klines, 20)
        if ma5 > ma20:
            scores["ma_score"] = 20  # 多头排列
        elif ma5 < ma20:
            scores["ma_score"] = 0   # 空头排列
        else:
            scores["ma_score"] = 10

        # 2. MACD 信号 (20分)
        macd_data = CalcIndex.macd(klines)
        if macd_data["histogram"] > 0 and macd_data["dif"] > macd_data["dea"]:
            scores["macd_score"] = 20
        elif macd_data["histogram"] < 0 and macd_data["dif"] < macd_data["dea"]:
            scores["macd_score"] = 0
        else:
            scores["macd_score"] = 10

        # 3. RSI 位置 (20分)
        rsi_val = CalcIndex.rsi(klines, 14)
        if 40 <= rsi_val <= 60:
            scores["rsi_score"] = 10  # 中性
        elif 30 <= rsi_val < 40:
            scores["rsi_score"] = 15  # 偏空，可能反弹
        elif rsi_val < 30:
            scores["rsi_score"] = 20  # 超卖，反弹概率大
        elif 60 < rsi_val <= 70:
            scores["rsi_score"] = 5   # 偏多，可能回调
        else:
            scores["rsi_score"] = 0   # 超买

        # 4. 布林带位置 (20分)
        bb = CalcIndex.bollinger_bands(klines, 20)
        current_price = float(klines[-1][K_CLOSE])
        if bb["lower"] > 0 and current_price <= bb["lower"] * 1.02:
            scores["bb_score"] = 20  # 触及下轨，超卖
        elif bb["upper"] > 0 and current_price >= bb["upper"] * 0.98:
            scores["bb_score"] = 0   # 触及上轨，超买
        else:
            scores["bb_score"] = 10  # 中轨附近

        # 5. 量能分析 (20分)
        vol_ratio = CalcIndex.volume_ratio(klines)
        if vol_ratio > 1.5:
            scores["volume_score"] = 15  # 放量
        elif vol_ratio < 0.5:
            scores["volume_score"] = 5   # 缩量
        else:
            scores["volume_score"] = 10

        scores["total"] = sum(scores.values())
        return scores

    # ============================================================
    # 可用作买入/卖出辅助判断的方法
    # ============================================================

    def is_pullback_buy_signal(
        self, symbol: str, interval: str = "5m"
    ) -> bool:
        """
        判断是否为**回调买入信号**

        条件：价格正在回调（MA5 斜率向下），但未破位，
        此时买入可以拿到更好的价格。

        :param symbol:   交易对名称
        :param interval: K 线间隔
        :return:         是否满足回调买入条件
        """
        try:
            api = self._get_api()
            klines = api.get_klines(symbol, interval, 6)
            prev_ma5, curr_ma5 = self.ma_slope(klines, 5, interval)
            # 回调买入：MA5 走平或向下（不在拉伸状态）
            return prev_ma5 >= curr_ma5
        except Exception as exc:
            logger.warning(f"判断回调买点失败 {symbol}: {exc}")
            return True  # 异常时默认允许买入

    def is_breakout_sell_signal(
        self, symbol: str, interval: str = "5m"
    ) -> bool:
        """
        判断是否为**突破卖出信号**

        条件：价格正在拉升（MA5 斜率向上），不应立即卖出，
        等待拉升高点后再卖出以获取更大利润。

        :param symbol:   交易对名称
        :param interval: K 线间隔
        :return:         是否满足突破卖出条件
        """
        try:
            api = self._get_api()
            klines = api.get_klines(symbol, interval, 6)
            prev_ma5, curr_ma5 = self.ma_slope(klines, 5, interval)
            # 拉升中不卖：MA5 向上（在拉升状态）
            return prev_ma5 <= curr_ma5
        except Exception as exc:
            logger.warning(f"判断突破卖点失败 {symbol}: {exc}")
            return True  # 异常时默认允许卖出


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    calc = CalcIndex()

    # 尝试获取真实数据做自测
    try:
        api = BinanceAPI(api_key, api_secret)
        klines = api.get_klines("BTCUSDT", "1h", 100)
    except Exception:
        klines = []
        print("⚠️ 无法获取真实 K 线数据，使用模拟数据...")

    if klines:
        print("=" * 60)
        print("技术指标自测 (BTCUSDT 1h K线)")
        print("=" * 60)

        print(f"MA(5):   {calc.ma(klines, 5)}")
        print(f"MA(20):  {calc.ma(klines, 20)}")
        print(f"MA(60):  {calc.ma(klines, 60)}")
        print(f"EMA(20): {calc.ema(klines, 20)}")
        print(f"ATR(14): {calc.atr(klines, 14)}%")
        print(f"RSI(14): {calc.rsi(klines, 14)}")
        print(f"BIAS(20): {calc.bias(klines, 20)}%")
        print(f"量比(5):  {calc.volume_ratio(klines, 5)}")

        macd = calc.macd(klines)
        print(f"MACD: DIF={macd['dif']}, DEA={macd['dea']}, "
              f"柱={macd['histogram']}")

        bb = calc.bollinger_bands(klines, 20)
        print(f"布林带: 上={bb['upper']}, 中={bb['middle']}, "
              f"下={bb['lower']}, 宽={bb['width']}%")

        scores = calc.trend_score(klines)
        print(f"\n综合评分: {scores['total']}/100")
        for key, val in scores.items():
            if key != "total":
                print(f"  {key}: {val}/20")
    else:
        print("无 K 线数据，跳过指标计算")
