#!/usr/bin/env python3
"""
TP钱包量化交易机器人
策略：网格低买高卖 - 跌破买入价回调一定比例再次买入，涨到止盈价卖出
作者：三才汇量化团队
"""

import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

# ========== 配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
LOG_FILE = os.path.join(BASE_DIR, "trades.log")

# 日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TPQuantBot")

# ========== 交易对配置 ==========
DEFAULT_PAIRS = [
    {"symbol": "BTC/USDT", "network": "BSC", "contract": None, "enabled": True},
    {"symbol": "ETH/USDT", "network": "BSC", "contract": None, "enabled": True},
    {"symbol": "BNB/USDT", "network": "BSC", "contract": None, "enabled": True},
]

# ========== 数据类 ==========
class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

@dataclass
class TradeOrder:
    id: str
    timestamp: str
    symbol: str
    side: str
    price: float
    amount: float
    value: float
    fee: float = 0
    tx_hash: str = ""
    status: str = "pending"

@dataclass
class GridLevel:
    level_id: int
    buy_price: float
    sell_price: float
    amount: float = 0
    filled: bool = False

@dataclass
class StrategyState:
    symbol: str
    last_buy_price: float = 0
    last_sell_price: float = 0
    avg_buy_price: float = 0
    total_bought: float = 0
    total_sold: float = 0
    profit: float = 0
    trades: List[Dict] = field(default_factory=list)
    grid_levels: List[Dict] = field(default_factory=list)
    enabled: bool = True
    last_update: str = ""

