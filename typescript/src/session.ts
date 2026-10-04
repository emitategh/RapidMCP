/**
 * AsyncQueue — unbounded async FIFO queue.
 * enqueue() pushes; dequeue() returns a promise that resolves
 * when an item is available.
 */
export class AsyncQueue<T> {
  private _buffer: T[] = [];
  private _waiters: Array<(value: T) => void> = [];

  enqueue(item: T): void {
    const waiter = this._waiters.shift();
    if (waiter) {
      waiter(item);
    } else {
      this._buffer.push(item);
    }
  }

  dequeue(): Promise<T> {
    const item = this._buffer.shift();
    if (item !== undefined) {
      return Promise.resolve(item);
    }
    return new Promise<T>((resolve) => {
      this._waiters.push(resolve);
    });
  }
}

/**
 * Settle with *promise*, or reject with `onTimeout()` after *ms*. The timer is
 * cleared as soon as the promise settles, so finished requests leave nothing behind.
 */
export function withTimeout<T>(promise: Promise<T>, ms: number, onTimeout: () => Error): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(onTimeout()), ms);
    // Don't keep the process alive just for this timer.
    if (typeof timer === "object" && "unref" in timer) (timer as NodeJS.Timeout).unref();
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

/**
 * PendingRequests — track in-flight outbound requests
 * and correlate responses by requestId.
 */
export class PendingRequests {
  private _counter = 0n;
  private _pending = new Map<bigint, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();

  nextId(): bigint {
    this._counter += 1n;
    return this._counter;
  }

  create(requestId: bigint): Promise<unknown> {
    return new Promise((resolve, reject) => {
      this._pending.set(requestId, { resolve, reject });
    });
  }

  resolve(requestId: bigint, result: unknown): void {
    const entry = this._pending.get(requestId);
    if (entry) {
      this._pending.delete(requestId);
      entry.resolve(result);
    }
  }

  reject(requestId: bigint, error: Error): void {
    const entry = this._pending.get(requestId);
    if (entry) {
      this._pending.delete(requestId);
      entry.reject(error);
    }
  }

  /** Forget a request nobody is waiting on any more (e.g. after a timeout). */
  discard(requestId: bigint): void {
    this._pending.delete(requestId);
  }

  cancelAll(): void {
    for (const entry of this._pending.values()) {
      entry.reject(new Error("cancelled"));
    }
    this._pending.clear();
  }

  rejectAll(error: Error): void {
    for (const entry of this._pending.values()) {
      entry.reject(error);
    }
    this._pending.clear();
  }
}

/**
 * NotificationRegistry — fan-out notification callbacks
 * keyed by notification type name.
 */
export class NotificationRegistry {
  private _handlers = new Map<string, Array<(payload: string) => void | Promise<void>>>();

  register(type: string, handler: (payload: string) => void | Promise<void>): void {
    let list = this._handlers.get(type);
    if (!list) {
      list = [];
      this._handlers.set(type, list);
    }
    list.push(handler);
  }

  /** True when at least one handler is registered for *type*. */
  has(type: string): boolean {
    return (this._handlers.get(type)?.length ?? 0) > 0;
  }

  async dispatch(type: string, payload: string): Promise<void> {
    const list = this._handlers.get(type);
    if (!list) return;
    for (const handler of list) {
      await handler(payload);
    }
  }
}
