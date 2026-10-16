import { computed, inject } from '@angular/core';
import { signalStore, patchState, withComputed, withHooks, withMethods, withState } from '@ngrx/signals';
import type { ModeState, RiskPolicy, SupervisorEvent, SupervisorSnapshot } from '../api/generated/control-api/model/models';
import { httpStatus, safeError, SupervisorApi, type ControlCommand } from './supervisor-api';
import { InvalidPublicDto } from './public-dto-schema';
import { RESOURCES, type Page, type Resource } from './supervisor.models';
import { SupervisorStream, type ConnectionStatus } from './supervisor-stream';

interface SupervisorState {
  snapshot: SupervisorSnapshot | null;
  mode: ModeState | null;
  policy: RiskPolicy | null;
  connectionStatus: ConnectionStatus;
  authenticated: boolean;
  error: string | null;
  pages: Record<Resource, Page>;
  filters: { traceId: string };
  pendingCommand: boolean;
}

function emptyPage(): Page {
  return { items: [], nextCursor: null, loading: false, error: null };
}

function emptyPages(): Record<Resource, Page> {
  return Object.fromEntries(RESOURCES.map(resource => [resource, emptyPage()])) as Record<Resource, Page>;
}

const initialState: SupervisorState = {
  snapshot: null,
  mode: null,
  policy: null,
  connectionStatus: 'disconnected',
  authenticated: false,
  error: null,
  pages: emptyPages(),
  filters: { traceId: '' },
  pendingCommand: false,
};

