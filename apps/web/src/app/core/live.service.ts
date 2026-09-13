import { Injectable, inject, signal } from '@angular/core';

import { AppStateService } from './app-state.service';
import { AuthService } from './auth.service';

const EVENTS_URL = '/api/v1/web/events';
// EventSource exposes no status code, so a 401 is indistinguishable from the
// network being down — both just retry forever. Probe /auth/me at most this
// often to tell an expired session from a flaky connection.
const AUTH_PROBE_INTERVAL_MS = 15000;

/**
 * Live updates: subscribes to the backend's Server-Sent Events stream (fed by
 * PostgreSQL NOTIFY triggers, so scanner and enrichment writes are included)
 * and bumps AppStateService.version so every open screen refetches.
 */
@Injectable({ providedIn: 'root' })
export class LiveService {
  private readonly state = inject(AppStateService);
  private readonly auth = inject(AuthService);
  private source: EventSource | null = null;
  private debounce: ReturnType<typeof setTimeout> | null = null;
  private lastAuthProbe = 0;

  readonly connected = signal(false);

  start(): void {
    if (this.source || typeof EventSource === 'undefined') {
      return;
    }
    this.source = new EventSource(EVENTS_URL);
    this.source.onopen = () => {
      this.connected.set(true);
      // Catch up on anything missed while disconnected.
      this.state.changed();
    };
    // EventSource reconnects on its own; just reflect the status.
    this.source.onerror = () => {
      this.connected.set(false);
      this.probeSession();
    };
    this.source.onmessage = () => {
      if (this.debounce) {
        clearTimeout(this.debounce);
      }
      this.debounce = setTimeout(() => this.state.changed(), this.state.liveUpdateDebounceMs());
    };
  }

  /** Send the user to Authentik if the stream dropped because we signed out. */
  private probeSession(): void {
    const now = Date.now();
    if (now - this.lastAuthProbe < AUTH_PROBE_INTERVAL_MS) {
      return;
    }
    this.lastAuthProbe = now;
    this.auth.refresh().subscribe((state) => {
      if (state?.enabled && !state.authenticated && !state.internalAccess) {
        this.auth.onUnauthorized();
      }
    });
  }
}
