# -*- coding: utf-8 -*-
"""
内置策略集合
============

此包下的所有策略均通过 ``@registry.register`` 装饰器自动注册。
新增内置策略：在此目录下创建新文件并实现 ``BaseStrategy`` 子类即可。
"""

from strategy.registry import registry, auto_load

# 包导入时即触发自动注册
auto_load()

__all__ = ["registry", "auto_load"]
