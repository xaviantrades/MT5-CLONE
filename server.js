/**
 * Browser bridge for the MT5 market-data client.
 * MT5 credentials and broker protocol remain server-side.
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, WebSocket } from "ws";
import { MarketDataClient } from "./src/index.js";

const root = fileURLToPath(new URL(".", import.meta.url));
const publicRoot = resolve(root, "public");
const port = Number(process.env.PORT ?? 3000);

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}. Configure it in your .env file.`);
  return value;
}

const symbols = (process.env.MT5_SYMBOLS ?? "EURUSDm")
  .split(",").map((value) => value.trim()).filter(Boolean);

const feed = new MarketDataClient({
  login: required("MT5_LOGIN"),
  password: required("MT5_PASSWORD"),
  server: required("MT5_SERVER"),
  symbols,
  timeframes: ["M1"],
  database: process.env.MT5_DATABASE ?? "market_data.sqlite3",
  initialBars: Number(process.env.MT5_INITIAL_BARS ?? 5000),
  backfillInterval: Number(process.env.MT5_BACKFILL_INTERVAL ?? 15),
});

const clients = new Set();
let feedStatus = "starting";
let latestQuotes = new Map();

function send(socket, message) {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

function broadcast(message) {
  const payload = JSON.stringify(message);
  for (const socket of clients) {
    if (socket.readyState === WebSocket.OPEN) socket.send(payload);
  }
}

feed.onQuote = (quote) => {
  latestQuotes.set(quote.symbol, quote);
  broadcast({
    type: "tick",
    quote: {
      symbol: quote.symbol,
      bid: quote.bid,
      ask: quote.ask,
      last: quote.last,
      tickVolume: quote.tickVolume,
      timestamp: quote.timestamp.toISOString(),
    },
  });
};

feed.onCandle = (candle) => {
  broadcast({
    type: "minute",
    candle: {
      symbol: candle.symbol,
      timeframe: candle.timeframe,
      timestamp: candle.timestamp,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.tickVolume,
    },
  });
};

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);

  if (url.pathname === "/api/status") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({
      status: feedStatus,
      symbols,
      quotes: [...latestQuotes.values()].map((q) => ({
        symbol: q.symbol, bid: q.bid, ask: q.ask,
        timestamp: q.timestamp.toISOString(),
      })),
    }));
    return;
  }

  let filePath;
  if (url.pathname === "/" || url.pathname === "/indx.html") {
    filePath = resolve(root, "indx.html");
  } else if (url.pathname.startsWith("/public/")) {
    filePath = resolve(root, `.${url.pathname}`);
    if (!filePath.startsWith(publicRoot + sep)) {
      response.writeHead(403).end("Forbidden");
      return;
    }
  } else {
    response.writeHead(404).end("Not found");
    return;
  }

  try {
    const body = await readFile(filePath);
    const types = {
      ".html": "text/html; charset=utf-8",
      ".js": "text/javascript; charset=utf-8",
      ".css": "text/css; charset=utf-8",
    };
    response.writeHead(200, {
      "content-type": types[extname(filePath)] ?? "application/octet-stream",
      "cache-control": "no-cache",
    });
    response.end(body);
  } catch {
    response.writeHead(404).end("File not found");
  }
});

const websocketServer = new WebSocketServer({ server, path: "/stream" });

websocketServer.on("connection", (socket) => {
  clients.add(socket);
  send(socket, { type: "status", status: feedStatus, symbols });

  socket.on("message", (raw) => {
    let message;
    try {
      message = JSON.parse(raw.toString());
    } catch {
      send(socket, { type: "error", message: "Invalid JSON message" });
      return;
    }

    if (message.type === "history") {
      const symbol = String(message.symbol ?? "");
      const limit = Math.min(10000, Math.max(1, Number(message.limit ?? 5000)));
      if (!symbols.includes(symbol)) {
        send(socket, { type: "error", message: "Symbol is not enabled on this server" });
        return;
      }

      try {
        const candles = feed.getHistory(symbol, "M1", { limit });
        send(socket, {
          type: "history",
          symbol,
          timeframe: "M1",
          candles: candles.map((c) => ({
            timestamp: c.timestamp,
            open: c.open, high: c.high, low: c.low, close: c.close,
            volume: c.tickVolume,
          })),
        });
      } catch (error) {
        send(socket, { type: "error", message: error.message });
      }
    }
  });

  socket.on("close", () => clients.delete(socket));
  socket.on("error", () => clients.delete(socket));
});

server.listen(port, () => {
  console.log(`Chart server listening at http://localhost:${port}`);
});

feedStatus = "connecting";
feed.runForever().then(() => {
  feedStatus = "stopped";
}).catch((error) => {
  feedStatus = "error";
  console.error("MT5 feed stopped:", error);
});

async function shutdown() {
  console.log("Shutting down chart server...");
  feedStatus = "stopping";
  await feed.stop();
  for (const socket of clients) socket.close();
  websocketServer.close();
  server.close();
  feed.closeStore();
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
