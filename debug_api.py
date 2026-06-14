# -*- coding: utf-8 -*-
from app.BinanceAPI import BinanceAPI
from app.authorization import api_key, api_secret, testnet_api_key, testnet_api_secret, recv_window
import requests
import time
import hmac
import hashlib
from urllib.parse import urlencode

def test_ping(binan):
    """测试连接"""
    print("\n--- 测试连接 (ping) ---")
    try:
        result = binan.ping()
        print(f"✅ ping成功: {result}")
        return True
    except Exception as e:
        print(f"❌ ping失败: {e}")
        return False

def test_public_endpoint(binan):
    """测试公开接口（无需签名）"""
    print("\n--- 测试公开接口 (获取价格) ---")
    try:
        price = binan.get_ticker_price("BTCUSDT")
        print(f"✅ 获取价格成功: BTCUSDT = {price}")
        return True
    except Exception as e:
        print(f"❌ 获取价格失败: {e}")
        return False

def test_exchange_info(binan):
    """测试获取交易对信息"""
    print("\n--- 测试获取交易对信息 ---")
    try:
        path = f"{binan._get_current_endpoint()}/exchangeInfo"
        result = binan._get_no_sign(path, {})
        if isinstance(result, dict) and 'symbols' in result:
            print(f"✅ 获取成功，共 {len(result['symbols'])} 个交易对")
            # 查找 ETHUSDT 的 stepSize
            for symbol in result['symbols']:
                if symbol['symbol'] == 'ETHUSDT':
                    for f in symbol['filters']:
                        if f['filterType'] == 'LOT_SIZE':
                            print(f"   ETHUSDT LOT_SIZE: minQty={f['minQty']}, maxQty={f['maxQty']}, stepSize={f['stepSize']}")
                    break
            return True
        else:
            print(f"❌ 获取失败: {result}")
            return False
    except Exception as e:
        print(f"❌ 获取失败: {e}")
        return False

def test_signed_request_manual():
    """手动测试签名请求"""
    print("\n--- 手动测试签名请求 ---")
    
    use_testnet = True
    
    if use_testnet:
        base_url = "https://testnet.binance.vision/api/v3"
        key = testnet_api_key
        secret = testnet_api_secret
    else:
        base_url = "https://api.binance.com/api/v3"
        key = api_key
        secret = api_secret
    
    if not key or not secret:
        print("❌ API密钥为空")
        return False
    
    print(f"使用环境: {'测试网' if use_testnet else '主网'}")
    print(f"API Key: {key[:10]}...")
    
    # 构建参数
    params = {
        "timestamp": int(time.time() * 1000),
        "recvWindow": recv_window
    }
    
    # 生成签名
    query_string = urlencode(params, doseq=True)
    signature = hmac.new(
        secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    full_url = f"{base_url}/account?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": key}
    
    print(f"请求URL: {full_url}")
    print(f"签名: {signature[:20]}...")
    
    try:
        response = requests.get(full_url, headers=headers, verify=True, timeout=30)
        print(f"HTTP状态码: {response.status_code}")
        print(f"响应内容: {response.text[:500]}")
        
        if response.status_code == 200:
            print("✅ 签名请求成功!")
            return True
        elif response.status_code == 401:
            print("❌ 认证失败 - 请检查API密钥和权限")
            return False
        else:
            print(f"❌ 请求失败: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 请求异常: {e}")
        return False

def main():
    print("=" * 60)
    print("Binance API 诊断工具")
    print("=" * 60)
    
    # 检查密钥配置
    print("\n--- 密钥配置检查 ---")
    print(f"主网 API Key: {'已配置' if api_key else '未配置'}")
    print(f"主网 API Secret: {'已配置' if api_secret else '未配置'}")
    print(f"测试网 API Key: {'已配置' if testnet_api_key else '未配置'}")
    print(f"测试网 API Secret: {'已配置' if testnet_api_secret else '未配置'}")
    
    # 创建测试网实例
    if testnet_api_key and testnet_api_secret:
        binan = BinanceAPI(testnet_api_key, testnet_api_secret, testnet=True)
        
        test_ping(binan)
        test_public_endpoint(binan)
        test_exchange_info(binan)
    
    # 手动测试签名
    test_signed_request_manual()
    
    print("\n" + "=" * 60)
    print("常见问题排查:")
    print("1. 确保在 https://testnet.binance.vision/ 使用GitHub登录")
    print("2. 确保生成了正确的 API Key 和 Secret")
    print("3. 如果设置了IP白名单，请添加当前IP")
    print("4. 确保API Key具有现货交易权限")
    print("5. 测试网需要单独领取测试资金")
    print("=" * 60)

if __name__ == "__main__":
    main()