class TPQuantBot:
    """TP钱包量化交易机器人"""
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or self._load_default_config()
        self.state: Dict[str, StrategyState] = {}
        self.running = False
        self._load_state()
        
    def _load_default_config(self) -> Dict:
        """默认配置"""
        return {
            "trading": {
                "initial_capital": 1000,       # 初始资金 USDT
                "per_trade_ratio": 0.1,         # 每次交易用10%的资金
                "profit_rate": 0.02,             # 止盈收益率 2%
                "buy_drop_rate": 0.015,         # 买入回调比例 1.5%
                "max_positions": 5,             # 最大持仓数
                "stop_loss_rate": 0.05,         # 止损比例 5%
            },
            "grid": {
                "enabled": True,
                "grid_count": 5,                # 网格数量
                "grid_spacing": 0.01,           # 网格间距 1%
            },
            "market": {
                "check_interval": 10,           # 检查间隔(秒)
                "price_source": "binance",      # 价格来源
            },
            "wallet": {
                "address": "",                  # TP钱包地址
                "private_key": "",              # 私钥(加密存储)
            },
            "notification": {
                "enabled": False,
                "webhook_url": "",
            }
        }
    
    def _load_state(self):
        """加载状态"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    for symbol, s in data.items():
                        self.state[symbol] = StrategyState(**s)
                logger.info(f"已加载 {len(self.state)} 个交易对状态")
            except Exception as e:
                logger.error(f"加载状态失败: {e}")
    
    def _save_state(self):
        """保存状态"""
        try:
            data = {symbol: asdict(state) for symbol, state in self.state.items()}
            with open(STATE_FILE, 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存状态失败: {e}")
    
    def _get_or_create_state(self, symbol: str) -> StrategyState:
        """获取或创建交易对状态"""
        if symbol not in self.state:
            self.state[symbol] = StrategyState(symbol=symbol)
            self.state[symbol].last_update = datetime.now().isoformat()
        return self.state[symbol]
    
    # ========== 策略逻辑 ==========
    
    def check_buy_signal(self, state: StrategyState, current_price: float, 
                         config: Dict) -> Optional[Dict]:
        """
        检查买入信号
        买入条件：
        1. 价格比上次买入价低回调比例以上（逢低加仓）
        2. 或者没有持仓时，价格相对较低
        """
        cfg = config["trading"]
        buy_drop_rate = cfg["buy_drop_rate"]
        
        # 情况1：已有持仓，等回调买入
        if state.last_buy_price > 0:
            # 价格比上次买入价低了一定比例
            drop_threshold = state.last_buy_price * (1 - buy_drop_rate)
            if current_price <= drop_threshold:
                return {
                    "type": "callback_buy",
                    "reason": f"价格{current_price:.4f}比买入价{state.last_buy_price:.4f}下跌{buy_drop_rate*100:.1f}%",
                    "target_price": current_price,
                    "priority": 1
                }
        
        # 情况2：无持仓
        if state.total_bought == 0:
            # 使用简单的均值回归：价格低于均价时买入
            if state.avg_buy_price > 0:
                if current_price < state.avg_buy_price * 0.98:
                    return {
                        "type": "init_buy",
                        "reason": f"价格{current_price:.4f}低于均价{state.avg_buy_price:.4f}",
                        "target_price": current_price,
                        "priority": 2
                    }
            else:
                # 初始买入
                return {
                    "type": "init_buy",
                    "reason": "首次买入机会",
                    "target_price": current_price,
                    "priority": 3
                }
        
        return None
    
    def check_sell_signal(self, state: StrategyState, current_price: float,
                          config: Dict) -> Optional[Dict]:
        """
        检查卖出信号
        卖出条件：
        1. 价格上涨达到止盈比例
        2. 止损条件触发
        """
        cfg = config["trading"]
        profit_rate = cfg["profit_rate"]
        stop_loss_rate = cfg["stop_loss_rate"]
        
        # 必须有持仓
        if state.total_bought == 0:
            return None
        
        # 情况1：止盈卖出（价格比买入价高了一定比例）
        if state.last_buy_price > 0:
            profit_threshold = state.last_buy_price * (1 + profit_rate)
            if current_price >= profit_threshold:
                return {
                    "type": "take_profit",
                    "reason": f"价格{current_price:.4f}比买入价{state.last_buy_price:.4f}上涨{profit_rate*100:.1f}%",
                    "target_price": current_price,
                    "priority": 1
                }
        
        # 情况2：止损卖出
        if state.last_buy_price > 0:
            stop_threshold = state.last_buy_price * (1 - stop_loss_rate)
            if current_price <= stop_threshold:
                return {
                    "type": "stop_loss",
                    "reason": f"价格{current_price:.4f}触及止损{stop_loss_rate*100:.1f}%",
                    "target_price": current_price,
                    "priority": 0
                }
        
        return None
    
    def calculate_trade_amount(self, state: StrategyState, current_price: float,
                               config: Dict) -> float:
        """计算交易数量"""
        cfg = config["trading"]
        per_trade_ratio = cfg["per_trade_ratio"]
        
        # 如果有卖出记录，用卖出后的资金
        if state.profit > 0:
            available = cfg["initial_capital"] + state.profit
        else:
            available = cfg["initial_capital"] + state.profit
        
        trade_value = available * per_trade_ratio
        amount = trade_value / current_price
        
        return amount
    
    def execute_trade(self, state: StrategyState, signal: Dict, 
                     current_price: float, config: Dict) -> Optional[TradeOrder]:
        """执行交易"""
        cfg = config["trading"]
        side = signal["type"] in ["callback_buy", "init_buy", "stop_loss"] and "BUY" or "SELL"
        
        amount = self.calculate_trade_amount(state, current_price, config)
        value = amount * current_price
        fee = value * 0.001  # 0.1% 手续费
        
        order = TradeOrder(
            id=f"ORDER_{int(time.time()*1000)}",
            timestamp=datetime.now().isoformat(),
            symbol=state.symbol,
            side=side,
            price=current_price,
            amount=amount,
            value=value,
            fee=fee,
            status="pending"
        )
        
        # 更新状态
        if side == "BUY":
            state.last_buy_price = current_price
            if state.avg_buy_price == 0:
                state.avg_buy_price = current_price
            else:
                # 加权平均
                total_cost = state.avg_buy_price * state.total_bought + current_price * amount
                state.total_bought += amount
                state.avg_buy_price = total_cost / state.total_bought
            state.total_bought += amount
        else:  # SELL
            state.last_sell_price = current_price
            sell_value = value - fee
            cost_basis = state.avg_buy_price * amount
            profit = sell_value - cost_basis
            state.profit += profit
            state.total_sold += amount
            state.total_bought -= amount
        
        # 记录交易
        trade_record = {
            "order_id": order.id,
            "timestamp": order.timestamp,
            "type": signal["type"],
            "side": side,
            "price": current_price,
            "amount": amount,
            "value": value,
            "fee": fee,
            "reason": signal["reason"],
            "total_profit": state.profit,
            "remaining": state.total_bought
        }
        state.trades.append(trade_record)
        state.last_update = datetime.now().isoformat()
        
        self._save_state()
        
        logger.info(f"📊 交易信号: {signal['type']} | {side} {amount:.6f} @ {current_price:.4f} | {signal['reason']}")
        
        return order
    
    # ========== 状态报告 ==========
    
    def get_status_report(self, symbol: str) -> Dict:
        """获取状态报告"""
        if symbol not in self.state:
            return {"error": "未初始化"}
        
        state = self.state[symbol]
        cfg = self.config["trading"]
        
        total_value = state.total_bought * cfg.get("current_price", 0) + state.profit
        roi = (state.profit / cfg["initial_capital"]) * 100 if cfg["initial_capital"] > 0 else 0
        
        return {
            "symbol": symbol,
            "status": "运行中" if state.enabled else "已停止",
            "avg_buy_price": f"{state.avg_buy_price:.6f}",
            "last_buy_price": f"{state.last_buy_price:.6f}",
            "last_sell_price": f"{state.last_sell_price:.6f}",
            "holding_amount": f"{state.total_bought:.6f}",
            "total_profit": f"{state.profit:.2f} USDT",
            "roi": f"{roi:.2f}%",
            "total_trades": len(state.trades),
            "last_update": state.last_update,
        }
    
    def get_all_status(self) -> List[Dict]:
        """获取所有交易对状态"""
        return [self.get_status_report(symbol) for symbol in self.state.keys()]
    
    # ========== 配置管理 ==========
    
    def update_config(self, key: str, value: Any):
        """更新配置"""
        keys = key.split(".")
        d = self.config
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
        self._save_config()
    
    def _save_config(self):
        """保存配置"""
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
    
    def load_config(self, config_path: str):
        """加载配置"""
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                self.config = json.load(f)
        else:
            self._save_config()


def main():
    """主函数 - 命令行模式"""
    print("=" * 60)
    print("🤖 TP钱包量化交易机器人")
    print("=" * 60)
    
    bot = TPQuantBot()
    
    # 检查配置
    if not bot.config.get("wallet", {}).get("address"):
        print("\n⚠️  请先配置钱包地址")
        print("   编辑 config.json 设置 wallet.address")
        return
    
    print("\n📊 交易配置:")
    print(f"   初始资金: {bot.config['trading']['initial_capital']} USDT")
    print(f"   每笔交易比例: {bot.config['trading']['per_trade_ratio']*100}%")
    print(f"   止盈比例: {bot.config['trading']['profit_rate']*100}%")
    print(f"   买入回调: {bot.config['trading']['buy_drop_rate']*100}%")
    print(f"   止损比例: {bot.config['trading']['stop_loss_rate']*100}%")
    
    # 演示模式 - 模拟数据
    print("\n🚀 启动演示模式（模拟数据）...")
    
    import random
    price = 50000.0  # BTC初始价格
    
    for i in range(20):
        # 模拟价格波动
        change = random.uniform(-0.02, 0.025)
        price *= (1 + change)
        
        # 模拟交易逻辑
        state = bot._get_or_create_state("BTC/USDT")
        
        buy_signal = bot.check_buy_signal(state, price, bot.config)
        sell_signal = bot.check_sell_signal(state, price, bot.config)
        
        if sell_signal and state.total_bought > 0:
            order = bot.execute_trade(state, sell_signal, price, bot.config)
            if order:
                print(f"  [{i+1:02d}] 💰 卖出: {order.amount:.4f} BTC @ {price:.2f} | 盈利: {state.profit:.2f}")
        
        if buy_signal and (state.total_bought == 0 or buy_signal["type"] == "callback_buy"):
            order = bot.execute_trade(state, buy_signal, price, bot.config)
            if order:
                print(f"  [{i+1:02d}] 🛒 买入: {order.amount:.4f} BTC @ {price:.2f}")
        
        time.sleep(0.5)
    
    # 打印最终状态
    print("\n" + "=" * 60)
    print("📈 最终状态报告")
    print("=" * 60)
    
    for status in bot.get_all_status():
        print(f"\n{status['symbol']}:")
        for k, v in status.items():
            if k != 'symbol':
                print(f"   {k}: {v}")


if __name__ == "__main__":
    main()
