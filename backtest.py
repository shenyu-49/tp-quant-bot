#!/usr/bin/env python3
"""
TP钱包量化交易机器人 - 回测模块
验证策略在过去市场中的表现
"""

import os
import sys
import json
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# ========== 配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@dataclass
class BacktestConfig:
    initial_capital: float = 1000
    per_trade_ratio: float = 0.1
    profit_rate: float = 0.02
    buy_drop_rate: float = 0.015
    stop_loss_rate: float = 0.05
    commission: float = 0.001
    slippage: float = 0.0005

@dataclass
class TradeRecord:
    timestamp: str
    symbol: str
    side: str
    price: float
    amount: float
    value: float
    fee: float
    profit: float = 0
    balance: float = 0
    reason: str = ""

@dataclass
class BacktestResult:
    symbol: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    trades: List[TradeRecord]

def generate_simulated_data(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """生成模拟市场数据（基于真实波动率模拟）"""
    np.random.seed(42 + hash(symbol) % 1000)
    
    # 基准价格
    base_prices = {
        'BTC/USDT': 67000, 'ETH/USDT': 3500, 'BNB/USDT': 580,
        'DOGE/USDT': 0.16, 'SOL/USDT': 180, 'XRP/USDT': 0.62
    }
    base = base_prices.get(symbol, 1000)
    
    # 根据时间间隔确定数据点数量
    interval_map = {'1m': 60, '5m': 12, '15m': 4, '1h': 1, '4h': 0.25, '1d': 1/24}
    points_per_hour = interval_map.get(interval, 1)
    total_points = int(days * 24 * points_per_hour)
    
    # 生成带趋势和波动的价格序列
    timestamps = pd.date_range(end=datetime.now(), periods=total_points, freq=interval or '1h')
    
    # 随机游走 + 趋势
    volatility = 0.02  # 2% 波动率
    trend = np.cumsum(np.random.randn(total_points) * volatility * base)
    trend += np.linspace(0, base * 0.1, total_points)  # 轻微上涨趋势
    
    prices = base + trend
    
    # 确保价格为正
    prices = np.maximum(prices, base * 0.5)
    
    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': prices,
        'high': prices * (1 + np.abs(np.random.randn(total_points) * 0.005)),
        'low': prices * (1 - np.abs(np.random.randn(total_points) * 0.005)),
        'close': prices * (1 + np.random.randn(total_points) * 0.003),
        'volume': np.random.rand(total_points) * 1000 + 100
    })
    
    # 修正 high >= open/close >= low
    df['high'] = df[['open', 'close', 'high']].max(axis=1)
    df['low'] = df[['open', 'close', 'low']].min(axis=1)
    
    return df

# ========== 数据获取 ==========
def fetch_binance_klines(symbol: str, interval: str = "1h", days: int = 30) -> pd.DataFrame:
    """从Binance获取K线数据"""
    try:
        pair = symbol.replace('/', '')
        limit = min(days * 24, 1000)  # Binance最大1000根
        
        url = f"https://api.binance.com/api/v3/klines"
        params = {
            "symbol": pair,
            "interval": interval,
            "limit": limit
        }
        
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        
        if not data or 'code' in data:
            raise Exception(f"API错误: {data}")
        
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'tb_base', 'tb_quote', 'ignore'
        ])
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        
        return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    
    except Exception as e:
        print(f"  ⚠️ 网络不可用，使用模拟数据 ({e})")
        return generate_simulated_data(symbol, interval, days)

