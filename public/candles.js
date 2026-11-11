/**
 * Multi-timeframe OHLC builder.
 * Broker M1 bars seed history; live bid ticks update each timeframe directly.
 * All timestamps are UTC Unix seconds.
 */
const PERIODS = Object.freeze({
  M1: 60,
  M5: 300,
  M15: 900,
  H1: 3600,
  H4: 14400,
  D1: 86400,
});

export class CandleBuilder {
  constructor() {
    this.data = new Map();
    this.tickTouched = new Set();
    this.lastTickAt = new Map();
  }

  key(symbol, timeframe, bucket) {
    return symbol + "|" + timeframe + "|" + bucket;
  }

  bucket(timestamp, seconds) {
    return Math.floor(timestamp / seconds) * seconds;
  }

  mapFor(symbol, timeframe) {
    const key = symbol + "|" + timeframe;
    if (!this.data.has(key)) this.data.set(key, new Map());
    return this.data.get(key);
  }

  getPeriods() {
    return Object.keys(PERIODS);
  }

  seed(symbol, minuteBars) {
    // Merge, do not clear: a live tick may arrive before the history response.
    const sorted = [...minuteBars].sort((a, b) => a.timestamp - b.timestamp);
    for (const bar of sorted) this.addMinuteBar({ ...bar, symbol });
  }

  addMinuteBar(bar) {
    const minuteTime = Number(bar.timestamp);
    if (!Number.isFinite(minuteTime)) return;

    for (const [timeframe, seconds] of Object.entries(PERIODS)) {
      const bucket = this.bucket(minuteTime, seconds);
      const bars = this.mapFor(bar.symbol, timeframe);
      const existing = bars.get(bucket);
      const tickKey = this.key(bar.symbol, timeframe, bucket);

      if (!existing) {
        bars.set(bucket, {
          time: bucket,
          open: Number(bar.open),
          high: Number(bar.high),
          low: Number(bar.low),
          close: Number(bar.close),
          firstMinute: minuteTime,
          lastMinute: minuteTime,
        });
        continue;
      }

      if (minuteTime < existing.firstMinute) {
        existing.firstMinute = minuteTime;
        existing.open = Number(bar.open);
      }
      if (minuteTime > existing.lastMinute) {
        existing.lastMinute = minuteTime;
        if (!this.tickTouched.has(tickKey)) existing.close = Number(bar.close);
      } else if (minuteTime === existing.lastMinute && !this.tickTouched.has(tickKey)) {
        existing.close = Number(bar.close);
      }

      existing.high = Math.max(existing.high, Number(bar.high));
      existing.low = Math.min(existing.low, Number(bar.low));
    }
  }

  addTick(quote) {
    const price = Number(quote.bid);
    const timestampMs = Number(quote.timestamp);
    if (!Number.isFinite(price) || !Number.isFinite(timestampMs)) return;
    const timestamp = Math.floor(timestampMs / 1000);
    const minute = this.bucket(timestamp, 60);

    for (const [timeframe, seconds] of Object.entries(PERIODS)) {
      const bucket = this.bucket(timestamp, seconds);
      const bars = this.mapFor(quote.symbol, timeframe);
      const latest = this.getLatestTime(bars);
      if (latest !== null && bucket < latest) continue;

      const tickKey = this.key(quote.symbol, timeframe, bucket);
      let candle = bars.get(bucket);
      if (!candle) {
        candle = {
          time: bucket,
          open: price,
          high: price,
          low: price,
          close: price,
          firstMinute: minute,
          lastMinute: minute,
        };
        bars.set(bucket, candle);
      } else {
        candle.high = Math.max(candle.high, price);
        candle.low = Math.min(candle.low, price);
        if (timestampMs >= (this.lastTickAt.get(tickKey) ?? -Infinity)) {
          candle.close = price;
        }
      }

      this.tickTouched.add(tickKey);
      this.lastTickAt.set(tickKey, Math.max(this.lastTickAt.get(tickKey) ?? -Infinity, timestampMs));
    }
  }

  getLatestTime(bars) {
    let latest = null;
    for (const time of bars.keys()) {
      if (latest === null || time > latest) latest = time;
    }
    return latest;
  }

  getBars(symbol, timeframe) {
    return [...this.mapFor(symbol, timeframe).values()]
      .sort((a, b) => a.time - b.time)
      .map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));
  }

  getLatest(symbol, timeframe) {
    const bars = this.mapFor(symbol, timeframe);
    let latest = null;
    for (const candle of bars.values()) {
      if (!latest || candle.time > latest.time) latest = candle;
    }
    if (!latest) return null;
    const { time, open, high, low, close } = latest;
    return { time, open, high, low, close };
  }
}
