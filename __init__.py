# -*- coding: utf-8 -*-
"""
Binance 网格交易系统
====================
基于 Binance 现货 API 的自动化网格交易系统。

模块结构：
    - app/              核心应用模块
        - BinanceAPI    币安API封装（符合最新RESTful标准）
        - strategy      交易策略类（买入/卖出核心逻辑）
        - dingding      钉钉机器人消息推送
        - authorization API密钥配置
    - data/             数据层
        - runBetData    运行数据管理（JSON读写）
        - calcIndex     技术指标计算
    - run.py            主入口（交易循环 + GUI监控面板）
    - debug_api.py      API诊断工具
    - check_ip.py       IP检测工具

使用方式：
    python run.py          # 启动GUI交易监控系统
    python debug_api.py    # 诊断API连接
    python check_ip.py     # 检查公网IP
"""

__version__ = "2.0.0"
__author__ = "Grid Trading System"
