# -*- coding: utf-8 -*-
"""
币种状态持久化模块
==================

负责 data.json 的读写，**仅存储持仓状态与策略配置**。
不再保存"买入触发价/卖出触发价"——这些由 strategy 实时计算。

存储结构：
    {
        "coinList": ["WLDUSDT", ...],
        "WLDUSDT": {
            "benchmark": "USDT",
            "chain": "Binance",
            "state": {
                "strategy":     "grid",     # 策略名（来自 strategy.registry）
                "params":       {...},      # 策略参数
                "step":         0,          # 当前持仓步数
                "last_buy_price": 0.0,      # 最近一次买入价
                "recorded_prices": []       # 历史买入价（用于多层持仓）
            }
        }
    }
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_PATH: Path = Path(__file__).parent / "data.json"

DEFAULT_STATE: dict[str, Any] = {
    "strategy": "grid",
    "params": {
        "profit_ratio": 5.0,
        "double_throw_ratio": 5.0,
        "stop_loss_ratio": 6.0,
        "quantity": 9.1,
    },
    "step": 0,
    "last_buy_price": 0.0,
    "recorded_prices": [],
}


class DataStore:
    """
    币种状态持久化（线程安全：单例 + 原子写入）
    """
    def __init__(self, data_path: Path | None = None) -> None:
        self._path: Path = data_path or DATA_PATH

    # ----------------------------------------------------------
    # 文件 I/O
    # ----------------------------------------------------------

    def _load(self) -> dict:
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        tmp.replace(self._path)
        logger.debug("已保存到 %s", self._path)

    # ----------------------------------------------------------
    # 币种列表
    # ----------------------------------------------------------

    def get_coin_list(self) -> list[str]:
        try:
            return list(self._load().get("coinList", []))
        except Exception as exc:
            logger.error("读取 coinList 失败: %s", exc)
            return []

    def add_coin(
        self,
        symbol: str,
        benchmark: str = "USDT",
        chain: str = "Binance",
        strategy_name: str = "grid",
        params: dict | None = None,
    ) -> None:
        """
        **添加新币种**到监控列表

        若币种已存在则跳过。
        """
        symbol = symbol.upper().strip()
        if not symbol:
            raise ValueError("symbol 不能为空")
        data = self._load()
        if symbol in data.get("coinList", []):
            logger.info("%s 已在监控列表中，跳过添加", symbol)
            return

        data.setdefault("coinList", []).append(symbol)
        data[symbol] = {
            "benchmark": benchmark,
            "chain": chain,
            "state": {
                **DEFAULT_STATE,
                "strategy": strategy_name,
                "params": {**DEFAULT_STATE["params"], **(params or {})},
                "recorded_prices": [],
            },
        }
        self._save(data)
        logger.info("已添加币种: %s (策略=%s)", symbol, strategy_name)

    def remove_coin(self, symbol: str) -> None:
        """从监控列表中移除币种"""
        symbol = symbol.upper().strip()
        data = self._load()
        coins = data.get("coinList", [])
        if symbol in coins:
            coins.remove(symbol)
        data.pop(symbol, None)
        self._save(data)
        logger.info("已移除币种: %s", symbol)

    # ----------------------------------------------------------
    # 单币种状态读写
    # ----------------------------------------------------------

    def get_state(self, symbol: str) -> dict:
        """
        读取币种状态；若不存在返回默认空仓状态
        """
        try:
            data = self._load()
            entry = data.get(symbol, {})
            state = entry.get("state", {})
            return {
                "strategy": state.get("strategy", "grid"),
                "params": dict(state.get("params", {})),
                "step": int(state.get("step", 0)),
                "last_buy_price": float(state.get("last_buy_price", 0.0)),
                "recorded_prices": list(state.get("recorded_prices", [])),
            }
        except Exception as exc:
            logger.error("读取 %s 状态失败: %s", symbol, exc)
            return {**DEFAULT_STATE, "params": dict(DEFAULT_STATE["params"]),
                    "recorded_prices": []}

    def update_state(self, symbol: str, **kwargs: Any) -> None:
        """
        更新币种状态字段

        示例：data.update_state("WLDUSDT", step=1, last_buy_price=0.55)
        """
        data = self._load()
        if symbol not in data:
            logger.warning("%s 不在配置中，无法更新", symbol)
            return
        state = data[symbol].setdefault("state", {})
        for k, v in kwargs.items():
            state[k] = v
        self._save(data)

    def update_params(self, symbol: str, params: dict) -> None:
        """更新币种的策略参数"""
        data = self._load()
        if symbol not in data:
            return
        data[symbol].setdefault("state", {}).setdefault("params", {}).update(params)
        self._save(data)

    def switch_strategy(self, symbol: str, strategy_name: str,
                        params: dict | None = None) -> None:
        """切换币种的策略"""
        data = self._load()
        if symbol not in data:
            return
        state = data[symbol].setdefault("state", {})
        state["strategy"] = strategy_name
        if params:
            state.setdefault("params", {}).update(params)
        self._save(data)

    # ----------------------------------------------------------
    # 交易后状态更新（便捷方法）
    # ----------------------------------------------------------

    def record_buy(self, symbol: str, fill_price: float) -> None:
        """记录买入成交：步数 +1，记录价格"""
        state = self.get_state(symbol)
        state["step"] += 1
        state["last_buy_price"] = fill_price
        state["recorded_prices"].append(fill_price)
        self.update_state(symbol, **state)

    def record_sell(self, symbol: str) -> None:
        """记录卖出成交：步数 -1，移除最近买入记录"""
        state = self.get_state(symbol)
        if state["step"] > 0:
            state["step"] -= 1
        if state["recorded_prices"]:
            state["recorded_prices"].pop()
        state["last_buy_price"] = (
            state["recorded_prices"][-1] if state["recorded_prices"] else 0.0
        )
        self.update_state(symbol, **state)
