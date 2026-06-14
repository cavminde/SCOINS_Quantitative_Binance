# -*- coding: utf-8 -*-
from app.BinanceAPI import BinanceAPI
from app.authorization import api_key,api_secret
from data.runBetData import RunBetData
from app.dingding import Message
from data.calcIndex import CalcIndex
import time
import tkinter as tk
from tkinter import ttk
import threading
import json
import os

binan = BinanceAPI(api_key,api_secret)
runbet = RunBetData()
msg = Message()

index = CalcIndex()

class IntegratedMonitor:
    '''集成监控窗口 - 与交易系统同步运行'''
    
    def __init__(self, root, trade_instance):
        self.root = root
        self.trade_instance = trade_instance
        self.root.title("Alpha网格交易监控")
        self.root.geometry("1000x700")
        
        self.monitoring = False
        self.monitor_thread = None
        
        self.load_config()
        self.create_widgets()
    
    def load_config(self):
        '''加载data.json配置'''
        data_path = os.getcwd() + "/data/data.json"
        try:
            with open(data_path, 'r') as f:
                self.config = json.load(f)
                self.coin_list = self.config.get('coinList', [])
        except Exception as e:
            self.config = {}
            self.coin_list = []
    
    def create_widgets(self):
        # 标题栏
        title_frame = ttk.Frame(self.root)
        title_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(title_frame, text="Alpha网格交易实时监控系统", 
                  font=('Arial', 18, 'bold')).pack()
        
        # 控制面板
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.btn_start = ttk.Button(control_frame, text="启动交易", 
                                     command=self.start_trade, width=15)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        
        self.btn_stop = ttk.Button(control_frame, text="停止交易", 
                                    command=self.stop_trade, state=tk.DISABLED, width=15)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(control_frame, text="刷新配置", 
                   command=self.refresh_config, width=15).pack(side=tk.LEFT, padx=5)
        
        # 状态显示
        self.status_var = tk.StringVar()
        self.status_var.set("状态: 就绪")
        ttk.Label(control_frame, textvariable=self.status_var, 
                  font=('Arial', 12, 'bold'), foreground='blue').pack(side=tk.RIGHT, padx=20)
        
        # 主监控表格
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        columns = ('交易对', '类型', '当前价格', '买入价', '卖出价', '交易状态', '步数', '建议')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                  yscrollcommand=scrollbar.set, height=12)
        
        scrollbar.config(command=self.tree.yview)
        
        col_widths = {'交易对': 100, '类型': 80, '当前价格': 120, 
                      '买入价': 120, '卖出价': 120, '交易状态': 100, 
                      '步数': 60, '建议': 180}
        
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths.get(col, 100), anchor='center')
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 配置颜色标签
        self.tree.tag_configure('buy', background='#90EE90')  # 浅绿色
        self.tree.tag_configure('sell', background='#FFB6C1')  # 浅红色
        self.tree.tag_configure('watch', background='#FFFACD')  # 浅黄色
        self.tree.tag_configure('trading', background='#ADD8E6')  # 浅蓝色
        
        # 日志区域
        log_frame = ttk.LabelFrame(self.root, text="交易日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=5)
        
        self.log_text = tk.Text(log_frame, height=6, font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        scrollbar_log = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar_log.set)
        
        self.populate_tree()
        self.log("监控系统初始化完成")
        self.log(f"加载 {len(self.coin_list)} 个交易对配置")
    
    def populate_tree(self):
        '''填充交易对表格'''
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for coin in self.coin_list:
            if coin in self.config:
                coin_config = self.config[coin]
                is_alpha = coin.startswith("ALPHA_")
                coin_type = "Alpha" if is_alpha else "现货"
                
                self.tree.insert('', tk.END, values=(
                    coin,
                    coin_type,
                    "---",
                    "---",
                    "---",
                    "等待",
                    "---",
                    "---"
                ))
    
    def start_trade(self):
        '''启动交易系统'''
        if self.monitoring:
            return
        
        self.monitoring = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.status_var.set("状态: 交易中...")
        
        # 启动监控线程
        self.monitor_thread = threading.Thread(target=self.monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
        # 启动交易线程
        self.trade_thread = threading.Thread(target=self.trade_loop)
        self.trade_thread.daemon = True
        self.trade_thread.start()
        
        self.log("交易系统已启动")
    
    def stop_trade(self):
        '''停止交易系统'''
        self.monitoring = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.status_var.set("状态: 已停止")
        self.log("交易系统已停止")
    
    def trade_loop(self):
        '''交易循环'''
        try:
            while self.monitoring:
                for coinType in self.trade_instance.coinList:
                    if not self.monitoring:
                        break
                    
                    try:
                        [grid_buy_price, grid_sell_price, quantity, step, 
                         cur_market_price, right_size] = self.trade_instance.pre_data(coinType)
                        
                        if cur_market_price is None:
                            self.log(f"无法获取 {coinType} 价格，跳过")
                            time.sleep(1)
                            continue
                        
                        stop_loss_triggered, stop_loss_price = runbet.check_stop_loss(coinType, cur_market_price)
                        if stop_loss_triggered and step > 0:
                            self.log(f"触发止损！{coinType}: {cur_market_price:.6f}")
                            last_price = runbet.get_record_price(coinType)
                            sell_amount = runbet.get_quantity(coinType, False)
                            porfit_usdt = (cur_market_price - last_price) * sell_amount
                            res = msg.sell_market_msg(coinType, sell_amount, porfit_usdt)
                            if 'orderId' in res:
                                self.log(f"止损成功: {coinType}")
                            else:
                                self.log(f"止损失败: {coinType}")
                            continue
                        
                        if grid_buy_price >= cur_market_price:
                            self.log(f"买入信号: {coinType}")
                            order_symbol = f"{coinType}USDT" if coinType.startswith("ALPHA_") else coinType
                            res = msg.buy_market_msg(order_symbol, quantity)
                            if 'orderId' in res:
                                success_price = float(res['fills'][0]['price'])
                                runbet.set_ratio(coinType)
                                runbet.set_record_price(coinType, success_price)
                                runbet.modify_price(coinType, cur_market_price, step+1, cur_market_price)
                                self.log(f"买入成功: {coinType} @ {success_price:.6f}")
                            time.sleep(1)
                        
                        elif grid_sell_price < cur_market_price:
                            if step > 0:
                                self.log(f"卖出信号: {coinType}")
                                last_price = runbet.get_record_price(coinType)
                                sell_amount = runbet.get_quantity(coinType, False)
                                porfit_usdt = (cur_market_price - last_price) * sell_amount
                                order_symbol = f"{coinType}USDT" if coinType.startswith("ALPHA_") else coinType
                                res = msg.sell_market_msg(order_symbol, sell_amount, porfit_usdt)
                                if 'orderId' in res:
                                    runbet.set_ratio(coinType)
                                    runbet.modify_price(coinType, runbet.get_record_price(coinType), step - 1, cur_market_price)
                                    runbet.remove_record_price(coinType)
                                    self.log(f"卖出成功: {coinType}, 盈利: {porfit_usdt:.2f} USDT")
                            else:
                                runbet.modify_price(coinType, grid_sell_price, step, cur_market_price)
                        
                        time.sleep(1)
                        
                    except Exception as e:
                        self.log(f"交易异常 {coinType}: {str(e)}")
                        time.sleep(1)
                        
        except Exception as e:
            self.log(f"交易线程异常: {str(e)}")
    
    def monitor_loop(self):
        '''价格监控循环'''
        while self.monitoring:
            try:
                for coin in self.coin_list:
                    if not self.monitoring:
                        break
                    
                    # 获取价格
                    try:
                        if coin.startswith("ALPHA_"):
                            price = binan.get_alpha_ticker_price(coin)
                        else:
                            price = binan.get_ticker_price(coin)
                    except:
                        price = None
                    
                    # 检查交易条件
                    condition = "观望"
                    suggestion = "等待..."
                    buy_price = sell_price = step = 0
                    
                    if coin in self.config and price:
                        config = self.config[coin]
                        run_bet = config.get('runBet', {})
                        buy_price = float(run_bet.get('next_buy_price', 0))
                        sell_price = float(run_bet.get('grid_sell_price', 0))
                        step = run_bet.get('step', 0)
                        
                        try:
                            price_float = float(price)
                            if price_float <= buy_price:
                                condition = "买入"
                                suggestion = f"✓ 满足买入条件"
                            elif price_float >= sell_price:
                                condition = "卖出"
                                suggestion = f"✓ 满足卖出条件"
                            else:
                                condition = "观望"
                                suggestion = f"区间内，等待..."
                        except (ValueError, TypeError):
                            condition = "观望"
                            suggestion = "价格格式错误"
                    
                    # 更新UI
                    self.root.after(0, lambda c=coin, p=price, 
                                   con=condition, sug=suggestion, 
                                   bp=buy_price, sp=sell_price, 
                                   st=step: self.update_item(c, p, con, sug, bp, sp, st))
                
                time.sleep(1)  # 每1秒更新
                
            except Exception as e:
                self.log(f"监控异常: {str(e)}")
                time.sleep(1)
    
    def update_item(self, coin, price, condition, suggestion, buy_price, sell_price, step):
        '''更新表格行'''
        for item in self.tree.get_children():
            values = self.tree.item(item, 'values')
            if values[0] == coin:
                # 转换价格为浮点数
                try:
                    price_float = float(price)
                    price_str = f"{price_float:.6f}"
                except (ValueError, TypeError):
                    price_str = "错误"
                
                buy_str = f"{buy_price:.6f}" if buy_price else "---"
                sell_str = f"{sell_price:.6f}" if sell_price else "---"
                step_str = str(step) if step else "---"
                
                self.tree.item(item, values=(
                    coin, values[1], price_str, buy_str, sell_str,
                    condition, step_str, suggestion
                ))
                
                # 设置颜色
                if condition == "买入":
                    self.tree.item(item, tags=('buy',))
                elif condition == "卖出":
                    self.tree.item(item, tags=('sell',))
                elif condition == "观望":
                    self.tree.item(item, tags=('watch',))
                break
    
    def refresh_config(self):
        '''刷新配置'''
        self.load_config()
        self.populate_tree()
        self.log(f"配置已刷新，共 {len(self.coin_list)} 个交易对")
    
    def log(self, message):
        '''添加日志'''
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)


class Run_Main():

    def __init__(self):
        self.coinList = runbet.get_coinList()
        pass

    def pre_data(self,cointype):
        '''获取交易对的data.json基础信息
            cointype:交易对
        '''
        grid_buy_price = float(runbet.get_buy_price(cointype))  # 当前网格买入价格
        grid_sell_price = float(runbet.get_sell_price(cointype))  # 当前网格卖出价格
        quantity = runbet.get_quantity(cointype)  # 买入量
        step = runbet.get_step(cointype)  # 当前步数
        
        if cointype.startswith("ALPHA_"):
            cur_market_price = binan.get_alpha_ticker_price(cointype)  # Alpha代币当前价格
        else:
            cur_market_price = binan.get_ticker_price(cointype)  # 当前交易对市价
        
        # 转换为浮点数
        try:
            cur_market_price = float(cur_market_price) if cur_market_price else None
        except (ValueError, TypeError):
            cur_market_price = None
        
        if cur_market_price is None:
            right_size = 6
        else:
            right_size = len(str(cur_market_price).split(".")[1]) if '.' in str(cur_market_price) else 0
        
        return [grid_buy_price, grid_sell_price, quantity, step, cur_market_price, right_size]
    def loop_run(self):
        while True:

            for coinType in self.coinList:
                [grid_buy_price,grid_sell_price,quantity,step,cur_market_price,right_size] = self.pre_data(coinType)

                stop_loss_triggered, stop_loss_price = runbet.check_stop_loss(coinType, cur_market_price)
                if stop_loss_triggered and step > 0:
                    print(f"触发止损！币种:{coinType}, 当前价格:{cur_market_price}, 止损价格:{stop_loss_price}")
                    last_price = runbet.get_record_price(coinType)
                    sell_amount = runbet.get_quantity(coinType, False)
                    porfit_usdt = (cur_market_price - last_price) * sell_amount
                    res = msg.sell_market_msg(coinType, sell_amount, porfit_usdt)
                    if 'orderId' in res:
                        print(f"止损卖出成功，程序停止运行")
                        msg.dingding_warn(f"【止损报警】币种:{coinType}触发止损，已全部卖出，程序停止运行")
                        return
                    else:
                        print(f"止损卖出失败，程序停止运行")
                        msg.dingding_warn(f"【止损报警】币种:{coinType}触发止损，但卖出失败，程序停止运行")
                        return

                if grid_buy_price >= cur_market_price:#and index.calcAngle(coinType,"5m",False,right_size):   # 是否满足买入价
                    res = msg.buy_market_msg(coinType, quantity)
                    if 'orderId' in res: # 挂单成功
                        success_price = float(res['fills'][0]['price'])
                        runbet.set_ratio(coinType)
                        time.sleep(1)
                        runbet.set_record_price(coinType,success_price)
                        time.sleep(1)
                        runbet.modify_price(coinType,cur_market_price, step+1,cur_market_price) #修改data.json中价格、当前步数
                        time.sleep(60*2) # 挂单后，停止运行1分钟
                    else:
                        break

                elif grid_sell_price < cur_market_price :#and index.calcAngle(coinType,"5m",True,right_size):  # 是否满足卖出价
                    if step==0: # setp=0 防止踏空，跟随价格上涨
                        runbet.modify_price(coinType,grid_sell_price,step,cur_market_price)
                    else:
                        last_price = runbet.get_record_price(coinType)
                        sell_amount = runbet.get_quantity(coinType,False)
                        porfit_usdt = (cur_market_price - last_price) * sell_amount
                        res = msg.sell_market_msg(coinType, runbet.get_quantity(coinType,False),porfit_usdt)
                        if 'orderId' in res: #True 代表下单成功
                            runbet.set_ratio(coinType) #启动动态改变比率
                            time.sleep(1)
                            runbet.modify_price(coinType,runbet.get_record_price(coinType), step - 1,cur_market_price)
                            time.sleep(1)
                            runbet.remove_record_price(coinType)
                            time.sleep(60*1)  # 挂单后，停止运行1分钟
                        else:
                            break
                else:
                    print("币种:{coin}当前市价：{market_price}。未能满足交易,继续运行".format(market_price = cur_market_price,coin=coinType))
                    time.sleep(1)

# 集成GUI版本 - 启动时直接打开监控窗口
if __name__ == "__main__":
    trade_instance = Run_Main()
    
    root = tk.Tk()
    monitor = IntegratedMonitor(root, trade_instance)
    root.mainloop()
