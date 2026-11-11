# Browser live chart

The browser frontend uses the repository's original \`MarketDataClient\` from \`src/index.js\`. The MT5 protocol, broker discovery, authentication, symbol subscription, heartbeat, reconnect loop, SQLite candle store and continuous M1 backfill remain in the existing engine.

## Run

Node.js 22.13 or newer is required by the existing project.

Create a root \`.env\` file:

\`\`\`dotenv
MT5_LOGIN=your_demo_login
MT5_PASSWORD=your_demo_password
MT5_SERVER=your_exact_broker_server
MT5_SYMBOLS=EURUSDm,GBPUSDm
PORT=3000
MT5_INITIAL_BARS=3000
MT5_BACKFILL_INTERVAL=15
\`\`\`

Start the application:

\`\`\`bash
npm install
node --env-file=.env server.js
\`\`\`

Open \`http://localhost:3000\` or \`http://localhost:3000/index.html\`.

## Data flow

1. \`server.js\` instantiates the existing \`MarketDataClient\` and subscribes to configured symbols with M1 history enabled.
2. The original engine writes broker-provided M1 candles to SQLite and repeatedly refreshes history from the latest stored candle. Its reconnect and heartbeat behavior are unchanged.
3. \`server.js\` exposes stored M1 history and relays \`onCandle\` and \`onQuote\` events through WebSocket.
4. \`candles.js\` aggregates M1 history into larger intervals and updates those intervals directly from each live bid tick.
5. \`index.html\` renders the chart. Credentials stay on the Node server.

Higher-timeframe historical bars are aggregations of broker M1 OHLC, not native broker H1/H4/D1 bars. Live higher-timeframe candles are built from incoming bid ticks. The chart uses UTC bucket boundaries.

Use exact broker symbol names (including suffixes). Start with a demo account. Review the main repository README for its reverse-engineered protocol and TLS caveat.
