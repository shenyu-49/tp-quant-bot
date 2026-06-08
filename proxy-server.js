const http = require('http');
const https = require('https');

const BINANCE = 'https://api.binance.com';
const COINGECKO = 'https://api.coingecko.com/api/v3';
const CRYPTOCOMPARE = 'https://min-api.cryptocompare.com';

// TP量化机器人 API 代理
// 部署到 Replit: https://replit.com
// 使用方法: 在 Replit 中 Import 这个项目，点 Run 即可

const ID_MAP = {
  'BTC':'bitcoin','ETH':'ethereum','BNB':'binancecoin',
  'CAKE':'pancakeswap-token','DOGE':'dogecoin','SHIB':'shiba-inu',
  'PEPE':'pepe','DOT':'polkadot','SOL':'solana','XRP':'ripple',
  'FIL':'filecoin','LTC':'litecoin','ADA':'cardano','AVAX':'avalanche-2',
  'LINK':'chainlink','MATIC':'matic-network','AAVE':'aave',
  'ARB':'arbitrum','OP':'optimism','INJ':'injective',
  'SUI':'sui','FTM':'fantom','NEAR':'near-protocol',
  'XLM':'stellar','ATOM':'cosmos','UNI':'uniswap',
};

function fetchUrl(url, timeout = 5000) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(url, { headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json' } }, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve(JSON.parse(d)); }
        catch { resolve({ raw: d.slice(0, 200) }); }
      });
    });
    req.setTimeout(timeout, () => { req.destroy(); reject(new Error('timeout')); });
    req.on('error', reject);
  });
}

async function getPrice(symbol) {
  // Binance (最快)
  try {
    const b = await fetchUrl(`${BINANCE}/api/v3/ticker/price?symbol=${symbol.toUpperCase()}USDT`, 3000);
    if (b?.price) return { source: 'binance', price: parseFloat(b.price) };
  } catch {}

  // CryptoCompare (备选)
  try {
    const c = await fetchUrl(`${CRYPTOCOMPARE}/data/price?fsym=${symbol.toUpperCase()}&tsyms=USDT`, 3000);
    if (c?.USDT) return { source: 'cryptocompare', price: c.USDT };
  } catch {}

  // CoinGecko (最后)
  try {
    const id = ID_MAP[symbol.toUpperCase()];
    if (id) {
      const g = await fetchUrl(`${COINGECKO}/simple/price?ids=${id}&vs_currencies=usdt`, 4000);
      if (g?.[id]?.usdt) return { source: 'coingecko', price: g[id].usdt };
    }
  } catch {}

  return null;
}

async function getKlines(symbol, interval = '1h', limit = 100) {
  try {
    const data = await fetchUrl(
      `${BINANCE}/api/v3/klines?symbol=${symbol.toUpperCase()}USDT&interval=${interval}&limit=${limit}`,
      5000
    );
    if (Array.isArray(data)) {
      return data.map(k => ({
        time: Math.floor(k[0] / 1000),
        open: parseFloat(k[1]),
        high: parseFloat(k[2]),
        low: parseFloat(k[3]),
        close: parseFloat(k[4]),
        volume: parseFloat(k[5]),
      }));
    }
  } catch {}
  return null;
}

async function getTokenInfo(address, chain = 'bsc') {
  try {
    const cgChain = chain === 'bsc' ? 'binancecoin' : 'ethereum';
    const data = await fetchUrl(
      `${COINGECKO}/coins/${cgChain}/contract/${address.toLowerCase()}`,
      5000
    );
    if (data?.id) {
      return {
        found: true,
        symbol: (data.symbol || '').toUpperCase(),
        name: data.name || '',
        address: address.toLowerCase(),
        price: data.market_data?.current_price?.usd,
        change_24h: data.market_data?.price_change_percentage_24h,
        market_cap: data.market_data?.market_cap?.usd,
      };
    }
  } catch {}
  return { found: false, address: address.toLowerCase(), chain };
}

const server = http.createServer(async (req, res) => {
  // CORS 头（支持所有来源，包括 TP钱包、AVE等）
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With');
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache');

  if (req.method === 'OPTIONS') {
    res.writeHead(204); res.end(); return;
  }

  const url = new URL(req.url, `http://${req.headers.host}`);
  const path = url.pathname.replace(/^\/+/, '');
  const qs = url.searchParams;

  const send = (data, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(data));
  };

  try {
    // 单币价格
    const priceMatch = path.match(/^price\/([A-Za-z0-9]+)$/i);
    if (priceMatch) {
      const sym = priceMatch[1].toUpperCase();
      const r = await getPrice(sym);
      if (r) return send({ symbol: sym, ...r, time: Date.now() });
      return send({ error: 'price not found', symbol: sym }, 404);
    }

    // 批量价格
    if (path === 'prices' || path === '' || path === 'index') {
      const syms = (qs.get('symbols') || 'BTC,ETH,BNB').split(',').map(s => s.trim().toUpperCase());
      const prices = {};
      await Promise.all(syms.map(async s => { prices[s] = await getPrice(s); }));
      return send({ prices, time: Date.now() });
    }

    // K线数据
    const klineMatch = path.match(/^klines\/([A-Za-z0-9]+)$/i);
    if (klineMatch) {
      const sym = klineMatch[1].toUpperCase();
      const interval = qs.get('interval') || '1h';
      const limit = Math.min(parseInt(qs.get('limit')) || 100, 500);
      const candles = await getKlines(sym, interval, limit);
      if (candles?.length) return send({ symbol: sym, pair: `${sym}USDT`, interval, candles });
      return send({ error: 'no kline data', symbol: sym }, 404);
    }

    // 代币信息
    if (path === 'token' && qs.get('address')) {
      return send(await getTokenInfo(qs.get('address'), qs.get('chain') || 'bsc'));
    }

    // 健康检查
    if (path === 'health' || path === 'status') {
      return send({ status: 'ok', service: 'TP量化API代理', time: Date.now() });
    }

    // 默认
    send({
      service: 'TP量化机器人 API代理',
      version: '1.0.0',
      endpoints: [
        'GET /price/:symbol     — 单币价格',
        'GET /prices?s=BTC,ETH  — 批量价格',
        'GET /klines/:symbol    — K线数据',
        'GET /token?address=0x — 代币信息',
        'GET /health            — 健康检查',
      ]
    });

  } catch (e) {
    send({ error: e.message }, 500);
  }
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, '0.0.0.0', () => {
  console.log(`✅ TP量化 API 代理已启动: http://0.0.0.0:${PORT}`);
  console.log(`   单币: GET /price/BTC`);
  console.log(`   批量: GET /prices?symbols=BTC,ETH,BNB`);
  console.log(`   K线:  GET /klines/BTC?interval=1h`);
});

// 保持活跃（Replit 需要）
setInterval(() => {}, 1000);
