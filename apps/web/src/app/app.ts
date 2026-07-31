import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AppStateService } from './core/app-state.service';
import { AuthService } from './core/auth.service';
import { ClockService } from './core/clock.service';
import { countdown } from './core/format';
import { LayoutService } from './core/layout.service';
import { LiveService } from './core/live.service';
import { PwaUpdateService } from './core/pwa-update.service';
import { ThemeService } from './core/theme.service';
import { ToastService } from './core/toast.service';
import { IntakePopup } from './modals/intake-popup';
import { QuickAddModal } from './modals/quick-add';
import { ScannerDrawer } from './modals/scanner-drawer';
import { ToastStackComponent } from './ui/toast-stack';

const NAV_ICONS: Record<string, string> = {
  products: 'M4 5v14M8 5v14M12 5v10M16 5v14M20 5v9',
  activity: 'M3 12h5l3-7 3 14 3-7h4',
  settings: 'M4 7h16M4 12h16M4 17h16M9 5v4M15 10v4M8 15v4',
};

/** Bottom-bar glyphs, drawn in the same 24px stroked style as NAV_ICONS. */
const BOTTOM_ICONS = {
  home: 'M3 10.4 12 3.5l9 6.9V20a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1zM9.4 21v-6.4h5.2V21',
  inventory: 'M3 7.6 12 3l9 4.6-9 4.6-9-4.6ZM3 7.6v8.8L12 21l9-4.6V7.6M12 12.2V21',
  shopping: 'M5.6 8h12.8l-1.1 11.2a2 2 0 0 1-2 1.8H8.7a2 2 0 0 1-2-1.8L5.6 8ZM9 8V6.4a3 3 0 0 1 6 0V8',
  more: 'M4.5 5h5.2v5.2H4.5zM14.3 5h5.2v5.2h-5.2zM4.5 14.3h5.2v5.2H4.5zM14.3 14.3h5.2v5.2h-5.2z',
} as const;

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    ToastStackComponent,
    IntakePopup,
    QuickAddModal,
    ScannerDrawer,
  ],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  readonly state = inject(AppStateService);
  readonly layout = inject(LayoutService);
  readonly clock = inject(ClockService);
  private readonly router = inject(Router);
  // Instantiated for its side effect: applies the persisted theme on startup.
  private readonly theme = inject(ThemeService);
  readonly live = inject(LiveService);
  readonly auth = inject(AuthService);
  private readonly pwa = inject(PwaUpdateService);
  private readonly toasts = inject(ToastService);

  readonly scanners = this.state.scanners;
  readonly bottomIcons = BOTTOM_ICONS;
  // Stores/brands/rules/categories live under Settings now.
  readonly iconNav = ['products', 'activity', 'settings'].map((path) => ({
    path,
    label: path[0].toUpperCase() + path.slice(1),
    d: NAV_ICONS[path],
  }));

  readonly time12 = computed(() => {
    const now = this.clock.now();
    const hours = ((now.getHours() + 11) % 12) + 1;
    return `${hours}:${String(now.getMinutes()).padStart(2, '0')}`;
  });
  readonly ampm = computed(() => (this.clock.now().getHours() < 12 ? 'AM' : 'PM'));
  readonly dateLong = computed(() =>
    this.clock.now().toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    }),
  );

  readonly chipScanner = computed(() => this.scanners()[0] ?? null);
  readonly chipMode = computed(() => {
    const scanner = this.chipScanner();
    if (!scanner?.mode) {
      return 'Idle';
    }
    if (scanner.mode === 'LIST') {
      return 'Add To List';
    }
    return scanner.mode[0] + scanner.mode.slice(1).toLowerCase();
  });
  readonly chipCountdown = computed(() => {
    const scanner = this.chipScanner();
    if (!scanner?.mode || !scanner.modeExpiresAt) {
      return '—';
    }
    return countdown(scanner.modeExpiresAt, this.clock.now());
  });

  readonly groceryBadge = computed(() => this.state.overview()?.metrics.groceryCount || null);
  readonly expiringBadge = computed(() => {
    if (this.state.settings()?.badgeExpiringNav === false) {
      return null;
    }
    return this.state.overview()?.metrics.expiringUnits || null;
  });

  constructor() {
    this.auth.refresh().subscribe();
    this.reportAuthError();
    this.live.start();
    this.pwa.start();
    // Refetch on every live change event; the interval is a fallback for
    // countdown drift while the SSE stream is quiet.
    effect(() => {
      this.state.version();
      this.loadScanners();
    });
    effect((onCleanup) => {
      const timer = setInterval(() => this.loadScanners(), this.state.fallbackRefreshMs());
      onCleanup(() => clearInterval(timer));
    });
  }

  loadScanners(): void {
    this.state.refreshScanners();
  }

  /** The auth callback bounces failures back as /?authError=<code>. */
  private reportAuthError(): void {
    const params = new URLSearchParams(window.location.search);
    const message = AuthService.describeError(params.get('authError'));
    if (!message) {
      return;
    }
    this.toasts.show(message, [{ label: 'Sign in', run: () => this.auth.signIn('/') }]);
    // Strip it so a reload does not re-report a stale failure.
    params.delete('authError');
    const query = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (query ? `?${query}` : ''));
  }

  openMoreLink(path: string): void {
    this.state.moreOpen.set(false);
    this.router.navigateByUrl(`/${path}`);
  }
}
