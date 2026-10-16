import { Injectable } from '@angular/core';
import type { SupervisorEvent } from '../api/generated/control-api/model/supervisorEvent';

export type ConnectionStatus = 'connecting' | 'connected' | 'stale' | 'disconnected' | 'unauthorized';

export interface StreamCallbacks {
  status(value: ConnectionStatus): void;
  event(value: SupervisorEvent): boolean;
  reset(): void;
  unauthorized(): void;
}

class ResetRequired extends Error {}

/** Fetch is used because EventSource cannot distinguish a 410 response from a network error. */
@Injectable({ providedIn: 'root' })
export class SupervisorStream {
  private generation = 0;
  private controller: AbortController | null = null;

  start(snapshotCursor: string, callbacks: StreamCallbacks): void {
    this.stop();
    const generation = this.generation;
    void this.run(snapshotCursor, callbacks, generation);
  }

  stop(): void {
    this.generation++;
    this.controller?.abort();
    this.controller = null;
  }

  private async run(snapshotCursor: string, callbacks: StreamCallbacks, generation: number): Promise<void> {
    let lastEventCursor: string | null = null;
    let retry = 0;
    while (this.generation === generation) {
      callbacks.status('connecting');
      const controller = new AbortController();
      this.controller = controller;
      let lastSignalAt = Date.now();
      let stale = false;
      const watchdog = setInterval(() => {
        if (Date.now() - lastSignalAt >= 45_000) {
          stale = true;
          callbacks.status('stale');
          controller.abort();
        }
      }, 1_000);
      try {
        const url = new URL('/api/v1/events', window.location.origin);
        url.searchParams.set('cursor', snapshotCursor);
        const headers = new Headers({ Accept: 'text/event-stream' });
        if (lastEventCursor) headers.set('Last-Event-ID', lastEventCursor);
        const response = await fetch(url, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers,
          signal: controller.signal,
        });
        if (response.status === 401 || response.status === 403) {
          callbacks.status('unauthorized');
          callbacks.unauthorized();
          return;
        }
        if (response.status === 410) throw new ResetRequired();
        if (!response.ok || !response.body) throw new Error('Stream unavailable');
        callbacks.status('connected');
        retry = 0;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let frame = emptyFrame();
        let frameSize = 0;
        const acceptLine = (line: string): void => {
          if (line.startsWith(':')) {
            lastSignalAt = Date.now();
            return;
          }
          if (line === '') {
            if (!frame.data.length) {
              frame = emptyFrame();
              frameSize = 0;
              return;
            }
            lastSignalAt = Date.now();
            if (frame.event === 'reset-required') throw new ResetRequired();
            let parsed: unknown;
            try {
              parsed = JSON.parse(frame.data.join('\n'));
            } catch {
              throw new ResetRequired();
            }
            if (!isSupervisorEvent(parsed) || !frame.id || parsed.cursor !== frame.id ||
                parsed.eventId.length === 0 || parsed.eventType !== frame.event) {
              throw new ResetRequired();
            }
            if (!callbacks.event(parsed)) throw new ResetRequired();
            lastEventCursor = frame.id;
            frame = emptyFrame();
            frameSize = 0;
            return;
          }
          const separator = line.indexOf(':');
          const field = separator < 0 ? line : line.slice(0, separator);
          let value = separator < 0 ? '' : line.slice(separator + 1);
          if (value.startsWith(' ')) value = value.slice(1);
          if (field === 'data') {
            frameSize += value.length;
            if (frameSize > 1_048_576) throw new ResetRequired();
            frame.data.push(value);
          }
          else if (field === 'id') frame.id = value;
          else if (field === 'event') frame.event = value;
        };

        while (this.generation === generation) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          if (buffer.length > 1_048_576) throw new ResetRequired();
          let boundary: number;
          while ((boundary = buffer.indexOf('\n')) >= 0) {
            let line = buffer.slice(0, boundary);
            if (line.endsWith('\r')) line = line.slice(0, -1);
            buffer = buffer.slice(boundary + 1);
            acceptLine(line);
          }
        }
      } catch (error) {
        if (this.generation !== generation) return;
        if (error instanceof ResetRequired) {
          callbacks.reset();
          return;
        }
      } finally {
        clearInterval(watchdog);
        if (this.controller === controller) this.controller = null;
        controller.abort();
      }
      if (this.generation !== generation) return;
      if (!stale) callbacks.status('disconnected');
      retry++;
      await delay(Math.min(30_000, 1_000 * 2 ** Math.min(retry, 5)));
    }
  }
}

function emptyFrame(): { id: string; event: string; data: string[] } {
  return { id: '', event: '', data: [] };
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function isSupervisorEvent(value: unknown): value is SupervisorEvent {
  if (typeof value !== 'object' || value === null) return false;
  const event = value as Partial<SupervisorEvent>;
  return boundedText(event.eventId, 256) && boundedText(event.cursor, 256) &&
    boundedText(event.eventType, 128) && /^[a-z]+\.[a-z]+\.v[1-9][0-9]*$/.test(event.eventType) &&
    boundedText(event.aggregateId, 256) &&
    typeof event.traceId === 'string' && /^[0-9a-f]{32}$/.test(event.traceId) &&
    boundedText(event.occurredAt, 64) &&
    !Number.isNaN(Date.parse(event.occurredAt!)) && event.occurredAt!.endsWith('Z') &&
    typeof event.payload === 'object' && event.payload !== null && !Array.isArray(event.payload) &&
    typeof event.aggregateVersion === 'number' && Number.isSafeInteger(event.aggregateVersion) &&
    event.aggregateVersion > 0;
}

function boundedText(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max;
}
