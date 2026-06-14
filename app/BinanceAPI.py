# -*- coding: utf-8 -*
import requests, time, hmac, hashlib,json,os,math
from app.authorization import dingding_token, recv_window,api_secret,api_key
# from app.dingding import Message
# linux
data_path = os.getcwd()+"/data/data.json"
# windows
# data_path = os.getcwd() + "\data\data.json"
try:
    from urllib import urlencode
# python3
except ImportError:
    from urllib.parse import urlencode

class BinanceAPI(object):
    BASE_URL = "https://www.binance.com/api/v1"
    FUTURE_URL = "https://fapi.binance.com"
    BASE_URL_V3 = "https://api.binance.com/api/v3"
    PUBLIC_URL = "https://www.binance.com/exchange/public/product"
    
    BASE_URL_V3_BACKUP = "https://api1.binance.com/api/v3"
    BASE_URL_V3_BACKUP2 = "https://api2.binance.com/api/v3"
    BASE_URL_V3_BACKUP3 = "https://api3.binance.com/api/v3"
    
    ALPHA_BASE_URL = "https://www.binance.com/bapi/defi/v1/public"
    ALPHA_TOKEN_LIST_URL = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"

    def __init__(self, key, secret, testnet=False):
        self.key = key
        self.secret = secret
        self.testnet = testnet
        self.current_api_index = 0
        
        if testnet:
            self.api_endpoints = [
                "https://testnet.binance.vision/api/v3",
                "https://testnet.binance.vision/api/v3"
            ]
        else:
            self.api_endpoints = [self.BASE_URL_V3, self.BASE_URL_V3_BACKUP, self.BASE_URL_V3_BACKUP2, self.BASE_URL_V3_BACKUP3]

    def _get_current_endpoint(self):
        return self.api_endpoints[self.current_api_index]

    def _rotate_endpoint(self):
        self.current_api_index = (self.current_api_index + 1) % len(self.api_endpoints)
        return self._get_current_endpoint()

    def ping(self):
        for _ in range(len(self.api_endpoints)):
            try:
                path = "%s/ping" % self._get_current_endpoint()
                res = requests.get(path, timeout=10, verify=True)
                if res.status_code == 200:
                    return res.json()
            except Exception as e:
                self._rotate_endpoint()
                continue
        raise Exception("所有Binance API端点均无法连接")

    def get_ticker_price(self, market):
        return self._retry_request('/ticker/price', {"symbol": market})

    def _retry_request(self, api_path, params={}, method='GET'):
        original_index = self.current_api_index
        
        for _ in range(len(self.api_endpoints)):
            try:
                url = self._get_current_endpoint() + api_path
                query = urlencode(params)
                full_url = f"{url}?{query}"
                
                response = requests.get(full_url, timeout=10, verify=True)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict) and 'code' in data:
                        error_code = data['code']
                        error_msg = data.get('msg', str(data))
                        print(f"API错误码: {error_code}")
                        raise Exception(f"API错误码: {error_code}, 错误信息: {error_msg}")
                    return data['price'] if 'price' in data else data
            except Exception as e:
                self.current_api_index = original_index
                raise e
        
        self.current_api_index = original_index
        raise Exception("所有API端点均无法连接")

    def get_ticker_24hour(self,market):
        path = "%s/ticker/24hr" % self.BASE_URL_V3
        params = {"symbol":market}
        res =  self._get_no_sign(path,params)
        return res

    def get_klines(self, market, interval, limit,startTime=None, endTime=None,rotate_count = 0):
        path = "%s/klines" % self.BASE_URL
        params = None
        if startTime is None:
            params = {"symbol": market, "interval":interval, "limit":limit}
        else:
            params = {"symbol": market,"limit":limit, "interval":interval, "startTime":startTime, "endTime":endTime}
        res =  self._get_no_sign(path, params)
        return res

    def buy_limit(self, market, quantity, rate):
        path = "%s/order" % self._get_current_endpoint()
        params = self._order(market, quantity, "BUY", rate)
        return self._post(path, params)

    def sell_limit(self, market, quantity, rate):
        path = "%s/order" % self._get_current_endpoint()
        params = self._order(market, quantity, "SELL", rate)
        return self._post(path, params)

    def buy_market(self, market, quantity):
        path = "%s/order" % self._get_current_endpoint()
        params = self._order(market, quantity, "BUY")
        return self._post(path, params)

    def sell_market(self, market, quantity):
        path = "%s/order" % self._get_current_endpoint()
        params = self._order(market, quantity, "SELL")
        return self._post(path, params)
    
    def get_ticker_24hour(self,market):
        path = "%s/ticker/24hr" % self.BASE_URL
        params = {"symbol":market}
        res =  self._get_no_sign(path,params)
        return round(float(res['priceChangePercent']),1)
    
    def get_positionInfo(self, symbol):
        '''当前持仓交易对信息'''
        path = "%s/positionRisk" % self.BASE_URL
        params = {"symbol":symbol}
        time.sleep(1)
        return self._get(path, params)

    def get_future_positionInfo(self, symbol):
        '''当前期货持仓交易对信息'''
        path = "%s/fapi/v2/positionRisk" % self.FUTURE_URL
        params = {"symbol":symbol}
        res = self._get(path, params)
        print(res)
        return res

    def dingding_warn(self,text):
        headers = {'Content-Type': 'application/json;charset=utf-8'}
        api_url = "https://oapi.dingtalk.com/robot/send?access_token=%s" % dingding_token
        json_text = json_text = {
            "msgtype": "text",
            "at": {
                "atMobiles": [
                    "11111"
                ],
                "isAtAll": False
            },
            "text": {
                "content": text
            }
        }
        requests.post(api_url, json.dumps(json_text), headers=headers).content
    def get_cointype(self):
        '''读取json文件'''
        tmp_json = {}
        with open(data_path, 'r') as f:
            tmp_json = json.load(f)
            f.close()
        return tmp_json["config"]["cointype"]
    def _get_step_size(self, market):
        '''获取交易对的stepSize（数量步长）'''
        try:
            path = "%s/exchangeInfo" % self.BASE_URL_V3
            params = {"symbol": market}
            res = self._get_no_sign(path, params)
            if isinstance(res, dict) and 'code' in res:
                # 如果API调用失败，使用默认值6位小数
                return "0.000001"
            for f in res.get('filters', []):
                if f.get('filterType') == 'LOT_SIZE':
                    return f.get('stepSize', '0.000001')
            return "0.000001"
        except Exception as e:
            return "0.000001"

    def _format_quantity_by_step(self, quantity, step_size):
        '''根据stepSize格式化数量'''
        try:
            step = float(step_size)
            # 计算精度：小数位数
            precision = int(round(-math.log(step, 10), 0))
            precision = max(0, precision)  # 确保不为负数
            return f"{round(quantity, precision):.{precision}f}"
        except (ValueError, TypeError):
            # 出错时使用6位小数
            return '%.6f' % quantity

    ### ----私有函数---- ###
    def _order(self, market, quantity, side, price=None):
        '''
        :param market:币种类型。如：BTCUSDT、ETHUSDT
        :param quantity: 购买量
        :param side: 订单方向，买还是卖
        :param price: 价格
        :return:
        '''
        params = {}

        if price is not None:
            params["type"] = "LIMIT"
            params["price"] = self._format(price)
            params["timeInForce"] = "GTC"
        else:
            params["type"] = "MARKET"

        params["symbol"] = market
        params["side"] = side
        # 根据交易对的stepSize自动调整数量精度
        step_size = self._get_step_size(market)
        params["quantity"] = self._format_quantity_by_step(quantity, step_size)

        return params

    def _get(self, path, params={}):
        params.update({"recvWindow": recv_window})
        query_string = self._sign(params)
        url = "%s?%s" % (path, query_string)
        header = {"X-MBX-APIKEY": self.key}
        print(f"GET请求URL: {url}")
        res = requests.get(url, headers=header,timeout=30, verify=True).json()
        if isinstance(res,dict):
            if 'code' in res:
                error_info = "报警：做多网格,请求异常.错误原因{info}".format(info=str(res))
                self.dingding_warn(error_info)
        return res

    def _get_no_sign(self, path, params={}):
        query = urlencode(params)
        url = "%s?%s" % (path, query)
        
        try:
            res = requests.get(url, timeout=10, verify=True).json()
            if isinstance(res, dict) and 'code' in res:
                error_info = "报警：做多网格,请求异常.错误原因{info}".format(info=str(res))
                self.dingding_warn(error_info)
            return res
        except Exception as e:
            if str(e).find("443") != -1:
                return 443
            raise e

    def _sign(self, params={}):
        data = params.copy()

        ts = int(1000 * time.time())
        data.update({"timestamp": ts})
        query_string = urlencode(data, doseq=True)
        query_string = query_string.replace('%27', '%22')
        signature = hmac.new(
            self.secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"{query_string}&signature={signature}"

    def _post(self, path, params={}):
        params.update({"recvWindow": recv_window})
        query_string = self._sign(params)
        url = "%s" % (path)
        header = {"X-MBX-APIKEY": self.key}
        
        try:
            response = requests.post(url, headers=header, data=query_string, timeout=180, verify=True)
            print(f"POST请求URL: {url}")
            print(f"POST请求参数: {query_string}")
            print(f"HTTP状态码: {response.status_code}")
            print(f"响应内容: {response.text[:500]}")
            
            try:
                res = response.json()
            except ValueError:
                # 响应不是JSON格式
                error_info = f"报警：API响应不是JSON格式.状态码: {response.status_code}, 响应: {response.text[:200]}"
                self.dingding_warn(error_info)
                return {"code": -999, "msg": "响应不是JSON格式"}
            
            if isinstance(res, dict):
                if 'code' in res:
                    error_info = "报警：做多网格,请求异常.错误原因{info}".format(info=str(res))
                    self.dingding_warn(error_info)
            
            return res
        except Exception as e:
            error_info = f"报警：POST请求异常.错误原因: {str(e)}"
            self.dingding_warn(error_info)
            return {"code": -998, "msg": str(e)}

    def _format(self, price):
        return "{:.6f}".format(price)

    def is_alpha_token(self, symbol):
        '''判断是否为Alpha代币'''
        return symbol.startswith("ALPHA_")
    
    def get_alpha_token_list(self):
        '''获取所有Alpha代币列表'''
        try:
            response = requests.get(self.ALPHA_TOKEN_LIST_URL, timeout=10, verify=True)
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return data.get('data', [])
                else:
                    raise Exception(f"API错误: {data.get('message', '未知错误')}")
            else:
                raise Exception(f"HTTP {response.status_code}")
        except Exception as e:
            self.dingding_warn(f"获取Alpha代币列表失败: {str(e)}")
            return []
    
    def get_alpha_ticker_price(self, alpha_id):
        '''获取Alpha代币当前价格'''
        url = f"{self.ALPHA_BASE_URL}/alpha-trade/ticker"
        params = {"symbol": f"{alpha_id}USDT"}
        try:
            response = requests.get(url, params=params, timeout=10, verify=True)
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return float(data.get('data', {}).get('lastPrice', '0'))
                else:
                    raise Exception(f"API错误: {data.get('message', '未知错误')}")
            else:
                raise Exception(f"HTTP {response.status_code}")
        except Exception as e:
            self.dingding_warn(f"获取Alpha代币价格失败: {str(e)}")
            return None
    
    def get_alpha_klines(self, alpha_id, interval, limit=100, startTime=None, endTime=None):
        '''获取Alpha代币K线数据'''
        url = f"{self.ALPHA_BASE_URL}/alpha-trade/klines"
        params = {
            "symbol": f"{alpha_id}USDT",
            "interval": interval,
            "limit": limit
        }
        if startTime:
            params["startTime"] = startTime
        if endTime:
            params["endTime"] = endTime
        
        try:
            response = requests.get(url, params=params, timeout=10, verify=True)
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return data.get('data', [])
                else:
                    raise Exception(f"API错误: {data.get('message', '未知错误')}")
            else:
                raise Exception(f"HTTP {response.status_code}")
        except Exception as e:
            self.dingding_warn(f"获取Alpha代币K线失败: {str(e)}")
            return []

if __name__ == "__main__":
    instance = BinanceAPI(api_key,api_secret)
    # print(instance.buy_limit("EOSUSDT",5,2))
    # print(instance.get_ticker_price("WINGUSDT"))
    print(instance.get_ticker_24hour("WINGUSDT"))
