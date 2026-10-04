/**
 * Open v2 Listen streams and what each one asked for. The server holds a
 * listener only while its stream is open; nothing about a subscription
 * survives the stream.
 */
import type { DeepPartial, ListenEvent, NotificationFilter } from "../../generated/mcp_v2.js";

export interface Listener {
  filter: NotificationFilter;
  send: (event: DeepPartial<ListenEvent>) => void;
}

export class Listeners {
  private _open = new Set<Listener>();

  get size(): number {
    return this._open.size;
  }

  add(filter: NotificationFilter, send: Listener["send"]): Listener {
    const listener = { filter, send };
    this._open.add(listener);
    return listener;
  }

  remove(listener: Listener): void {
    this._open.delete(listener);
  }

  private _publish(event: DeepPartial<ListenEvent>, wanted: (filter: NotificationFilter) => boolean): void {
    for (const listener of this._open) {
      if (wanted(listener.filter)) listener.send(event);
    }
  }

  toolsListChanged(): void {
    this._publish({ event: { $case: "toolsListChanged", toolsListChanged: {} } }, (f) => f.toolsListChanged);
  }

  promptsListChanged(): void {
    this._publish(
      { event: { $case: "promptsListChanged", promptsListChanged: {} } },
      (f) => f.promptsListChanged,
    );
  }

  resourcesListChanged(): void {
    this._publish(
      { event: { $case: "resourcesListChanged", resourcesListChanged: {} } },
      (f) => f.resourcesListChanged,
    );
  }

  resourceUpdated(uri: string): void {
    this._publish({ event: { $case: "resourceUpdated", resourceUpdated: { uri } } }, (f) =>
      f.resourceSubscriptions.includes(uri),
    );
  }
}
