# -*- coding: utf-8 -*-
"""
钉钉机器人消息通知模块
======================
通过钉钉自定义机器人 Webhook 发送交易告警和状态通知。

配置方法：
    1. 打开钉钉PC端 → 群设置 → 智能群助手 → 添加机器人 → 自定义
    2. 复制 Webhook URL 中的 access_token
    3. 将 token 填入 app/authorization.py 的 dingding_token 变量

消息类型：
    - 交易通知：买入/卖出成交后推送（含价格、数量、盈亏）
    - 告警通知：止损触发、API异常、系统错误
    - 状态通知：系统启动/停止、配置变更
"""

import requests
import json
import logging
from typing import Any

from app.authorization import dingding_token, api_secret, api_key
from app.BinanceAPI import BinanceAPI

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger(__name__)


class DingDingNotifier:
    """
    钉钉通知器

    封装钉钉机器人消息推送逻辑，提供：
        - 交易成交通知（买入/卖出）
        - 系统告警通知
        - 自定义消息推送

    使用示例：
        >>> notifier = DingDingNotifier()
        >>> notifier.send_trade_notification("BUY", "BTCUSDT", 0.001, 85000.0)
    """

    def __init__(self, token: str | None = None):
        """
        初始化通知器

        :param token: 钉钉机器人 access_token（None 则从 authorization 读取）
        """
        self.token: str = token or dingding_token
        self._base_url: str = "https://oapi.dingtalk.com/robot/send"
        self._enabled: bool = bool(self.token and not self.token.startswith("your_"))

    # ----------------------------------------------------------
    # 交易通知
    # ----------------------------------------------------------

    def send_buy_notification(
        self,
        symbol: str,
        quantity: float,
        price: float,
        order_id: int | None = None,
    ) -> bool:
        """
        发送**买入成交**通知

        :param symbol:   交易对名称
        :param quantity: 买入数量
        :param price:    成交均价
        :param order_id: 订单 ID（可选）
        :return:         是否发送成功
        """
        total_cost = round(quantity * price, 2)
        message = (
            f"【买入成交】\n"
            f"币种: {symbol}\n"
            f"数量: {quantity}\n"
            f"价格: {price}\n"
            f"成交金额: {total_cost} USDT\n"
            f"时间: {self._now()}\n"
            + (f"订单ID: {order_id}" if order_id else "")
        )
        return self.send(message, title="买入通知")

    def send_sell_notification(
        self,
        symbol: str,
        quantity: float,
        profit_usdt: float = 0.0,
        profit_pct: float = 0.0,
        order_id: int | None = None,
    ) -> bool:
        """
        发送**卖出成交**通知

        :param symbol:     交易对名称
        :param quantity:   卖出数量
        :param profit_usdt: 盈利金额（USDT）
        :param profit_pct:  盈利率（%）
        :param order_id:   订单 ID（可选）
        :return:           是否发送成功
        """
        emoji = "🟢" if profit_usdt >= 0 else "🔴"
        message = (
            f"{emoji} 【卖出成交】\n"
            f"币种: {symbol}\n"
            f"数量: {quantity}\n"
            f"盈利: {profit_usdt:.2f} USDT ({profit_pct:+.2f}%)\n"
            f"时间: {self._now()}\n"
            + (f"订单ID: {order_id}" if order_id else "")
        )
        return self.send(message, title="卖出通知")

    # ----------------------------------------------------------
    # 告警通知
    # ----------------------------------------------------------

    def send_stop_loss_alert(
        self,
        symbol: str,
        current_price: float,
        stop_loss_price: float,
        loss_amount: float,
    ) -> bool:
        """
        发送**止损触发**告警

        :param symbol:          交易对名称
        :param current_price:   当前触发价格
        :param stop_loss_price: 止损线价格
        :param loss_amount:     预计亏损金额
        :return:                是否发送成功
        """
        message = (
            f"🚨 【止损触发告警】\n"
            f"币种: {symbol}\n"
            f"当前价格: {current_price}\n"
            f"止损价格: {stop_loss_price}\n"
            f"预计亏损: {loss_amount:.2f} USDT\n"
            f"时间: {self._now()}\n"
            f"请及时检查账户状态！"
        )
        return self.send(message, title="止损告警", at_all=True)

    def send_error_alert(self, title: str, error_detail: str) -> bool:
        """
        发送**系统错误**告警

        :param title:        错误标题
        :param error_detail: 错误详细描述
        :return:             是否发送成功
        """
        message = (
            f"⚠️ 【系统异常】{title}\n"
            f"详情: {error_detail}\n"
            f"时间: {self._now()}"
        )
        return self.send(message, title="错误告警")

    # ----------------------------------------------------------
    # 通用消息发送
    # ----------------------------------------------------------

    def send(
        self,
        text: str,
        title: str = "",
        at_all: bool = False,
        at_mobiles: list[str] | None = None,
    ) -> bool:
        """
        发送**通用文本消息**到钉钉群

        :param text:       消息正文
        :param title:      消息标题（用于日志标识）
        :param at_all:     是否 @所有人
        :param at_mobiles: 要 @ 的手机号列表
        :return:           是否发送成功
        """
        if not self._enabled:
            logger.debug(f"钉钉通知未配置，跳过: {title}")
            return False

        try:
            headers = {"Content-Type": "application/json;charset=utf-8"}
            api_url = f"{self._base_url}?access_token={self.token}"

            payload: dict[str, Any] = {
                "msgtype": "text",
                "at": {
                    "atMobiles": at_mobiles or [],
                    "isAtAll": at_all,
                },
                "text": {
                    "content": f"【Binance 网格交易】{title}\n{text}" if title else text,
                },
            }

            resp = requests.post(
                api_url,
                json.dumps(payload),
                headers=headers,
                timeout=10,
            )
            result = resp.json()

            if result.get("errcode") == 0:
                logger.info(f"钉钉消息发送成功: {title}")
                return True
            else:
                logger.warning(
                    f"钉钉消息发送失败: {result.get('errmsg')} (errcode={result.get('errcode')})"
                )
                return False

        except Exception as exc:
            logger.error(f"钉钉消息发送异常: {exc}")
            return False

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------

    @staticmethod
    def _now() -> str:
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def is_enabled(self) -> bool:
        """检查通知是否已启用"""
        return self._enabled


