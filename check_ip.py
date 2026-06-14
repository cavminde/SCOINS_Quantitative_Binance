# -*- coding: utf-8 -*-
import requests

def get_public_ip():
    """获取当前公网IP地址"""
    print("正在检测当前公网IP地址...")
    
    ip_services = [
        "https://api.ipify.org",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
        "https://api.myip.com",
        "https://ip.seeip.org"
    ]
    
    for service in ip_services:
        try:
            response = requests.get(service, timeout=10)
            if response.status_code == 200:
                ip = response.text.strip()
                if ip:
                    return ip
        except Exception as e:
            print(f"  尝试 {service} 失败: {e}")
    
    return None

def check_binance_ip_restriction():
    """检查Binance IP白名单设置建议"""
    print("\n" + "=" * 60)
    print("Binance API IP白名单设置建议")
    print("=" * 60)
    print("1. 如果你的API Key设置了IP白名单：")
    print("   - 登录 https://www.binance.com/en/my/settings/api-management")
    print("   - 找到对应的API Key")
    print("   - 在 'IP Access Restriction' 中添加当前IP")
    print("   - 或者选择 'Unrestricted' 取消IP限制")
    print("\n2. 如果没有设置IP白名单：")
    print("   - 建议启用IP白名单提高安全性")
    print("   - 只允许你信任的IP地址访问API")
    print("\n3. 常见问题：")
    print("   - 如果使用代理/VPN，IP可能会变化")
    print("   - 某些网络环境可能使用共享IP")
    print("   - 重启路由器可能会改变公网IP")
    print("=" * 60)

def main():
    print("=" * 60)
    print("IP地址检测工具")
    print("=" * 60)
    
    ip = get_public_ip()
    
    if ip:
        print(f"\n✅ 当前公网IP地址: {ip}")
        print(f"\n请确保此IP已添加到Binance API Key的IP白名单中")
        check_binance_ip_restriction()
    else:
        print("\n❌ 无法获取公网IP地址")
        print("请检查网络连接或尝试其他网络")

if __name__ == "__main__":
    main()