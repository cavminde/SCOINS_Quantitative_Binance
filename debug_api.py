# -*- coding: utf-8 -*-
"""
Binance API 诊断工具
====================
用于排查 Binance API 连接问题，逐项测试：

    1. API 密钥配置检查
    2. 端点连通性测试 (ping)
    3. 公开接口测试（获取价格、交易对信息）
    4. 签名接口测试（账户信息、下单测试）
    5. 错误诊断和常见问题排查建议

使用方式：
    python debug_api.py              # 全部诊断
    python debug_api.py --public     # 仅测试公开接口
    python debug_api.py --signed     # 仅测试签名接口

注意事项：
    - 签名接口测试会使用测试网（testnet），不会影响真实资产
    - 如果测试网密钥未配置，会跳过签名测试
"""

import sys
import time
import json
import argparse
from datetime import datetime

from app.BinanceAPI import BinanceAPI, BinanceAPIException
from app.authorization import (
    api_key,
    api_secret,
    testnet_api_key,
    testnet_api_secret,
    recv_window,
)


# ============================================================
# 诊断项基类
# ============================================================

class DiagnosticItem:
    """单个诊断项的基类"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.status: str = "⏳"
        self.message: str = ""
        self.duration_ms: float = 0.0

    def run(self) -> bool:
        """执行诊断（子类重写），返回 True 表示通过"""
        raise NotImplementedError


# ============================================================
# 具体诊断项
# ============================================================

def test_key_config() -> bool:
    """检查 API 密钥是否已配置"""
    print("\n" + "─" * 60)
    print("📋 1. API 密钥配置检查")
    print("─" * 60)

    checks = [
        ("主网 API Key", api_key, "your_api_key_here"),
        ("主网 API Secret", api_secret, "your_api_secret_here"),
        ("测试网 API Key", testnet_api_key, "your_testnet_api_key_here"),
        ("测试网 API Secret", testnet_api_secret, "your_testnet_api_secret_here"),
    ]

    all_ok = True
    for name, value, placeholder in checks:
        if not value:
            print(f"  ❌ {name}: 未配置（为空）")
            all_ok = False
        elif value == placeholder:
            print(f"  ⚠️  {name}: 使用默认占位符（请修改为真实密钥）")
        else:
            masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
            print(f"  ✅ {name}: 已配置 ({masked})")

    print(f"  recvWindow: {recv_window}ms")
    return all_ok


def test_ping(client: BinanceAPI) -> bool:
    """测试端点 ping 连通性"""
    print("\n" + "─" * 60)
    print("📶 2. 端点连通性测试 (ping)")
    print("─" * 60)

    try:
        start = time.time()
        result = client.ping()
        elapsed = (time.time() - start) * 1000
        print(f"  ✅ ping 成功 → 响应: {result}, 延迟: {elapsed:.1f}ms")
        return True
    except BinanceAPIException as e:
        print(f"  ❌ ping 失败: [{e.code}] {e.message}")
        return False
    except Exception as e:
        print(f"  ❌ ping 异常: {e}")
        return False


def test_public_api(client: BinanceAPI) -> bool:
    """测试公开接口"""
    print("\n" + "─" * 60)
    print("📊 3. 公开接口测试")
    print("─" * 60)

    ok = True

    # 3.1 获取价格
    test_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    print("  3.1 价格查询:")
    for sym in test_symbols:
        try:
            start = time.time()
            price = client.get_ticker_price(sym)
            elapsed = (time.time() - start) * 1000
            if price:
                print(f"    ✅ {sym}: {price} ({elapsed:.1f}ms)")
            else:
                print(f"    ⚠️  {sym}: 返回空")
                ok = False
        except Exception as e:
            print(f"    ❌ {sym}: {e}")
            ok = False

    # 3.2 24小时行情
    print("  3.2 24小时行情:")
    try:
        data = client.get_ticker_24hour("BTCUSDT")
        if data and "lastPrice" in data:
            print(f"    ✅ BTCUSDT: 最新={data['lastPrice']}, "
                  f"涨跌={data.get('priceChangePercent', 'N/A')}%")
        else:
            print(f"    ⚠️  BTCUSDT 24h 数据异常: {data}")
    except Exception as e:
        print(f"    ❌ BTCUSDT 24h 查询失败: {e}")
        ok = False

    # 3.3 K线数据
    print("  3.3 K线数据:")
    try:
        klines = client.get_klines("BTCUSDT", "1h", 5)
        if klines and len(klines) > 0:
            print(f"    ✅ BTCUSDT 1h K线: 获取到 {len(klines)} 根")
            latest = klines[-1]
            print(f"       最新: 开={latest[1]}, 高={latest[2]}, "
                  f"低={latest[3]}, 收={latest[4]}")
        else:
            print(f"    ⚠️  BTCUSDT K线数据为空")
    except Exception as e:
        print(f"    ❌ BTCUSDT K线查询失败: {e}")
        ok = False

    # 3.4 交易对信息
    print("  3.4 交易对信息:")
    try:
        info = client.get_exchange_info("ETHUSDT")
        symbols = info.get("symbols", [])
        if symbols:
            print(f"    ✅ ETHUSDT exchangeInfo: 获取成功")
            for f in symbols[0].get("filters", []):
                if f["filterType"] in ("LOT_SIZE", "PRICE_FILTER"):
                    name = "数量精度" if f["filterType"] == "LOT_SIZE" else "价格精度"
                    print(f"      {name}: tickSize={f.get('tickSize', 'N/A')}, "
                          f"stepSize={f.get('stepSize', 'N/A')}")
    except Exception as e:
        print(f"    ⚠️  exchangeInfo 查询失败: {e}")

    # 3.5 订单簿深度
    print("  3.5 订单簿深度:")
    try:
        depth = client.get_order_book("BTCUSDT", 5)
        if depth and "bids" in depth:
            print(f"    ✅ BTCUSDT 深度: 买一 {depth['bids'][0]}, "
                  f"卖一 {depth['asks'][0]}")
    except Exception as e:
        print(f"    ⚠️  深度查询失败: {e}")

    return ok


def test_signed_api(client: BinanceAPI) -> bool:
    """测试签名接口"""
    print("\n" + "─" * 60)
    print("🔐 4. 签名接口测试（使用测试网，不影响真实资产）")
    print("─" * 60)

    if not testnet_api_key or testnet_api_key.startswith("your_"):
        print("  ⚠️  测试网密钥未配置，跳过签名测试")
        print("      请在 app/authorization.py 中配置 testnet_api_key 和 testnet_api_secret")
        return False

    ok = True

    # 4.1 账户信息
    print("  4.1 账户信息查询:")
    try:
        account = client.get_account()
        if account and "balances" in account:
            # 显示有余额的币种
            nonzero = [
                b for b in account["balances"]
                if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0
            ]
            print(f"    ✅ 账户查询成功，{len(nonzero)} 个非零余额币种:")
            for b in nonzero[:5]:  # 最多显示 5 个
                print(f"      {b['asset']}: free={b['free']}, locked={b['locked']}")
            if len(nonzero) > 5:
                print(f"      ... 还有 {len(nonzero) - 5} 个")
        elif account and "code" in account:
            print(f"    ❌ 账户查询错误: [{account['code']}] {account.get('msg', '')}")
            ok = False
    except Exception as e:
        print(f"    ❌ 账户查询异常: {e}")
        ok = False

    # 4.2 获取 stepSize
    print("  4.2 交易精度:")
    for sym in ["ETHUSDT", "BTCUSDT"]:
        try:
            step = client.get_step_size(sym)
            price_prec = client.get_price_precision(sym)
            print(f"    ✅ {sym}: stepSize={step}, 价格精度={price_prec}位")
        except Exception as e:
            print(f"    ❌ {sym} 精度查询失败: {e}")

    # 4.3 下单测试 ❗警告：会创建真实测试网订单
    print("  4.3 下单测试:")
    print("    ⚠️  此测试会在测试网下一个小额限价单（不会成交）")
    response = input("    是否继续？(y/N): ")
    if response.lower() == "y":
        try:
            # 下一个价格极低的限价买单（确保不会实际成交）
            btc_price = client.get_ticker_price("BTCUSDT") or 80000
            test_price = float(btc_price) * 0.5  # 用市价的一半，极不可能成交
            test_qty = float(client.format_quantity(0.00001, client.get_step_size("BTCUSDT")))

            print(f"    下测试限价买单: BTCUSDT × {test_qty} @ {test_price}")
            res = client.buy_limit("BTCUSDT", test_qty, test_price)

            if isinstance(res, dict) and "orderId" in res:
                order_id = res["orderId"]
                print(f"    ✅ 测试订单已创建: orderId={order_id}")
                print(f"    ℹ️  请记得手动取消此测试订单")

                # 查询订单状态
                time.sleep(1)
                order_info = client.get_order("BTCUSDT", order_id)
                if order_info:
                    print(f"    订单状态: {order_info.get('status', 'N/A')}")
                    print(f"    订单类型: {order_info.get('type', 'N/A')} "
                          f"{order_info.get('side', 'N/A')}")

                # 取消订单
                cancel_res = client.cancel_order("BTCUSDT", order_id)
                if "orderId" in cancel_res:
                    print(f"    ✅ 测试订单已取消")
            else:
                print(f"    ❌ 下单失败: {res.get('msg', res)}")
                ok = False
        except Exception as e:
            print(f"    ❌ 下单测试异常: {e}")
            ok = False
    else:
        print("    已跳过下单测试")

    return ok


# ============================================================
# 错误诊断建议
# ============================================================

def show_error_guide() -> None:
    """显示常见错误诊断指南"""
    print("\n" + "=" * 60)
    print("🔧 常见问题排查指南")
    print("=" * 60)
    print("""
