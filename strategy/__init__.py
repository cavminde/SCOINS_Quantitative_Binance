# -*- coding: utf-8 -*-
"""
策略模块
========
按业务标准组织策略：所有策略均继承自 ``BaseStrategy``，由 ``run.py`` 统一调度。

新策略开发只需：
    1. 继承 ``BaseStrategy``
    2. 实现 ``decide()`` 方法，返回 ``StrategyDecision``
    3. 在 ``strategy/builtin/`` 目录下创建文件，会被 ``auto_load()`` 自动发现

目录结构
--------
strategy/
    base.py                 ← 抽象基类 + 标准数据契约（含全局 registry 实例）
    registry.py             ← 策略注册表 + 自动加载
    builtin/                ← 内置策略
        __init__.py
        grid_strategy.py    ← 经典网格策略
        wld_strategy.py     ← WLD 波动性双模策略
"""

# 基础数据契约（无副作用，不会触发循环导入）
from strategy.base import (
    BaseStrategy,
    StrategyDecision,
    MarketContext,
    OrderRecord,
    ActionType,
    MarketTrend,
    StrategyRegistry,
)

__all__ = [
    "BaseStrategy",
    "StrategyDecision",
    "MarketContext",
    "OrderRecord",
    "ActionType",
    "MarketTrend",
    "StrategyRegistry",
    # registry 实例请从 strategy.base 或 strategy.registry 显式导入：
    #   from strategy.base import registry
    #   from strategy.registry import registry, auto_load
]