export const SupervisorStore = signalStore(
  { providedIn: 'root' },
  withState(initialState),
  withComputed(({ mode, authenticated, connectionStatus, pendingCommand }) => ({
    canResume: computed(() => {
      const current = mode();
      return authenticated() && connectionStatus() === 'connected' && !pendingCommand() &&
        current?.mode === 'PAPER' && current.pauseNew && !current.killSwitch &&
        current.health === 'HEALTHY' && current.reconciliation === 'CURRENT';
    }),
  })),
  withMethods((store, api = inject(SupervisorApi), stream = inject(SupervisorStream)) => {
    let epoch = 0;
    let initializing: Promise<void> | null = null;
    let refreshing: Promise<void> | null = null;
    let refreshAgain = false;
    let activationInProgress = false;
    const commandsInFlight = new Set<ControlCommand>();
    let pending: { kind: ControlCommand; key: string } | null = null;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const pageTokens = Object.fromEntries(RESOURCES.map(resource => [resource, 0])) as Record<Resource, number>;
    const seenEvents = new Set<string>();
    const seenOrder: string[] = [];
    const aggregateVersions = new Map<string, number>();

    function replacePage(resource: Resource, page: Page): void {
      patchState(store, { pages: { ...store.pages(), [resource]: page } });
    }

    function clearSession(): void {
      epoch++;
      stream.stop();
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = null;
      pending = null;
      commandsInFlight.clear();
      seenEvents.clear();
      seenOrder.length = 0;
      aggregateVersions.clear();
      for (const resource of RESOURCES) pageTokens[resource]++;
      patchState(store, {
        snapshot: null, mode: null, policy: null, authenticated: false,
        connectionStatus: 'unauthorized', pages: emptyPages(), pendingCommand: false,
        error: 'Eine Operator-Sitzung ist erforderlich.',
      });
    }

    async function loadPage(resource: Resource, more = false): Promise<void> {
      if (!store.authenticated()) return;
      const current = store.pages()[resource];
      if (current.loading || (more && !current.nextCursor)) return;
      const requestEpoch = epoch;
      const token = ++pageTokens[resource];
      const cursor = more ? current.nextCursor || undefined : undefined;
      const traceId = resource === 'audit' ? store.filters().traceId : undefined;
      replacePage(resource, { ...current, loading: true, error: null });
      try {
        const data = await api.page(resource, cursor, traceId);
        if (requestEpoch !== epoch || token !== pageTokens[resource]) return;
        replacePage(resource, {
          items: more ? [...current.items, ...data.items] : data.items,
          nextCursor: data.nextCursor,
          loading: false,
          error: null,
        });
      } catch (error) {
        if (requestEpoch !== epoch || token !== pageTokens[resource]) return;
        if (httpStatus(error) === 401 || httpStatus(error) === 403) {
          clearSession();
          return;
        }
        if (httpStatus(error) === 410 && more) {
          replacePage(resource, emptyPage());
          await loadPage(resource);
          return;
        }
        replacePage(resource, { ...current, loading: false, error: safeError(error) });
      }
    }

    function scheduleViewRefresh(): void {
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        void refresh();
        for (const resource of RESOURCES) {
          pageTokens[resource]++;
          replacePage(resource, emptyPage());
          void loadPage(resource);
        }
      }, 150);
    }

    function eventReceived(event: SupervisorEvent): boolean {
      if (seenEvents.has(event.eventId)) return true;
      const prior = aggregateVersions.get(event.aggregateId);
      if (prior !== undefined && event.aggregateVersion > prior + 1) return false;
      seenEvents.add(event.eventId);
      seenOrder.push(event.eventId);
      if (seenOrder.length > 1_024) seenEvents.delete(seenOrder.shift()!);
      if (prior === undefined || event.aggregateVersion > prior) {
        aggregateVersions.set(event.aggregateId, event.aggregateVersion);
        scheduleViewRefresh();
      }
      return true;
    }

    async function resnapshot(): Promise<void> {
      epoch++;
      const requestEpoch = epoch;
      stream.stop();
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = null;
      seenEvents.clear();
      seenOrder.length = 0;
      aggregateVersions.clear();
      for (const resource of RESOURCES) pageTokens[resource]++;
      patchState(store, {
        snapshot: null, mode: null, policy: null, pages: emptyPages(),
        connectionStatus: 'connecting', error: null,
      });
      try {
        const snapshot = await api.snapshot();
        if (requestEpoch !== epoch) return;
        patchState(store, {
          snapshot, mode: snapshot.mode, authenticated: true,
          connectionStatus: 'connecting', error: null,
        });
        stream.start(snapshot.cursor, {
          status: value => {
            if (requestEpoch === epoch) patchState(store, { connectionStatus: value });
          },
          event: value => requestEpoch === epoch && eventReceived(value),
          reset: () => {
            if (requestEpoch === epoch) void resnapshot();
          },
          unauthorized: () => {
            if (requestEpoch === epoch) clearSession();
          },
        });
        void loadPolicy(requestEpoch);
        for (const resource of RESOURCES) void loadPage(resource);
      } catch (error) {
        if (requestEpoch !== epoch) return;
        if (httpStatus(error) === 401 || httpStatus(error) === 403) {
          clearSession();
        } else {
          patchState(store, { authenticated: false, connectionStatus: 'disconnected', error: safeError(error) });
        }
      }
    }

    async function loadPolicy(requestEpoch: number): Promise<void> {
      try {
        const policy = await api.policy();
        if (requestEpoch === epoch) patchState(store, { policy });
      } catch (error) {
        if (requestEpoch !== epoch) return;
        if (httpStatus(error) === 401 || httpStatus(error) === 403) clearSession();
        else patchState(store, { error: safeError(error) });
      }
    }

    async function refresh(): Promise<void> {
      if (!store.authenticated()) return;
      if (refreshing) {
        refreshAgain = true;
        return refreshing;
      }
      const requestEpoch = epoch;
      patchState(store, { error: null });
      refreshing = Promise.all([
        (async () => {
          try {
            const snapshot = await api.snapshot();
            if (requestEpoch !== epoch) return;
            // A display refresh never changes the SSE resume cursor.
            patchState(store, { snapshot, mode: snapshot.mode });
          } catch (error) {
            if (requestEpoch !== epoch) return;
            if (httpStatus(error) === 401 || httpStatus(error) === 403) clearSession();
            else patchState(store, { error: safeError(error) });
          }
        })(),
        loadPolicy(requestEpoch),
      ]).then(() => undefined);
      try {
        await refreshing;
      } finally {
        refreshing = null;
        if (refreshAgain && store.authenticated()) {
          refreshAgain = false;
          scheduleViewRefresh();
        }
      }
    }

    async function execute(kind: ControlCommand, retry = false): Promise<void> {
      if (commandsInFlight.has(kind)) return;
      if (pending && !retry && kind === 'resume') {
        patchState(store, { error: 'Bitte die ausstehende Anfrage zuerst klären.' });
        return;
      }
      if (!store.authenticated()) {
        patchState(store, { error: 'Eine Operator-Sitzung ist erforderlich.' });
        return;
      }
      const command = retry ? pending : { kind, key: crypto.randomUUID() };
      if (!command) return;
      // A new PAUSE or KILL must remain available even if another control request is ambiguous.
      pending = command;
      commandsInFlight.add(command.kind);
      const requestEpoch = epoch;
      patchState(store, { pendingCommand: true, error: null });
      try {
        const mode = await api.command(command.kind, command.key);
        if (requestEpoch !== epoch) return;
        if (pending === command) {
          pending = null;
          patchState(store, { mode, pendingCommand: commandsInFlight.size > 1 });
        }
        await refresh();
      } catch (error) {
        if (requestEpoch !== epoch) return;
        if (httpStatus(error) === 401 || httpStatus(error) === 403) {
          clearSession();
          return;
        }
        // A lost response or an invalid success DTO leaves the backend outcome uncertain.
        // Only an explicit retry may reuse this command's key.
        if (pending !== command) return;
        if (httpStatus(error) !== 0 && !(error instanceof InvalidPublicDto)) pending = null;
        patchState(store, {
          pendingCommand: pending !== null || commandsInFlight.size > 1,
          error: safeError(error),
        });
      } finally {
        commandsInFlight.delete(command.kind);
        if (requestEpoch === epoch) patchState(store, { pendingCommand: pending !== null || commandsInFlight.size > 0 });
      }
    }

    return {
      initialize(): Promise<void> {
        if (initializing) return initializing;
        if (store.authenticated()) return Promise.resolve();
        initializing = resnapshot().finally(() => { initializing = null; });
        return initializing;
      },
      async activate(code: string): Promise<boolean> {
        if (activationInProgress) return false;
        activationInProgress = true;
        try {
          await api.activate(code);
          await resnapshot();
          return store.authenticated();
        } catch (error) {
          patchState(store, { error: safeError(error) });
          return false;
        } finally {
          activationInProgress = false;
        }
      },
      refresh,
      loadPage,
      filterChanged(traceId: string): void {
        pageTokens.audit++;
        patchState(store, {
          filters: { traceId: traceId.trim() },
          pages: { ...store.pages(), audit: emptyPage() },
        });
      },
      pauseRequested(): Promise<void> { return execute('pause'); },
      resumeRequested(): Promise<void> { return execute('resume'); },
      killRequested(): Promise<void> { return execute('kill'); },
      retryPending(): Promise<void> {
        return pending ? execute(pending.kind, true) : Promise.resolve();
      },
      destroy(): void {
        epoch++;
        stream.stop();
        if (refreshTimer) clearTimeout(refreshTimer);
      },
    };
  }),
  withHooks({ onDestroy(store) { store.destroy(); } }),
);
