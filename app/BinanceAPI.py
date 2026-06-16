# -*- coding: utf-8 -*-
"""
Binance 现货 API 封装模块
========================
基于 Binance 官方 REST API 文档（https://developers.binance.com/docs/zh-CN/binance-spot-api-docs）
实现的 Python 客户端，支持：

    - 公开接口：行情数据、K线数据、交易对信息
    - 签名接口：下单（市价/限价）、账户信息、订单查询
    - 多节点自动故障切换
    - 自动签名（HMAC-SHA256）
    - 数量精度自动适配（根据交易对 stepSize）

API 端点（按优先级排列）：
    https://api.binance.com/api/v3
    https://api1.binance.com/api/v3
    https://api2.binance.com/api/v3
    https://api3.binance.com/api/v3
    https://api4.binance.com/api/v3
"""

import requests
import time
import hmac
import hashlib
import json
import math
import logging
from typing import Optional, Any
from pathlib import Path
from urllib.parse import urlencode

from app.authorization import dingding_token, recv_window, api_secret, api_key

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger(__name__)

# ============================================================
# 配置文件路径
# ============================================================
DATA_PATH: Path = Path(__file__).parent.parent / "data" / "data.json"


class BinanceAPIException(Exception):
    """Binance API 自定义异常，携带错误码与响应内容"""

    def __init__(self, code: int, message: str, response: dict | None = None):
        self.code = code
        self.message = message
        self.response = response or {}
        super().__init__(f"[错误码 {code}] {message}")


