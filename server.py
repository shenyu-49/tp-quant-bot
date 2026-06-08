#!/usr/bin/env python3
"""
TP钱包量化交易机器人 - 后端服务
提供实时价格、WebSocket推送、交易执行接口
"""

import os
import sys
import json
import time
import asyncio
import logging
import requests
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS

# ========== 配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("TPServer")

app = Flask(__name__)
CORS(app)

# ========== 全局状态 ==========
class TradingState:
    def __init__(self):
        self.config = self._load_config()
        self.state = self._load_state()
        self.prices = {}
        self.connected = False
        self.wallet_address = ""
        
    def _load_config(self):
        default = {
            "initial_capital": 1000,
            "per_trade_ratio": 0.1,
            "profit_rate": 0.02,
            "buy_drop_rate": 0.015,
            "stop_loss_rate": 0.05,
            "check_interval": 10,
            "symbols": ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
        }
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return {**default, **json.load(f)}
        return default
    
    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        return {
            "lastBuyPrices": {},
            "totalBought": {},
            "totalSold": {},
            "trades": [],
            "profit": 0
        }
    
    def save_state(self):
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)

state = TradingState()

# ========== 价格获取 ==========
def get_binance_price(symbol: str) -> float:
    """从Binance获取实时价格"""
    try:
        pair = symbol.replace('/', '')
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        return float(data['price'])
    except Exception as e:
        logger.error(f"获取价格失败 {symbol}: {e}")
        return 0

def get_all_prices() -> dict:
    """获取所有交易对价格"""
    prices = {}
    for symbol in state.config['symbols']:
        price = get_binance_price(symbol)
        if price > 0:
            prices[symbol] = price
            state.prices[symbol] = price
    return prices

# ========== 交易逻辑 ==========
def check_signals(symbol: str, current_price: float) -> dict:
    """检查交易信号"""
    cfg = state.config
    last_buy = state.state['lastBuyPrices'].get(symbol, 0)
    holding = state.state['totalBought'].get(symbol, 0)
    
    result = {
        "symbol": symbol,
        "current_price": current_price,
        "last_buy_price": last_buy,
        "holding": holding,
        "signal": "hold",
        "signal_text": "监控中",
        "buy_threshold": 0,
        "sell_threshold": 0,
        "stop_loss_threshold": 0
    }
    
    if last_buy == 0:
        # 无持仓，不显示买入价
        result["signal"] = "ready"
        result["signal_text"] = "📊 等待买入信号"
        return result
    
    # 计算阈值
    buy_drop = cfg['buy_drop_rate']
    profit = cfg['profit_rate']
    stop_loss = cfg['stop_loss_rate']
    
    result["buy_threshold"] = last_buy * (1 - buy_drop)
    result["sell_threshold"] = last_buy * (1 + profit)
    result["stop_loss_threshold"] = last_buy * (1 - stop_loss)
    
    if holding > 0:
        # 有持仓
        if current_price >= result["sell_threshold"]:
            result["signal"] = "take_profit"
            result["signal_text"] = "🎯 止盈信号"
        elif current_price <= result["stop_loss_threshold"]:
            result["signal"] = "stop_loss"
            result["signal_text"] = "⚠️ 止损信号"
        elif current_price <= result["buy_threshold"]:
            result["signal"] = "callback_buy"
            result["signal_text"] = "📉 回调买入信号"
        else:
            result["signal"] = "holding"
            result["signal_text"] = "📊 持仓中"
    else:
        # 无持仓
        if current_price <= result["buy_threshold"]:
            result["signal"] = "buy"
            result["signal_text"] = "🛒 买入信号"
        else:
            result["signal"] = "waiting"
            result["signal_text"] = "📊 等待机会"
    
    return result

