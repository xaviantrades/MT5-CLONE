/**
 * Browser-side candle construction.
 *
 * Historical M1 bars seed all timeframes. Live quote ticks then update each
 * timeframe directly, using bid as the chart price. This module has no MT5
 * protocol or networking dependencies.
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
  constructor(onUpdate = () => {}) {
    this.onUpdate = onUpdate;
    this.data = new Map();
    this.latestBucket = new Map();
  }

  key(symbol, timeframe) {
    return `${symbol}:${timeframe}`;
  }

  bucket(timestamp, timeframe) {
    const seconds = PERIODS[timeframe];
    return Math.floor(timestamp / seconds) * seconds;
  }

  mapFor(symbol, timeframe) {
    const key = this.key(symbol, timeframe);
    if (!this.data.has(key)) this.data.set(key, new Map());
    return this.data.get(key);
  }

  mergeBar(symbol, timeframe, source) {
    const time = this.bucket(source.timestamp, timeframe);
    const bars = this.mapFor(symbol, timeframe);
    const existing = bars.get(time);

    if (!existing) {
      bars.set(time, {
        time,
        open: source.open,
        high: source.high,
        low: source.low,
        close: source.close,
        volume: Number(source.volume ?? 0),
      });
    } else {
      existing.high = Math.max(existing.high, source.high);
      existing.low = Math.min(existing.low, source.low);
      existing.close = source.close;
      existing.volume += Number(source.volume ?? 0);
    }

    this.latestBucket.set(this.key(symbol, timeframe), Math.max(
      this.latestBucket.get(this.key(symbol, timeframe)) ?? 0, time,
    ));
    return bars.get(time);
  }

  seed(symbol, minuteBars) {
    for (const timeframe of Object.keys(PERIODS)) {
      this.data.set(this.key(symbol, timeframe), new Map());
      this.latestBucket.delete(this.key(symbol, timeframe));
    }

    const sorted = [...minuteBars].sort((a, b) => a.timestamp - b.timestamp);
    for (const bar of sorted) {
      this.mergeBar(symbol, "M1", bar);
      for (const timeframe of Object.keys(PERIODS)) {
        if (timeframe !== "M1") this.mergeBar(symbol, timeframe, bar);
      }
    }
  }

  addMinuteBar(bar) {
    if (!bar || !PERIODS.M1) return;
    const key = this.key(bar.symbol, "M1");
    const incomingTime = this.bucket(bar.timestamp, "M1");
    const previousLatest = this.latestBucket.get(key) ?? 0;

    this.mergeBar(bar.symbol, "M1", bar);
    for (const timeframe of Object.keys(PERIODS)) {
      if (timeframe !== "M1") this.mergeBar(bar.symbol, timeframe, bar);
    }

    // Historical pages can arrive after live ticks. Never send an old candle
    // to Lightweight Charts' update() API, which only accepts the newest bar.
    if (incomingTime >= previousLatest) {
      this.onUpdate(bar.symbol, "M1", this.getLatest(bar.symbol, "M1"));
    }
  }

  addTick(quote) {
    const price = Number(quote.bid);
    const timestamp = Math.floor(new Date(quote.timestamp).getTime() / 1000);
    if (!Number.isFinite(price) || !Number.isFinite(timestamp)) return;

    for (const timeframe of Object.keys(PERIODS)) {
      const time = this.bucket(timestamp, timeframe);
      const bars = this.mapFor(quote.symbol, timeframe);
      const latest = this.latestBucket.get(this.key(quote.symbol, timeframe)) ?? 0;

      // Ignore delayed ticks that would move a candle backwards.
      if (time < latest) continue;

      let candle = bars.get(time);
      if (!candle) {
        candle = { time, open: price, high: price, low: price, close: price, volume: 0 };
        bars.set(time, candle);
        this.latestBucket.set(this.key(quote.symbol, timeframe), time);
      }

      candle.high = Math.max(candle.high, price);
      candle.low = Math.min(candle.low, price);
      candle.close = price;
      candle.volume += 1;
      this.onUpdate(quote.symbol, timeframe, candle);
    }
  }

  getLatest(symbol, timeframe) {
    const bars = this.mapFor(symbol, timeframe);
    const values = [...bars.values()];
    return values.length ? values[values.length - 1] : null;
  }

  getCandles(symbol, timeframe) {
    return [...this.mapFor(symbol, timeframe).values()]
      .sort((a, b) => a.time - b.time);
  }

  getPeriods() {
    return Object.keys(PERIODS);
  }
}