# ========== 回测引擎 ==========
class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.trades: List[TradeRecord] = []
        self.balance = config.initial_capital
        self.position = 0
        self.position_price = 0
        self.last_buy_price = 0
        self.equity_curve = []
        
    def reset(self):
        """重置状态"""
        self.trades = []
        self.balance = self.config.initial_capital
        self.position = 0
        self.position_price = 0
        self.last_buy_price = 0
        self.equity_curve = []
    
    def check_signals(self, price: float, dt: str) -> Optional[str]:
        """检查交易信号"""
        cfg = self.config
        
        if self.position > 0:
            # 有持仓 - 检查卖出
            take_profit = self.last_buy_price * (1 + cfg.profit_rate)
            stop_loss = self.last_buy_price * (1 - cfg.stop_loss_rate)
            buy_drop = self.last_buy_price * (1 - cfg.buy_drop_rate)
            
            if price >= take_profit:
                return 'take_profit'
            elif price <= stop_loss:
                return 'stop_loss'
            elif price <= buy_drop and cfg.per_trade_ratio > 0:
                return 'callback_buy'
        else:
            # 无持仓 - 检查首买入
            if self.last_buy_price == 0:
                # 还没有任何买入，随机触发一次首买入（模拟价格相对低点）
                return 'first_buy'
            elif cfg.per_trade_ratio > 0:
                # 有历史买入价，等回调
                buy_drop = self.last_buy_price * (1 - cfg.buy_drop_rate)
                if price <= buy_drop:
                    return 'callback_buy'
        
        return None
    
    def execute_buy(self, price: float, dt: str, reason: str):
        """执行买入"""
        cfg = self.config
        
        # 考虑滑点
        exec_price = price * (1 + cfg.slippage)
        trade_value = self.balance * cfg.per_trade_ratio
        amount = trade_value / exec_price
        fee = trade_value * cfg.commission
        
        self.balance -= (trade_value + fee)
        self.position += amount
        self.position_price = exec_price
        self.last_buy_price = exec_price
        
        trade = TradeRecord(
            timestamp=dt,
            symbol="",
            side="BUY",
            price=exec_price,
            amount=amount,
            value=trade_value,
            fee=fee,
            balance=self.balance + self.position * exec_price,
            reason=reason
        )
        self.trades.append(trade)
    
    def execute_sell(self, price: float, dt: str, reason: str) -> float:
        """执行卖出，返回盈亏"""
        cfg = self.config
        
        exec_price = price * (1 - cfg.slippage)
        value = self.position * exec_price
        fee = value * cfg.commission
        cost_basis = self.position * self.position_price
        profit = value - cost_basis - fee
        
        self.balance += (value - fee)
        
        trade = TradeRecord(
            timestamp=dt,
            symbol="",
            side="SELL",
            price=exec_price,
            amount=self.position,
            value=value,
            fee=fee,
            profit=profit,
            balance=self.balance,
            reason=reason
        )
        self.trades.append(trade)
        
        self.position = 0
        self.position_price = 0
        
        return profit
    
    def run(self, symbol: str, df: pd.DataFrame) -> BacktestResult:
        """运行回测"""
        self.reset()
        
        for i in range(len(df)):
            row = df.iloc[i]
            price = row['close']
            dt = str(row['timestamp'])
            
            # 记录权益
            equity = self.balance + self.position * price
            self.equity_curve.append(equity)
            
            # 检查信号
            signal = self.check_signals(price, dt)
            
            if signal == 'take_profit':
                self.execute_sell(price, dt, '止盈')
            elif signal == 'stop_loss':
                self.execute_sell(price, dt, '止损')
            elif signal in ('callback_buy', 'first_buy'):
                self.execute_buy(price, dt, '回调买入' if signal == 'callback_buy' else '首买入')
        
        # 计算最终权益
        final_equity = self.balance + self.position * df.iloc[-1]['close']
        
        # 计算指标
        result = self._calculate_metrics(symbol, df, final_equity)
        
        return result
    
    def _calculate_metrics(self, symbol: str, df: pd.DataFrame, final_equity: float) -> BacktestResult:
        """计算性能指标"""
        trades = self.trades
        
        # 分离买卖交易
        sells = [t for t in trades if t.side == 'SELL']
        wins = [t for t in sells if t.profit > 0]
        losses = [t for t in sells if t.profit <= 0]
        
        win_rate = len(wins) / len(sells) * 100 if sells else 0
        avg_profit = np.mean([t.profit for t in wins]) if wins else 0
        avg_loss = np.mean([t.profit for t in losses]) if losses else 0
        profit_factor = abs(sum(t.profit for t in wins) / sum(t.profit for t in losses)) if losses and sum(t.profit for t in losses) != 0 else 0
        
        # 最大回撤
        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak * 100
        max_drawdown = abs(np.min(drawdown)) if len(drawdown) > 0 else 0
        
        # 夏普比率 (简化)
        if sells and len(sells) > 1:
            returns = [t.profit / self.config.initial_capital * 100 for t in sells]
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(len(returns)) if np.std(returns) > 0 else 0
        else:
            sharpe = 0
        
        return BacktestResult(
            symbol=symbol,
            start_date=str(df['timestamp'].iloc[0]),
            end_date=str(df['timestamp'].iloc[-1]),
            initial_capital=self.config.initial_capital,
            final_capital=final_equity,
            total_return=(final_equity - self.config.initial_capital) / self.config.initial_capital * 100,
            total_trades=len(sells),
            win_trades=len(wins),
            loss_trades=len(losses),
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            trades=trades
        )