1. "所有 API 端点均无法连接"
   → 检查网络是否能访问 api.binance.com
   → 尝试 ping api.binance.com
   → 中国大陆用户可能需要科学上网

2. "API 错误码: -2015"
   → API Key 无效或已过期
   → 请重新生成 API Key
   → 检查 API Key 权限是否包含"现货交易"

3. "API 错误码: -1022"
   → 签名错误
   → 检查 API Secret 是否正确
   → 确认系统时间与标准时间偏差不超过 1 秒

4. "签名请求失败"
   → 可能的原因：
     a. IP 不在白名单中（运行 check_ip.py 确认）
     b. API Key 权限不足（需开启"现货交易"和"读取"）
     c. 请求过于频繁（触发频率限制）
     d. 时间戳与服务器时间偏差过大

5. "下单失败 -1013 (LOT_SIZE)"
   → 数量不符合交易对的 stepSize
   → 系统已自动处理精度，若仍失败请检查数量是否小于最小下单量

6. "响应不是 JSON 格式"
   → 网络代理/防火墙干扰了 API 响应
   → 检查是否使用了 HTTP 代理
   → 尝试关闭代理后重试

7. Windows 环境特别提示:
   → 系统时间需与网络时间同步（设置→时间→自动同步）
   → 签名中的时间戳与服务器时间差不能超过 recvWindow
