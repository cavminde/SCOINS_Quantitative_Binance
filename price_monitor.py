# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import json
import os
from app.BinanceAPI import BinanceAPI
from app.authorization import api_key, api_secret
from data.runBetData import RunBetData

class PriceMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Alpha网格交易监控")
        self.root.geometry("900x600")
        self.root.resizable(True, True)
        
        self.binance_api = BinanceAPI(api_key, api_secret)
        self.runbet = RunBetData()
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
            messagebox.showerror("错误", f"加载配置文件失败: {str(e)}")
            self.config = {}
            self.coin_list = []
    
    def create_widgets(self):
        # 标题
        title_frame = ttk.Frame(self.root)
        title_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(title_frame, text="Alpha网格交易实时监控", 
                  font=('Arial', 18, 'bold')).pack()
        
        # 控制按钮
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.btn_start = ttk.Button(control_frame, text="开始监控", 
                                     command=self.start_monitor)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        
        self.btn_stop = ttk.Button(control_frame, text="停止监控", 
                                    command=self.stop_monitor, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(control_frame, text="刷新配置", 
                   command=self.refresh_config).pack(side=tk.LEFT, padx=5)
        
        # 状态标签
        self.status_var = tk.StringVar()
        self.status_var.set("状态: 未启动")
        ttk.Label(control_frame, textvariable=self.status_var, 
                  font=('Arial', 10)).pack(side=tk.RIGHT, padx=10)
        
        # 创建Treeview显示交易对信息
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 滚动条
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 交易对表格
        columns = ('交易对', '类型', '当前价格', '买入价', '卖出价', '状态', '步数', '操作建议')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                  yscrollcommand=scrollbar.set, height=15)
        
        scrollbar.config(command=self.tree.yview)
        
        # 设置列宽和对齐
        col_widths = {'交易对': 100, '类型': 80, '当前价格': 120, 
                      '买入价': 120, '卖出价': 120, '状态': 100, 
                      '步数': 60, '操作建议': 150}
        
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths.get(col, 100), anchor='center')
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 添加样式
        style = ttk.Style()
        style.configure("Treeview", rowheight=30, font=('Arial', 10))
        style.configure("Treeview.Heading", font=('Arial', 10, 'bold'))
        
        # 填充初始数据
        self.populate_tree()
        
        # 日志区域
        log_frame = ttk.LabelFrame(self.root, text="交易日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = tk.Text(log_frame, height=8, font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        scrollbar_log = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar_log.set)
        
        self.log("系统初始化完成")
        self.log(f"加载了 {len(self.coin_list)} 个交易对")
    
    def populate_tree(self):
        '''填充交易对表格'''
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for coin in self.coin_list:
            if coin in self.config:
                coin_config = self.config[coin]
                is_alpha = coin.startswith("ALPHA_")
                coin_type = "Alpha" if is_alpha else "现货"
                benchmark = coin_config.get('benchmark', 'USDT')
                chain = coin_config.get('chain', 'Binance')
                
                self.tree.insert('', tk.END, values=(
                    coin,
                    f"{coin_type}",
                    "---",
                    "---",
                    "---",
                    "等待数据",
                    "---",
                    "---"
                ))
    
    def get_price_info(self, coin):
        '''获取代币价格信息'''
        try:
            if coin.startswith("ALPHA_"):
                price = self.binance_api.get_alpha_ticker_price(coin)
                coin_type = "Alpha"
            else:
                price = self.binance_api.get_ticker_price(coin)
                coin_type = "现货"
            
            return price, coin_type
        except Exception as e:
            self.log(f"获取 {coin} 价格失败: {str(e)}")
            return None, None
    
    def check_trade_condition(self, coin, cur_price):
        '''检查交易条件'''
        try:
            if coin not in self.config:
                return "无配置", "无配置"
            
            config = self.config[coin]
            run_bet = config.get('runBet', {})
            
            buy_price = run_bet.get('next_buy_price', 0)
            sell_price = run_bet.get('grid_sell_price', 0)
            step = run_bet.get('step', 0)
            
            # 判断条件
            if cur_price <= buy_price:
                condition = "买入"
                suggestion = f"价格≤买入价({buy_price:.6f})"
            elif cur_price >= sell_price:
                condition = "卖出"
                suggestion = f"价格≥卖出价({sell_price:.6f})"
            else:
                condition = "观望"
                suggestion = "价格区间内，等待..."
            
            return condition, suggestion, buy_price, sell_price, step
            
        except Exception as e:
            return "错误", str(e), 0, 0, 0
    
    def update_tree_item(self, coin, price, condition, suggestion, buy_price, sell_price, step):
        '''更新表格行'''
        for item in self.tree.get_children():
            values = self.tree.item(item, 'values')
            if values[0] == coin:
                self.tree.item(item, values=(
                    coin,
                    values[1],  # 类型不变
                    f"{price:.6f}" if price else "错误",
                    f"{buy_price:.6f}" if buy_price else "---",
                    f"{sell_price:.6f}" if sell_price else "---",
                    condition,
                    step if step else "---",
                    suggestion
                ))
                
                # 根据状态设置颜色
                if condition == "买入":
                    self.tree.item(item, tags=('buy',))
                elif condition == "卖出":
                    self.tree.item(item, tags=('sell',))
                elif condition == "观望":
                    self.tree.item(item, tags=('watch',))
                else:
                    self.tree.item(item, tags=('error',))
                
                break
    
    def start_monitor(self):
        '''开始监控'''
        if self.monitoring:
            return
        
        self.monitoring = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.status_var.set("状态: 监控中...")
        
        self.monitor_thread = threading.Thread(target=self.monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
        self.log("开始监控价格...")
    
    def stop_monitor(self):
        '''停止监控'''
        self.monitoring = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.status_var.set("状态: 已停止")
        self.log("监控已停止")
    
    def monitor_loop(self):
        '''监控循环'''
        while self.monitoring:
            try:
                for coin in self.coin_list:
                    price, coin_type = self.get_price_info(coin)
                    
                    if price is not None:
                        condition, suggestion, buy_price, sell_price, step = \
                            self.check_trade_condition(coin, price)
                        
                        # 在主线程更新UI
                        self.root.after(0, lambda c=coin, p=price, con=condition, 
                                       sug=suggestion, bp=buy_price, 
                                       sp=sell_price, st=step: 
                                       self.update_tree_item(c, p, con, sug, bp, sp, st))
                        
                        # 记录买入/卖出信号
                        if condition in ["买入", "卖出"]:
                            self.root.after(0, lambda c=coin, p=price, con=condition:
                                           self.log(f"【{con}信号】{c}: {p:.6f}"))
                
                # 配置刷新间隔
                time.sleep(1)  # 每1秒更新一次
                
            except Exception as e:
                self.root.after(0, lambda msg=str(e): self.log(f"监控异常: {msg}"))
                time.sleep(1)
    
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

def main():
    root = tk.Tk()
    app = PriceMonitorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