def execute_trade(symbol: str, side: str, price: float, reason: str) -> dict:
    """执行交易"""
    cfg = state.config
    
    if side == "BUY":
        # 计算买入数量
        available = cfg['initial_capital'] + state.state['profit']
        trade_value = available * cfg['per_trade_ratio']
        amount = trade_value / price
        fee = trade_value * 0.001
        
        # 更新状态
        state.state['lastBuyPrices'][symbol] = price
        state.state['totalBought'][symbol] = state.state['totalBought'].get(symbol, 0) + amount
        
        trade_record = {
            "id": int(time.time() * 1000),
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "side": "BUY",
            "price": price,
            "amount": amount,
            "value": trade_value,
            "fee": fee,
            "reason": reason,
            "tx_hash": ""
        }
        
    else:  # SELL
        amount = state.state['totalBought'].get(symbol, 0)
        if amount <= 0:
            return {"success": False, "error": "没有持仓"}
        
        value = amount * price
        avg_buy = state.state['lastBuyPrices'].get(symbol, price)
        profit = (price - avg_buy) * amount
        fee = value * 0.001
        net_profit = profit - fee
        
        trade_record = {
            "id": int(time.time() * 1000),
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "side": "SELL",
            "price": price,
            "amount": amount,
            "value": value,
            "fee": fee,
            "profit": net_profit,
            "reason": reason,
            "tx_hash": ""
        }
        
        # 更新状态
        state.state['totalSold'][symbol] = state.state['totalSold'].get(symbol, 0) + amount
        state.state['totalBought'][symbol] = 0
        state.state['lastBuyPrices'][symbol] = 0
        state.state['profit'] += net_profit
    
    state.state['trades'].append(trade_record)
    state.save_state()
    
    return {"success": True, "trade": trade_record}

# ========== API路由 ==========

@app.route('/')
def index():
    """主页"""
    return jsonify({
        "name": "TP钱包量化交易机器人 API",
        "version": "1.0.0",
        "endpoints": {
            "GET /api/prices": "获取所有交易对实时价格",
            "GET /api/status/<symbol>": "获取交易对状态和信号",
            "GET /api/status": "获取所有交易对状态",
            "GET /api/trades": "获取交易历史",
            "POST /api/config": "更新配置",
            "GET /api/config": "获取当前配置",
            "POST /api/trade/execute": "手动执行交易"
        }
    })

@app.route('/api/prices')
def api_prices():
    """获取实时价格"""
    prices = get_all_prices()
    return jsonify({
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "prices": prices
    })

@app.route('/api/status/<symbol>')
def api_status_symbol(symbol):
    """获取单个交易对状态"""
    symbol = symbol.upper()
    if '/' not in symbol:
        symbol = symbol + '/USDT'
    
    price = state.prices.get(symbol, get_binance_price(symbol))
    result = check_signals(symbol, price)
    
    return jsonify({
        "success": True,
        "data": result
    })

@app.route('/api/status')
def api_status():
    """获取所有交易对状态"""
    result = {}
    for symbol in state.config['symbols']:
        price = state.prices.get(symbol, get_binance_price(symbol))
        result[symbol] = check_signals(symbol, price)
    
    return jsonify({
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "total_profit": state.state['profit'],
        "total_trades": len(state.state['trades']),
        "data": result
    })

@app.route('/api/trades')
def api_trades():
    """获取交易历史"""
    return jsonify({
        "success": True,
        "trades": state.state['trades'][-50:]  # 最近50条
    })

@app.route('/api/config', methods=['GET'])
def api_config_get():
    """获取配置"""
    return jsonify({
        "success": True,
        "config": state.config
    })

@app.route('/api/config', methods=['POST'])
def api_config_set():
    """更新配置"""
    data = request.json
    for key, value in data.items():
        if key in state.config:
            state.config[key] = value
    return jsonify({
        "success": True,
        "config": state.config
    })

@app.route('/api/trade/execute', methods=['POST'])
def api_trade_execute():
    """手动执行交易"""
    data = request.json
    symbol = data.get('symbol')
    side = data.get('side')
    reason = data.get('reason', 'manual')
    
    if not symbol or not side:
        return jsonify({"success": False, "error": "缺少参数"}), 400
    
    price = get_binance_price(symbol)
    if price <= 0:
        return jsonify({"success": False, "error": "无法获取价格"}), 400
    
    result = execute_trade(symbol, side.upper(), price, reason)
    return jsonify(result)

# ========== 主函数 ==========

def main():
    port = int(os.environ.get('PORT', 3001))
    logger.info(f"🚀 启动TP量化机器人服务 :{port}")
    logger.info(f"   初始资金: {state.config['initial_capital']} USDT")
    logger.info(f"   止盈比例: {state.config['profit_rate']*100}%")
    logger.info(f"   回调买入: {state.config['buy_drop_rate']*100}%")
    logger.info(f"   止损比例: {state.config['stop_loss_rate']*100}%")
    
    # 预热价格
    get_all_prices()
    
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()