class BinanceAPI:
    """
    Binance 现货 API 客户端

    功能概览：
        - 公开行情：get_ticker_price / get_ticker_24hour / get_klines / get_exchange_info
        - 账户交易：buy_market / sell_market / buy_limit / sell_limit / get_account / get_order
        - 工具方法：get_step_size / get_price_precision

    使用示例：
        >>> api = BinanceAPI(api_key, api_secret)
        >>> price = api.get_ticker_price("BTCUSDT")
        >>> print(f"BTC 当前价格: {price}")
    """

    # ============================================================
    # Binance 官方 API 端点列表（主网）
    # 参考文档: https://developers.binance.com/docs/zh-CN/binance-spot-api-docs/rest-api
    # ============================================================
    BASE_URLS: list[str] = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
        "https://api4.binance.com",
    ]

    # 测试网端点（用于开发调试，无需真实资金）
    TESTNET_URL: str = "https://testnet.binance.vision"

    # 公开接口的 API 版本前缀（v3 = 现货标准接口）
    API_VERSION: str = "/api/v3"

    def __init__(self, key: str, secret: str, testnet: bool = False):
        """
        初始化 Binance API 客户端

        :param key:     API Key（从 Binance 后台获取）
        :param secret:  API Secret（从 Binance 后台获取）
        :param testnet: 是否使用测试网（默认 False = 主网）
        """
        self.key: str = key
        self.secret: str = secret
        self.testnet: bool = testnet

        # 构建完整的 REST API 端点列表
        if testnet:
            endpoints: list[str] = [f"{self.TESTNET_URL}{self.API_VERSION}"]
        else:
            endpoints = [f"{url}{self.API_VERSION}" for url in self.BASE_URLS]

        # 自动选择延迟最低的端点
        self.base_url: str = self._select_fastest_endpoint(endpoints)

        # 缓存交易对信息（避免重复请求）
        self._exchange_info_cache: dict | None = None
        self._exchange_info_cache_time: float = 0.0

    # ============================================================
    # 端点选择
    # ============================================================

    def _select_fastest_endpoint(self, endpoints: list[str]) -> str:
        """
        探测所有端点并返回延迟最低的一个

        :param endpoints: 端点 URL 列表
        :return: 延迟最低的端点 URL
        :raises BinanceAPIException: 所有端点均不可达时抛出
        """
        latencies: dict[str, float] = {}

        for endpoint in endpoints:
            ping_url = f"{endpoint}/ping"
            try:
                start = time.time()
                resp = requests.get(ping_url, timeout=5, verify=True)
                elapsed = (time.time() - start) * 1000  # 毫秒
                if resp.status_code == 200:
                    latencies[endpoint] = elapsed
                    logger.info(f"端点 {endpoint} 延迟: {elapsed:.1f}ms")
            except Exception as exc:
                logger.debug(f"端点 {endpoint} 不可达: {exc}")

        if not latencies:
            raise BinanceAPIException(-1000, "所有 Binance API 端点均无法连接")

        fastest = min(latencies, key=latencies.get)
        logger.info(f"选用最快端点: {fastest} ({latencies[fastest]:.1f}ms)")
        return fastest

    def get_ticker_price(self, symbol: str) -> float | None:
        """
        获取指定交易对的**最新成交价格**

        GET /api/v3/ticker/price

        参考文档：
            https://developers.binance.com/docs/zh-CN/binance-spot-api-docs/rest-api/market-data-endpoints#symbol-price-ticker

        :param symbol: 交易对名称，如 "BTCUSDT"、"ETHUSDT"
        :return:       最新价格（float），失败返回 None

        使用示例：
            >>> price = api.get_ticker_price("BTCUSDT")
            >>> print(f"BTC 价格: {price}")
        """
        data = self._public_request("/ticker/price", {"symbol": symbol})
        if data and "price" in data:
            try:
                return float(data["price"])
            except (ValueError, TypeError):
                return None
        return None

    def get_ticker_24hour(self, symbol: str) -> dict | None:
        """
        获取指定交易对的**24小时行情统计**

        GET /api/v3/ticker/24hr

        返回字段包括：
            - priceChange:       24小时价格变动
            - priceChangePercent: 24小时涨跌幅（%）
            - highPrice:         24小时最高价
            - lowPrice:          24小时最低价
            - volume:            24小时成交量
            - lastPrice:         最新成交价

        :param symbol: 交易对名称
        :return:       24小时行情数据字典
        """
        return self._public_request("/ticker/24hr", {"symbol": symbol})

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list]:
        """
        获取指定交易对的 **K线（蜡烛图）数据**

        GET /api/v3/klines

        参考文档：
            https://developers.binance.com/docs/zh-CN/binance-spot-api-docs/rest-api/market-data-endpoints#klinecandlestick-data

        :param symbol:     交易对名称，如 "BTCUSDT"
        :param interval:   K线间隔，可选值：
                           1m / 3m / 5m / 15m / 30m
                           1h / 2h / 4h / 6h / 8h / 12h
                           1d / 3d / 1w / 1M
        :param limit:      返回数量，默认 500，最大 1000
        :param start_time: 起始时间（毫秒时间戳）
        :param end_time:   结束时间（毫秒时间戳）
        :return:           K线数据列表，每条记录格式：
                           [开盘时间, 开盘价, 最高价, 最低价, 收盘价, 成交量,
                            收盘时间, 成交额, 成交笔数, 主动买入成交量,
                            主动买入成交额, 忽略]

        使用示例：
            >>> klines = api.get_klines("BTCUSDT", "1h", limit=100)
            >>> for k in klines[:3]:
            ...     print(f"时间: {k[0]}, 收盘价: {k[4]}")
        """
        params: dict = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time

        return self._public_request("/klines", params) or []

    def get_exchange_info(self, symbol: str | None = None) -> dict:
        """
        获取**交易对信息**（交易规则、精度、过滤器等）

        GET /api/v3/exchangeInfo

        参考文档：
            https://developers.binance.com/docs/zh-CN/binance-spot-api-docs/rest-api/market-data-endpoints#exchange-information

        :param symbol: 可选，指定交易对以缩小返回范围
        :return:       交易对信息字典，包含 symbols、filters 等

        使用示例：
            >>> info = api.get_exchange_info("ETHUSDT")
            >>> for f in info['symbols'][0]['filters']:
            ...     if f['filterType'] == 'LOT_SIZE':
            ...         print(f"最小下单量: {f['minQty']}, 步长: {f['stepSize']}")
        """
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._public_request("/exchangeInfo", params) or {}

    def get_order_book(self, symbol: str, limit: int = 100) -> dict | None:
        """
        获取指定交易对的**订单簿深度信息**

        GET /api/v3/depth

        :param symbol: 交易对名称
        :param limit:  深度档位：5 / 10 / 20 / 50 / 100 / 500 / 1000 / 5000
        :return:       订单簿数据，包含 bids（买盘）和 asks（卖盘）
        """
        return self._public_request("/depth", {"symbol": symbol, "limit": limit})

    # ============================================================
    # 签名接口（需要 API Key + Secret）
    # ============================================================

    def get_account(self) -> dict:
        """
        获取**账户信息**（余额、权限等）

        GET /api/v3/account (HMAC SHA256 签名)

        参考文档：
            https://developers.binance.com/docs/zh-CN/binance-spot-api-docs/rest-api/account-endpoints#account-information-user_data

        :return: 账户信息字典，balances 字段包含各币种余额
        """
        return self._signed_request("GET", "/account")

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """
        查询**当前挂单**（未成交的订单）

        GET /api/v3/openOrders (HMAC SHA256 签名)

        :param symbol: 可选，指定交易对
        :return:       挂单列表
        """
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._signed_request("GET", "/openOrders", params) or []

    def get_order(self, symbol: str, order_id: int | None = None) -> dict:
        """
        查询**指定订单状态**

        GET /api/v3/order (HMAC SHA256 签名)

        :param symbol:   交易对名称
        :param order_id: 订单 ID
        :return:         订单详情字典
        """
        params = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        return self._signed_request("GET", "/order", params)

    def buy_market(self, symbol: str, quantity: float) -> dict:
        """
        **市价买入** - 以当前市场最优价格立即买入

        POST /api/v3/order (HMAC SHA256 签名)
        参数: type=MARKET, side=BUY

        注意：
            - 市价单不保证成交价格，实际价格由市场深度决定
            - 大额市价单可能产生较大的滑点
            - 小额币种请留意流动性

        :param symbol:   交易对名称，如 "BTCUSDT"
        :param quantity: 买入数量（以 base asset 计，如 BTC 的个数）
        :return:         订单响应字典，包含 orderId, fills, status 等字段

        使用示例：
            >>> res = api.buy_market("BTCUSDT", 0.001)
            >>> if 'orderId' in res:
            ...     print(f"买入成功，订单ID: {res['orderId']}")
            ...     print(f"成交均价: {res['fills'][0]['price']}")
        """
        params = self._build_order_params(symbol, quantity, "BUY")
        return self._signed_request("POST", "/order", params)

    def sell_market(self, symbol: str, quantity: float) -> dict:
        """
        **市价卖出** - 以当前市场最优价格立即卖出

        POST /api/v3/order (HMAC SHA256 签名)
        参数: type=MARKET, side=SELL

        :param symbol:   交易对名称
        :param quantity: 卖出数量（以 base asset 计）
        :return:         订单响应字典

        使用示例：
            >>> res = api.sell_market("BTCUSDT", 0.001)
            >>> if 'orderId' in res:
            ...     print(f"卖出成功，订单ID: {res['orderId']}")
        """
        params = self._build_order_params(symbol, quantity, "SELL")
        return self._signed_request("POST", "/order", params)

    def buy_limit(self, symbol: str, quantity: float, price: float) -> dict:
        """
        **限价买入** - 以指定价格挂买单

        POST /api/v3/order (HMAC SHA256 签名)
        参数: type=LIMIT, side=BUY, timeInForce=GTC

        限价单特点：
            - GTC（Good-Till-Canceled）：一直有效直到成交或被取消
            - 如果市场价格达到限价，订单将成交
            - 未成交部分保留在订单簿中

        :param symbol:   交易对名称
        :param quantity: 买入数量
        :param price:    限价价格
        :return:         订单响应字典
        """
        params = self._build_order_params(symbol, quantity, "BUY", price)
        return self._signed_request("POST", "/order", params)

    def sell_limit(self, symbol: str, quantity: float, price: float) -> dict:
        """
        **限价卖出** - 以指定价格挂卖单

        POST /api/v3/order (HMAC SHA256 签名)
        参数: type=LIMIT, side=SELL, timeInForce=GTC

        :param symbol:   交易对名称
        :param quantity: 卖出数量
        :param price:    限价价格
        :return:         订单响应字典
        """
        params = self._build_order_params(symbol, quantity, "SELL", price)
        return self._signed_request("POST", "/order", params)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """
        **撤销订单**

        DELETE /api/v3/order (HMAC SHA256 签名)

        :param symbol:   交易对名称
        :param order_id: 要撤销的订单 ID
        :return:         撤销结果字典
        """
        params = {"symbol": symbol, "orderId": order_id}
        return self._signed_request("DELETE", "/order", params)

    def get_trades(self, symbol: str, limit: int = 500) -> list[dict]:
        """
        查询**近期成交记录**

        GET /api/v3/myTrades (HMAC SHA256 签名)

        :param symbol: 交易对名称
        :param limit:  返回数量，默认 500，最大 1000
        :return:       成交记录列表
        """
        return self._signed_request("GET", "/myTrades", {"symbol": symbol, "limit": limit}) or []

    # ============================================================
    # 工具方法
    # ============================================================

    def get_step_size(self, symbol: str) -> str:
        """
        获取指定交易对的**数量步长（stepSize）**

        从 exchangeInfo 的 LOT_SIZE 过滤器中提取。
        用于下单时自动格式化数量，避免精度过高导致 API 拒绝。

        :param symbol: 交易对名称
        :return:       stepSize 字符串，如 "0.000001"，获取失败返回 "0.000001"

        使用示例：
            >>> step = api.get_step_size("ETHUSDT")
            >>> print(f"ETHUSDT 数量步长: {step}")
        """
        try:
            info = self.get_exchange_info(symbol)
            symbols = info.get("symbols", [])
            for sym in symbols:
                if sym.get("symbol") == symbol:
                    for f in sym.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            return f.get("stepSize", "0.000001")
            return "0.000001"
        except Exception:
            return "0.000001"

    def get_price_precision(self, symbol: str) -> int:
        """
        获取指定交易对的**价格精度（小数位数）**

        从 exchangeInfo 的 PRICE_FILTER 过滤器中提取。

        :param symbol: 交易对名称
        :return:       价格小数位数，如 BTCUSDT 返回 2
        """
        try:
            info = self.get_exchange_info(symbol)
            symbols = info.get("symbols", [])
            for sym in symbols:
                if sym.get("symbol") == symbol:
                    for f in sym.get("filters", []):
                        if f.get("filterType") == "PRICE_FILTER":
                            tick_size = f.get("tickSize", "0.01")
                            precision = abs(round(math.log10(float(tick_size))))
                            return max(0, precision)
            return 2
        except Exception:
            return 2

    def format_quantity(self, quantity: float, step_size: str) -> str:
        """
        根据 stepSize **格式化下单数量**

        确保数量符合交易对的最小数量步长要求，避免 API 拒绝下单。

        :param quantity:  原始数量
        :param step_size: 数量步长（如 "0.001"）
        :return:          格式化后的数量字符串

        使用示例：
            >>> step = "0.001"
            >>> qty = api.format_quantity(1.234567, step)
            >>> print(qty)  # "1.234"
        """
        try:
            step = float(step_size)
            precision = int(round(-math.log(step, 10), 0))
            precision = max(0, precision)
            return f"{round(quantity, precision):.{precision}f}"
        except (ValueError, TypeError):
            return f"{quantity:.6f}"

    def format_price(self, price: float, precision: int | None = None) -> str:
        """
        根据精度**格式化价格**

        :param price:     原始价格
        :param precision: 小数位数（None 则默认 6 位）
        :return:          格式化后的价格字符串
        """
        if precision is not None:
            return f"{price:.{precision}f}"
        return f"{price:.2f}"

    # ============================================================
    # 内部方法 - 公开接口请求
    # ============================================================

    def _public_request(self, api_path: str, params: dict | None = None) -> Any:
        """
        发起**无需签名的公开 GET 请求**

        :param api_path: API 路径，如 "/ticker/price"
        :param params:   查询参数字典
        :return:         JSON 解析后的响应数据
        """
        if params is None:
            params = {}

        url = f"{self.base_url}{api_path}"
        query = urlencode(params)
        full_url = f"{url}?{query}" if query else url

        resp = requests.get(full_url, timeout=10, verify=True)
        data = resp.json()

        # 检查 Binance 自定义错误码
        if isinstance(data, dict) and "code" in data:
            err_code = data["code"]
            err_msg = data.get("msg", str(data))
            logger.error(f"API 返回错误 [code={err_code}]: {err_msg}")
            raise BinanceAPIException(err_code, err_msg, data)

        return data

    # ============================================================
    # 内部方法 - 签名请求
    # ============================================================

    def _signed_request(
        self,
        method: str,
        api_path: str,
        params: dict | None = None,
    ) -> Any:
        """
        发起**需要 HMAC SHA256 签名的请求**

        签名流程：
            1. 添加 timestamp 和 recvWindow 参数
            2. 将参数序列化为 query string
            3. 使用 API Secret 对 query string 做 HMAC-SHA256 签名
            4. 在请求头中携带 X-MBX-APIKEY
            5. 发送带签名的请求

        参考文档：
            https://developers.binance.com/docs/zh-CN/binance-spot-api-docs/rest-api#signed-trade-and-user_data-endpoint-security

        :param method:   HTTP 方法（GET / POST / DELETE）
        :param api_path: API 路径，如 "/order"
        :param params:   请求参数字典
        :return:         JSON 解析后的响应数据
        """
        if params is None:
            params = {}

        # 添加时间戳和接收窗口（防重放攻击）
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = recv_window

        # 生成签名
        query_string = self._generate_signature(params)
        url = f"{self.base_url}{api_path}"
        headers = {"X-MBX-APIKEY": self.key}

        try:
            if method == "GET":
                resp = requests.get(
                    f"{url}?{query_string}",
                    headers=headers,
                    timeout=30,
                    verify=True,
                )
            elif method == "POST":
                resp = requests.post(
                    url,
                    headers=headers,
                    data=query_string,
                    timeout=180,
                    verify=True,
                )
            elif method == "DELETE":
                resp = requests.delete(
                    f"{url}?{query_string}",
                    headers=headers,
                    timeout=30,
                    verify=True,
                )
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")

            logger.debug(f"{method} {url} → HTTP {resp.status_code}")

            # 解析响应
            try:
                data = resp.json()
            except ValueError:
                error_msg = (
                    f"API 响应非 JSON 格式 | HTTP {resp.status_code} | "
                    f"响应: {resp.text[:300]}"
                )
                logger.error(error_msg)
                self._send_dingding_alert(error_msg)
                return {"code": -999, "msg": "响应解析失败"}

            # 检查错误
            if isinstance(data, dict) and "code" in data:
                err_code = data["code"]
                err_msg = data.get("msg", "")
                logger.error(f"签名请求错误 [{err_code}]: {err_msg}")
                self._send_dingding_alert(
                    f"API 错误 [code={err_code}]: {err_msg}"
                )

            return data

        except BinanceAPIException:
            raise
        except Exception as exc:
            error_msg = f"签名请求异常: {str(exc)}"
            logger.error(error_msg)
            self._send_dingding_alert(error_msg)
            return {"code": -998, "msg": str(exc)}

    def _generate_signature(self, params: dict) -> str:
        """
        生成 **HMAC-SHA256 签名**

        签名算法：
            1. 将参数字典按字母顺序排序
            2. 序列化为 URL 编码的 query string
            3. 使用 API Secret 作为密钥，对 query string 做 HMAC-SHA256
            4. 返回带签名的完整 query string

        :param params: 请求参数（含 timestamp）
        :return:       签名后的 query string（格式: key1=val1&...&signature=xxx）
        """
        data = params.copy()
        query_string = urlencode(data, doseq=True)

        signature = hmac.new(
            self.secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return f"{query_string}&signature={signature}"

    def _build_order_params(
        self,
        symbol: str,
        quantity: float,
        side: str,
        price: float | None = None,
    ) -> dict:
        """
        构建**下单请求参数**

        自动处理数量格式化（根据交易对 stepSize），
        区分市价单（MARKET）和限价单（LIMIT）。

        :param symbol:   交易对
        :param quantity: 下单数量
        :param side:     方向：BUY（买入）/ SELL（卖出）
        :param price:    限价价格（None = 市价单）
        :return:         格式化后的下单参数字典
        """
        params: dict = {
            "symbol": symbol,
            "side": side,
        }

        if price is not None:
            # 限价单
            params["type"] = "LIMIT"
            params["price"] = self.format_price(price)
            params["timeInForce"] = "GTC"
        else:
            # 市价单
            params["type"] = "MARKET"

        # 格式化数量（根据交易对 stepSize 自动适配精度）
        step_size = self.get_step_size(symbol)
        params["quantity"] = self.format_quantity(quantity, step_size)

        return params

    # ============================================================
    # 钉钉报警
    # ============================================================

    def _send_dingding_alert(self, text: str) -> None:
        """
        发送**钉钉机器人报警消息**

        通过 Webhook 推送告警到指定钉钉群。
        发送失败不会抛出异常，仅记录日志。

        :param text: 告警文本内容
        """
        if not dingding_token or dingding_token.startswith("your_"):
            # 未配置 token，跳过
            return

        try:
            headers = {"Content-Type": "application/json;charset=utf-8"}
            api_url = (
                f"https://oapi.dingtalk.com/robot/send"
                f"?access_token={dingding_token}"
            )
            payload = {
                "msgtype": "text",
                "at": {"atMobiles": [], "isAtAll": False},
                "text": {"content": f"【Binance 交易系统告警】\n{text}"},
            }
            resp = requests.post(
                api_url, json.dumps(payload), headers=headers, timeout=10
            )
            result = resp.json()
            if result.get("errcode") != 0:
                logger.warning(f"钉钉消息发送失败: {result.get('errmsg')}")
        except Exception as exc:
            logger.warning(f"钉钉消息发送异常: {exc}")


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 请先修改 authorization.py 中的密钥
    client = BinanceAPI(api_key, api_secret)

    # 测试公开接口
    print("=" * 50)
    print("测试公开接口...")
    print(f"BTC 价格: {client.get_ticker_price('BTCUSDT')}")
    print(f"ETH 价格: {client.get_ticker_price('ETHUSDT')}")

    # 测试 exchangeInfo
    step = client.get_step_size("ETHUSDT")
    print(f"ETHUSDT stepSize: {step}")
    print("=" * 50)
