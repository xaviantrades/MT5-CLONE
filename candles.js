/**
 * Multi-timeframe OHLC builder.
 * Broker M1 bars seed history; live bid ticks update each timeframe directly.
 * All timestamps are UTC Unix seconds.
 */
const PERIODS = Object.freeze({
  M1: 60, M5: 300, M15: 900, H1: 3600, H4: 14400, D1: 86400,
});

export class CandleBuilder {
  constructor() {
    this.data = new Map();
    this.latestTimes = new Map();
    this.tickTouched = new Set();
    this.lastTickAt = new Map();
  }

  key(symbol, timeframe, bucket) {
    return symbol + "|" + timeframe + "|" + bucket;
  }

  seriesKey(symbol, timeframe) {
    return symbol + "|" + timeframe;
  }

  bucket(timestamp, seconds) {
    return Math.floor(timestamp / seconds) * seconds;
  }

  mapFor(symbol, timeframe) {
    const key = this.seriesKey(symbol, timeframe);
    if (!this.data.has(key)) this.data.set(key, new Map());
    return this.data.get(key);
  }

  getPeriods() {
    return Object.keys(PERIODS);
  }

  seed(symbol, minuteBars) {
    // Merge rather than clear: live ticks can arrive before history returns.
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
      const seriesKey = this.seriesKey(bar.symbol, timeframe);

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
      } else {
        if (minuteTime <= existing.firstMinute) {
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

      const latest = this.latestTimes.get(seriesKey);
      if (latest === undefined || bucket > latest) this.latestTimes.set(seriesKey, bucket);
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
      const seriesKey = this.seriesKey(quote.symbol, timeframe);
      const latest = this.latestTimes.get(seriesKey);
      if (latest !== undefined && bucket < latest) continue;

      const tickKey = this.key(quote.symbol, timeframe, bucket);
      let candle = bars.get(bucket);
      if (!candle) {
        candle = {
          time: bucket, open: price, high: price, low: price, close: price,
          firstMinute: minute, lastMinute: minute,
        };
        bars.set(bucket, candle);
      } else {
        candle.high = Math.max(candle.high, price);
        candle.low = Math.min(candle.low, price);
        if (timestampMs >= (this.lastTickAt.get(tickKey) ?? -Infinity)) candle.close = price;
      }

      this.latestTimes.set(seriesKey, bucket);
      this.tickTouched.add(tickKey);
      this.lastTickAt.set(tickKey, Math.max(this.lastTickAt.get(tickKey) ?? -Infinity, timestampMs));
    }
  }

  getBars(symbol, timeframe) {
    return [...this.mapFor(symbol, timeframe).values()]
      .sort((a, b) => a.time - b.time)
      .map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));
  }

  getLatest(symbol, timeframe) {
    const latestTime = this.latestTimes.get(this.seriesKey(symbol, timeframe));
    if (latestTime === undefined) return null;
    const candle = this.mapFor(symbol, timeframe).get(latestTime);
    if (!candle) return null;
    const { time, open, high, low, close } = candle;
    return { time, open, high, low, close };
  }
}