""")


# ============================================================
# 主入口
# ============================================================

def main() -> None:
    """诊断工具主入口"""
    parser = argparse.ArgumentParser(
        description="Binance API 诊断工具",
    )
    parser.add_argument(
        "--public", action="store_true",
        help="仅测试公开接口（无需密钥）",
    )
    parser.add_argument(
        "--signed", action="store_true",
        help="仅测试签名接口（需测试网密钥）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("🔬 Binance API 诊断工具")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_passed = True

    # ── 密钥检查 ──
    if not args.signed:
        test_key_config()

    # ── 创建客户端 ──
    # 公开接口测试用主网
    main_client = BinanceAPI(
        api_key if api_key else "dummy",
        api_secret if api_secret else "dummy",
        testnet=False,
    )

    # ── 公开接口测试 ──
    if not args.signed:
        if test_ping(main_client):
            # 探测最快端点
            try:
                fastest = main_client.get_fastest_endpoint()
                print(f"  ℹ️  最快端点: {fastest}")
            except Exception:
                pass

        test_public_api(main_client)

    # ── 签名接口测试 ──
    if not args.public:
        if testnet_api_key and not testnet_api_key.startswith("your_"):
            test_client = BinanceAPI(
                testnet_api_key,
                testnet_api_secret,
                testnet=True,
            )
            test_signed_api(test_client)
        else:
            print("\n" + "─" * 60)
            print("🔐 4. 签名接口测试")
            print("─" * 60)
            print("  ⚠️  测试网密钥未配置，跳过")
            print("      获取方式: https://testnet.binance.vision/")

    # ── 显示排查指南 ──
    show_error_guide()

    print("\n" + "=" * 60)
    print("诊断完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
