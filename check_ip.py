# -*- coding: utf-8 -*-
"""
IP 地址检测工具
===============
检测当前公网 IP 地址，辅助配置 Binance API 的 IP 白名单。

Binance API 支持 IP 白名单访问限制，启用后只有白名单中的 IP
才能使用该 API Key 进行交易。本工具帮助您确认当前公网 IP。

使用方式：
    python check_ip.py

输出：
    当前公网 IP → 用于添加到 Binance API 管理后台的白名单中
"""

import requests
import sys
from typing import Optional

# ============================================================
# 公网 IP 检测服务列表
# 按优先级排列，如果前面的服务失败则尝试下一个
# ============================================================
IP_SERVICES: list[str] = [
    "https://api.ipify.org",        # 最稳定
    "https://icanhazip.com",        # 老牌服务
    "https://ifconfig.me/ip",       # 返回纯文本 IP
    "https://api.myip.com",         # 返回 JSON（含国家和 IP）
    "https://ip.seeip.org",         # 备选
    "https://checkip.amazonaws.com", # AWS 提供
]


def get_public_ip(timeout: int = 10) -> Optional[str]:
    """
    通过多个公共服务检测当前公网 IP

    依次尝试多个 IP 检测服务，返回第一个成功获取到的 IP。

    :param timeout: 单个请求超时时间（秒）
    :return:        公网 IP 地址字符串，全部失败返回 None
    """
    print("🔍 正在检测当前公网 IP 地址...")
    print("-" * 50)

    for i, service in enumerate(IP_SERVICES, 1):
        try:
            print(f"  [{i}/{len(IP_SERVICES)}] 尝试 {service} ... ", end="")
            response = requests.get(service, timeout=timeout)

            if response.status_code == 200:
                # 某些服务返回 JSON 格式
                ip = response.text.strip()
                if service == "https://api.myip.com":
                    # 该服务返回 {"ip":"x.x.x.x","country":"..."}
                    import json
                    data = response.json()
                    ip = data.get("ip", "").strip()
                    country = data.get("country", "未知")
                    print(f"✅ {ip} (来自 {country})")
                else:
                    print(f"✅ {ip}")
                return ip
            else:
                print(f"❌ HTTP {response.status_code}")

        except Exception as exc:
            print(f"❌ 失败 ({exc})")

    return None


def show_ip_whitelist_guide(ip: str) -> None:
    """
    显示 Binance IP 白名单配置指南

    :param ip: 检测到的公网 IP
    """
    print("\n" + "=" * 60)
    print("📋 Binance API IP 白名单配置指南")
    print("=" * 60)
    print(f"""
检测到的公网 IP: {ip}

配置步骤：
    1. 登录 Binance 官网 → 账户 → API 管理
       https://www.binance.com/zh-CN/my/settings/api-management
    2. 找到对应的 API Key，点击「编辑」
    3. 在「IP 访问限制」中：
       - 选择「仅限受信任的 IP」
       - 添加此 IP: {ip}
    4. 保存设置

注意事项：
    • 如果使用 VPN/代理，IP 可能会变化
    • 家庭宽带的公网 IP 通常会在重启路由器后改变
    • 服务器（如阿里云/腾讯云）的 IP 通常固定不变
    • 建议同时添加多个常用 IP（办公、家庭、手机热点）
    • 测试网 (testnet.binance.vision) 通常不需要 IP 白名单
    """)


def main() -> None:
    """主入口"""
    print("=" * 60)
    print("🔐 Binance API IP 白名单检测工具")
    print("=" * 60)
    print()

    ip = get_public_ip()

    if ip:
        print(f"\n✅ 当前公网 IP: {ip}")
        show_ip_whitelist_guide(ip)

        # 验证 IP 格式
        if not ip.replace(".", "").isdigit():
            print("⚠️  警告：检测到的 IP 格式似乎不是标准 IPv4 地址")
    else:
        print("\n❌ 无法获取公网 IP 地址")
        print("   可能的原因：")
        print("   1. 当前网络无法访问外网")
        print("   2. 防火墙/代理阻止了请求")
        print("   3. DNS 解析问题")
        print("\n   建议：")
        print("   - 检查网络连接")
        print("   - 手动访问 https://www.whatismyip.com 查看 IP")
        sys.exit(1)


if __name__ == "__main__":
    main()
