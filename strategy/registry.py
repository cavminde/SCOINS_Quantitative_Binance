# -*- coding: utf-8 -*-
"""
策略注册表
==========

提供两类能力：
1. **显式注册**：开发者在 ``builtin/`` 子模块中通过 ``@registry.register`` 装饰器注册；
2. **自动扫描**：调用 ``registry.auto_load()`` 自动发现并加载
   ``strategy/builtin/`` 下的全部策略类。

使用示例::

    from strategy import registry, BaseStrategy

    @registry.register
    class MyStrat(BaseStrategy):
        name = "my_strat"
        ...

    cls = registry.get("my_strat")
    instance = cls("BTCUSDT", {"period": 20})
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from strategy.base import BaseStrategy

logger = logging.getLogger(__name__)


# ── 关键修复：直接引用 strategy.base 中的全局 registry 实例 ──
# 避免多实例导致策略无法被找到
from strategy.base import registry as _base_registry


class _Proxy:
    """
    **代理**：把方法转发到 strategy.base.registry

    原因：auto_load() 在导入 strategy.builtin 时执行，
    此时它需要把策略注册到**全局唯一的** registry 上，
    因此 auto_load 内部直接使用 ``_base_registry`` 即可。
    本类只是为了对外保持 ``from strategy.registry import registry`` 的导入方式。
    """
    def __getattr__(self, name):
        return getattr(_base_registry, name)


# 对外暴露的 registry（代理到 base.registry，确保是同一实例）
registry = _base_registry


def auto_load() -> None:
    """
    **自动加载** ``strategy/builtin/`` 下的全部策略模块

    通过 ``importlib`` 动态导入，每个模块中只要有继承 ``BaseStrategy`` 的
    类（且 ``name`` 非空），都会被自动注册到 ``_base_registry``。
    """
    try:
        import strategy.builtin as builtin_pkg
    except ImportError as exc:
        logger.warning("无法导入 strategy.builtin 包: %s", exc)
        return

    for mod_info in pkgutil.iter_modules(builtin_pkg.__path__):
        mod_name = mod_info.name
        if mod_name.startswith("_"):
            continue
        full_name = f"strategy.builtin.{mod_name}"
        try:
            module = importlib.import_module(full_name)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseStrategy)
                    and attr is not BaseStrategy
                    and attr.name
                ):
                    if attr.name not in _base_registry.names():
                        _base_registry.register(attr)
                        logger.info("自动注册策略: %s -> %s", attr.name, attr.__name__)
        except Exception as exc:
            logger.error("加载策略模块 %s 失败: %s", full_name, exc)


__all__ = ["registry", "auto_load"]
