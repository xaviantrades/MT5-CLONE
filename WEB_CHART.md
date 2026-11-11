# Browser chart

This is a small browser interface for the MT5 market-data client.

## Start

1. Create a `.env` file in the repository root with the broker credentials:

```env
MT5_LOGIN=your_demo_login
MT5_PASSWORD=your_demo_password
MT5_SERVER=YourBroker-MT5
MT5_SYMBOLS=EURUSDm,GBPUSDm
MT5_INITIAL_BARS=5000
MT5_BACKFILL_INTERVAL=15
PORT=3000
```

2. Install the existing dependency and start the bridge:

```bash
npm install
node --env-file=.env server.js
```

3. Open `http://localhost:3000/indx.html`.

Keep `.env` private and use a demo account while testing. The browser never receives the MT5 password.

## Data flow

- The existing `MarketDataClient` maintains the MT5 session, subscribes to the configured symbols, reconnects, and stores broker M1 history in SQLite.
- `server.js` relays live quotes and M1 history over a browser WebSocket.
- `public/candles.js` seeds all chart intervals from the M1 history and then aggregates live bid ticks independently into M1, M5, M15, H1, H4, and D1 candles.
- Higher-timeframe history is aggregated from broker M1 bars. Live higher-timeframe candles are then updated directly from ticks.

The chart uses bid prices. The browser's tick-volume field counts received updates; it is not broker exchange volume. Broker M1 history remains the source for historical OHLC.
