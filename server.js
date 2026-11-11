/**
 * Browser bridge around the repository's original MT5 MarketDataClient.
 * MT5 authentication, quote subscription, reconnect and history backfill all
 * remain inside src/client.js. This file only exposes that data to the browser.
 */
import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, WebSocket } from "ws";
import { MarketDataClient } from "./src/index.js";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 3000);
const symbols = (process.env.MT5_SYMBOLS || "EURUSDm").split(",").map(x => x.trim()).filter(Boolean);

for (const name of ["MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"]) {
  if (!process.env[name]) {
    console.error("Missing " + name + ". Configure it in .env before starting.");
    process.exit(1);
  }
}

let feedState = "starting";
let feedError = null;
const sockets = new Set();

function send(socket, data) {
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
}
function broadcast(data) {
  const payload = JSON.stringify(data);
  for (const socket of sockets) if (socket.readyState === WebSocket.OPEN) socket.send(payload);
}
function setFeedState(state, error = null) {
  feedState = state;
  feedError = error;
  broadcast({ type: "status", state, error: feedError, symbols });
}

const logger = {
  debug: (...args) => console.debug("[MT5]", ...args),
  info: (...args) => {
    console.info("[MT5]", ...args);
    if (args.map(String).join(" ").includes("Connected to ")) setFeedState("connected");
  },
  warn: (...args) => {
    console.warn("[MT5]", ...args);
    if (args.map(String).join(" ").includes("Reconnecting")) setFeedState("reconnecting");
  },
  error: (...args) => {
    console.error("[MT5]", ...args);
    const message = args.map(x => x instanceof Error ? x.message : String(x)).join(" ");
    setFeedState("reconnecting", message);
  },
};

const feed = new MarketDataClient({
  login: Number(process.env.MT5_LOGIN),
  password: process.env.MT5_PASSWORD,
  server: process.env.MT5_SERVER,
  symbols,
  timeframes: ["M1"],
  database: process.env.MT5_DATABASE || "market_data.sqlite3",
  initialBars: Number(process.env.MT5_INITIAL_BARS || 3000),
  backfillInterval: Number(process.env.MT5_BACKFILL_INTERVAL || 15),
  logger,
});

feed.onQuote = quote => broadcast({
  type: "quote",
  symbol: quote.symbol,
  bid: quote.bid,
  ask: quote.ask,
  last: quote.last,
  tickVolume: quote.tickVolume,
  timestamp: quote.timestamp.getTime(),
});

feed.onCandle = candle => broadcast({
  type: "minute",
  symbol: candle.symbol,
  timestamp: candle.timestamp,
  open: candle.open,
  high: candle.high,
  low: candle.low,
  close: candle.close,
  tickVolume: candle.tickVolume,
});

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url || "/", "http://" + (request.headers.host || "localhost"));

  if (url.pathname === "/api/status") {
    response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    response.end(JSON.stringify({ state: feedState, error: feedError, symbols, historyTimeframe: "M1" }));
    return;
  }

  if (url.pathname === "/api/history") {
    const symbol = url.searchParams.get("symbol") || symbols[0];
    const n = Number(url.searchParams.get("limit") || 5000);
    const limit = Number.isFinite(n) ? Math.min(10000, Math.max(1, Math.floor(n))) : 5000;
    if (!symbols.includes(symbol)) {
      response.writeHead(400, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "Symbol is not configured" }));
      return;
    }
    try {
      const candles = feed.getHistory(symbol, "M1", { limit }).map(c => ({
        timestamp: c.timestamp, open: c.open, high: c.high, low: c.low,
        close: c.close, tickVolume: c.tickVolume,
      }));
      response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ symbol, timeframe: "M1", candles }));
    } catch (error) {
      response.writeHead(503, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: error.message }));
    }
    return;
  }

  const filename = url.pathname === "/" || url.pathname === "/index.html"
    ? "index.html" : url.pathname === "/candles.js" ? "candles.js" : null;
  if (!filename) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  try {
    const body = await readFile(path.join(ROOT, filename));
    response.writeHead(200, {
      "content-type": filename.endsWith(".js") ? "text/javascript; charset=utf-8" : "text/html; charset=utf-8",
      "cache-control": "no-cache",
    });
    response.end(body);
  } catch {
    response.writeHead(404);
    response.end("File not found");
  }
});

const websocketServer = new WebSocketServer({ noServer: true });
server.on("upgrade", (request, socket, head) => {
  if (request.url !== "/stream") return socket.destroy();
  websocketServer.handleUpgrade(request, socket, head, ws => websocketServer.emit("connection", ws, request));
});

websocketServer.on("connection", socket => {
  sockets.add(socket);
  send(socket, { type: "status", state: feedState, error: feedError, symbols });
  socket.on("message", raw => {
    let message;
    try { message = JSON.parse(raw.toString()); }
    catch { send(socket, { type: "error", message: "Invalid WebSocket message" }); return; }
    if (message.type !== "history") return;
    const symbol = String(message.symbol || "");
    const n = Number(message.limit || 5000);
    const limit = Number.isFinite(n) ? Math.min(10000, Math.max(1, Math.floor(n))) : 5000;
    if (!symbols.includes(symbol)) {
      send(socket, { type: "error", message: "Symbol is not configured" });
      return;
    }
    try {
      const candles = feed.getHistory(symbol, "M1", { limit }).map(c => ({
        timestamp: c.timestamp, open: c.open, high: c.high, low: c.low,
        close: c.close, tickVolume: c.tickVolume,
      }));
      send(socket, { type: "history", symbol, timeframe: "M1", candles });
    } catch (error) {
      send(socket, { type: "error", message: error.message });
    }
  });
  socket.on("close", () => sockets.delete(socket));
  socket.on("error", () => sockets.delete(socket));
});

server.listen(PORT, () => {
  console.log("Chart UI: http://localhost:" + PORT);
  console.log("Configured MT5 symbols: " + symbols.join(", "));
});

const feedTask = feed.runForever().catch(error => {
  console.error("MT5 feed stopped unexpectedly:", error);
  setFeedState("error", error.message);
});

let shuttingDown = false;
async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log("Stopping browser server and MT5 feed...");
  setFeedState("stopping");
  await feed.stop();
  await feedTask;
  for (const socket of sockets) socket.close();
  await new Promise(resolve => websocketServer.close(resolve));
  await new Promise(resolve => server.close(resolve));
  feed.closeStore();
}
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