# ========== 回测运行 ==========
def run_backtest(symbol: str = "BTC/USDT", days: int = 90, 
                  initial_capital: float = 1000) -> BacktestResult:
    """运行回测"""
    
    print("\n" + "=" * 60)
    print(f"📊 回测: {symbol} (最近 {days} 天)")
    print("=" * 60)
    
    # 获取数据
    print(f"📥 获取K线数据...")
    df = fetch_binance_klines(symbol, interval="1h", days=days)
    
    if df.empty:
        print(f"❌ 无法获取数据")
        return None
    
    print(f"   获取到 {len(df)} 条数据")
    print(f"   时间范围: {df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]}")
    print(f"   价格范围: {df['low'].min():.2f} ~ {df['high'].max():.2f}")
    
    # 配置
    config = BacktestConfig(
        initial_capital=initial_capital,
        per_trade_ratio=0.1,
        profit_rate=0.02,
        buy_drop_rate=0.015,
        stop_loss_rate=0.05
    )
    
    print(f"\n⚙️ 策略参数:")
    print(f"   初始资金: ${config.initial_capital:,.2f}")
    print(f"   每笔交易: {config.per_trade_ratio*100:.0f}% 资金")
    print(f"   止盈比例: {config.profit_rate*100:.1f}%")
    print(f"   回调买入: {config.buy_drop_rate*100:.1f}%")
    print(f"   止损比例: {config.stop_loss_rate*100:.1f}%")
    
    # 运行回测
    engine = BacktestEngine(config)
    result = engine.run(symbol, df)
    
    # 打印结果
    print(f"\n" + "=" * 60)
    print(f"📈 回测结果")
    print("=" * 60)
    print(f"   最终资金: ${result.final_capital:,.2f}")
    print(f"   总收益率: {result.total_return:.2f}%")
    print(f"   交易次数: {result.total_trades}")
    print(f"   盈利次数: {result.win_trades}")
    print(f"   亏损次数: {result.loss_trades}")
    print(f"   胜率: {result.win_rate:.1f}%")
    print(f"   最大回撤: {result.max_drawdown:.2f}%")
    print(f"   夏普比率: {result.sharpe_ratio:.2f}")
    print(f"   盈亏比: {result.profit_factor:.2f}")
    
    if result.trades:
        print(f"\n📋 最近交易:")
        for trade in result.trades[-10:]:
            profit_str = f" | 盈亏: {trade.profit:+.2f}" if trade.side == "SELL" else ""
            print(f"   [{trade.timestamp[:16]}] {trade.side} {trade.amount:.4f} @ {trade.price:.2f} {trade.reason}{profit_str}")
    
    return result

def compare_parameters():
    """参数敏感性分析"""
    print("\n" + "=" * 60)
    print("🔬 参数敏感性分析 - BTC/USDT 90天回测")
    print("=" * 60)
    
    df = fetch_binance_klines("BTC/USDT", interval="1h", days=90)
    if df.empty:
        return
    
    # 测试不同参数组合
    results = []
    for profit_rate in [0.01, 0.015, 0.02, 0.03]:
        for buy_drop in [0.01, 0.015, 0.02, 0.03]:
            for stop_loss in [0.03, 0.05, 0.07, 0.1]:
                config = BacktestConfig(
                    initial_capital=1000,
                    per_trade_ratio=0.1,
                    profit_rate=profit_rate,
                    buy_drop_rate=buy_drop,
                    stop_loss_rate=stop_loss
                )
                engine = BacktestEngine(config)
                result = engine.run("BTC/USDT", df)
                
                results.append({
                    'profit_rate': profit_rate,
                    'buy_drop': buy_drop,
                    'stop_loss': stop_loss,
                    'return': result.total_return,
                    'sharpe': result.sharpe_ratio,
                    'max_dd': result.max_drawdown,
                    'trades': result.total_trades,
                    'win_rate': result.win_rate
                })
    
    # 排序找最优
    results.sort(key=lambda x: x['sharpe'], reverse=True)
    
    print("\n🏆 Top 5 参数组合 (按夏普比率):")
    print(f"{'止盈%':>8} {'回调%':>8} {'止损%':>8} {'收益率%':>10} {'夏普':>8} {'最大回撤%':>10} {'交易次数':>8}")
    print("-" * 65)
    for r in results[:5]:
        print(f"{r['profit_rate']*100:>7.1f} {r['buy_drop']*100:>7.1f} {r['stop_loss']*100:>7.1f} {r['return']:>9.2f} {r['sharpe']:>8.2f} {r['max_dd']:>9.2f} {r['trades']:>8}")

# ========== 主函数 ==========
def main():
    print("=" * 60)
    print("🤖 TP钱包量化交易机器人 - 回测系统")
    print("=" * 60)
    
    # 单次回测
    result = run_backtest("BTC/USDT", days=90, initial_capital=1000)
    
    if result:
        # ETH对比
        run_backtest("ETH/USDT", days=90, initial_capital=1000)
        
        # 参数分析
        compare_parameters()
    
    print("\n" + "=" * 60)
    print("⚠️ 风险提示: 回测结果不代表未来表现")
    print("   请在实盘前充分测试，并设置合理的止损")
    print("=" * 60)

if __name__ == "__main__":
    main()