# ============================================================
# 兼容旧版 Message 类（桥接封装）
# ============================================================

class Message:
    """
    兼容 COIN 旧版 Message 接口的桥接类

    保持与原有 run.py 的调用方式一致，
    内部委托给 DingDingNotifier 和 BinanceAPI。

    使用示例：
        >>> msg = Message(api)  # 传入已有的 BinanceAPI 实例
        >>> res = msg.buy_market_msg("BTCUSDT", 0.001)
        >>> if 'orderId' in res:
        ...     print("买入成功")
    """

    def __init__(self, api: BinanceAPI | None = None):
        """
        初始化消息模块

        :param api: BinanceAPI 实例（推荐传入以避免重复端点探测）
        """
        self._notifier = DingDingNotifier()
        self._api = api or BinanceAPI(api_key, api_secret)

    # ── 市价单 ──

    def buy_market_msg(self, symbol: str, quantity: float) -> dict:
        """
        **市价买入** + 钉钉通知

        流程：调用 Binance API 下市价买单 → 成功则推送钉钉通知

        :param symbol:   交易对名称
        :param quantity: 买入数量
        :return:         API 响应字典（含 orderId 表示成功）
        """
        try:
            res = self._api.buy_market(symbol, quantity)
            if isinstance(res, dict) and "orderId" in res:
                fill_price = float(res.get("fills", [{}])[0].get("price", 0))
                order_id = res.get("orderId")
                self._notifier.send_buy_notification(
                    symbol, quantity, fill_price, order_id
                )
                logger.info(f"市价买入成功: {symbol} × {quantity} @ {fill_price}")
            else:
                err_msg = res.get("msg", str(res)) if isinstance(res, dict) else str(res)
                self._notifier.send_error_alert(
                    f"买入失败 - {symbol}",
                    f"数量: {quantity}, 原因: {err_msg}",
                )
                logger.error(f"市价买入失败: {symbol}, {err_msg}")
            return res if isinstance(res, dict) else {"code": -1, "msg": str(res)}
        except Exception as exc:
            logger.error(f"买入异常 {symbol}: {exc}")
            self._notifier.send_error_alert(f"买入异常 - {symbol}", str(exc))
            return {"code": -2, "msg": str(exc)}

    def sell_market_msg(
        self, symbol: str, quantity: float, profit_usdt: float = 0.0
    ) -> dict:
        """
        **市价卖出** + 钉钉通知

        :param symbol:     交易对名称
        :param quantity:   卖出数量
        :param profit_usdt: 预计盈利（USDT）
        :return:           API 响应字典
        """
        try:
            res = self._api.sell_market(symbol, quantity)
            if isinstance(res, dict) and "orderId" in res:
                order_id = res.get("orderId")
                self._notifier.send_sell_notification(
                    symbol, quantity, profit_usdt, order_id=order_id
                )
                logger.info(
                    f"市价卖出成功: {symbol} × {quantity}, 盈利: {profit_usdt:.2f} USDT"
                )
            else:
                err_msg = res.get("msg", str(res)) if isinstance(res, dict) else str(res)
                self._notifier.send_error_alert(
                    f"卖出失败 - {symbol}",
                    f"数量: {quantity}, 原因: {err_msg}",
                )
                logger.error(f"市价卖出失败: {symbol}, {err_msg}")
            return res if isinstance(res, dict) else {"code": -1, "msg": str(res)}
        except Exception as exc:
            logger.error(f"卖出异常 {symbol}: {exc}")
            self._notifier.send_error_alert(f"卖出异常 - {symbol}", str(exc))
            return {"code": -2, "msg": str(exc)}

    # ── 限价单 ──

    def buy_limit_msg(self, symbol: str, quantity: float, price: float) -> dict:
        """
        **限价买入** + 钉钉通知

        :param symbol:   交易对名称
        :param quantity: 买入数量
        :param price:    限价价格
        :return:         API 响应字典
        """
        try:
            res = self._api.buy_limit(symbol, quantity, price)
            if isinstance(res, dict) and "orderId" in res:
                self._notifier.send(
                    f"【限价买单已挂出】\n币种: {symbol}\n数量: {quantity}\n"
                    f"挂单价: {price}\n时间: {DingDingNotifier._now()}",
                    title="限价买入挂单",
                )
                logger.info(f"限价买入挂单: {symbol} × {quantity} @ {price}")
            else:
                err_msg = res.get("msg", str(res)) if isinstance(res, dict) else str(res)
                self._notifier.send_error_alert(
                    f"限价买单失败 - {symbol}", err_msg
                )
            return res if isinstance(res, dict) else {"code": -1, "msg": str(res)}
        except Exception as exc:
            logger.error(f"限价买入异常 {symbol}: {exc}")
            return {"code": -2, "msg": str(exc)}

    def sell_limit_msg(
        self, symbol: str, quantity: float, price: float, profit_usdt: float = 0.0
    ) -> dict:
        """
        **限价卖出** + 钉钉通知

        :param symbol:     交易对名称
        :param quantity:   卖出数量
        :param price:      限价价格
        :param profit_usdt: 预计盈利
        :return:           API 响应字典
        """
        try:
            res = self._api.sell_limit(symbol, quantity, price)
            if isinstance(res, dict) and "orderId" in res:
                self._notifier.send(
                    f"【限价卖单已挂出】\n币种: {symbol}\n数量: {quantity}\n"
                    f"挂单价: {price}\n预计盈利: {profit_usdt:.2f} USDT\n"
                    f"时间: {DingDingNotifier._now()}",
                    title="限价卖出挂单",
                )
                logger.info(f"限价卖出挂单: {symbol} × {quantity} @ {price}")
            else:
                err_msg = res.get("msg", str(res)) if isinstance(res, dict) else str(res)
                self._notifier.send_error_alert(
                    f"限价卖单失败 - {symbol}", err_msg
                )
            return res if isinstance(res, dict) else {"code": -1, "msg": str(res)}
        except Exception as exc:
            logger.error(f"限价卖出异常 {symbol}: {exc}")
            return {"code": -2, "msg": str(exc)}

    # ── 钉钉告警（独立调用）──

    def send_alert(self, text: str, at_all: bool = False) -> bool:
        """
        发送自定义钉钉告警消息

        :param text:   告警内容
        :param at_all: 是否 @所有人
        :return:       是否发送成功
        """
        return self._notifier.send(text, title="手动告警", at_all=at_all)


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    notifier = DingDingNotifier()
    print(f"钉钉通知状态: {'已启用' if notifier.is_enabled else '未配置（跳过发送）'}")

    # 测试发送（仅在已配置时）
    if notifier.is_enabled:
        notifier.send(
            "这是一条测试消息，如果您看到此消息，表明钉钉通知配置正确。",
            title="配置测试",
        )
