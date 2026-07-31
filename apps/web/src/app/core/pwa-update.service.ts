import { Injectable, inject } from '@angular/core';
import { SwUpdate } from '@angular/service-worker';

import { ToastService } from './toast.service';

/** Installed on a phone, the app can stay open for days between cold starts. */
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

@Injectable({ providedIn: 'root' })
export class PwaUpdateService {
  private readonly updates = inject(SwUpdate);
  private readonly toasts = inject(ToastService);

  start(): void {
    // Disabled in dev builds and wherever service workers are unavailable.
    if (!this.updates.isEnabled) {
      return;
    }

    this.updates.versionUpdates.subscribe((event) => {
      if (event.type === 'VERSION_READY') {
        this.toasts.show('A new version of Larder is ready.', [
          { label: 'Reload', run: () => document.location.reload() },
        ]);
      }
    });

    // Cached app shell no longer matches anything on the server — only a
    // reload can recover it.
    this.updates.unrecoverable.subscribe(() => {
      this.toasts.show('Larder needs to reload to recover.', [
        { label: 'Reload', run: () => document.location.reload() },
      ]);
    });

    setInterval(() => {
      // Rejects while offline; nothing to do but wait for the next tick.
      this.updates.checkForUpdate().catch(() => undefined);
    }, CHECK_INTERVAL_MS);
  }
}
