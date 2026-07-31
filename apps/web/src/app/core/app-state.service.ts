import { Injectable, computed, effect, inject, signal } from '@angular/core';
import { Observable, finalize } from 'rxjs';

import { ApiService } from './api.service';
import {
  HouseholdSettings,
  OverviewResponse,
  ScannerRow,
  StorageLocationRow,
  StoreRow,
  TaxLocationRow,
} from './models';
import { UiCacheService } from './ui-cache.service';

/**
 * Cross-screen state: household settings, nav badges, and the globally
 * reachable overlays (quick add, scanner drawer, mobile "more" sheet).
 */
@Injectable({ providedIn: 'root' })
export class AppStateService {
  private readonly api = inject(ApiService);
  private readonly cache = inject(UiCacheService);
  private readonly refreshes = new Set<string>();

  readonly settings = signal(this.cache.read<HouseholdSettings>('settings'));
  readonly overview = signal(this.cache.read<OverviewResponse>('overview'));
  readonly stores = signal(this.cache.read<StoreRow[]>('stores') ?? []);
  readonly taxLocations = signal(this.cache.read<TaxLocationRow[]>('tax-locations') ?? []);
  readonly storageLocations = signal(
    this.cache.read<StorageLocationRow[]>('storage-locations') ?? [],
  );
  readonly scanners = signal(this.cache.read<ScannerRow[]>('scanners') ?? []);
  /** Archived locations stay valid on old units but are hidden from selectors. */
  readonly activeStorageLocations = computed(() => this.storageLocations().filter((l) => l.active));

  readonly quickAddOpen = signal(false);
  readonly drawerOpen = signal(false);
  readonly moreOpen = signal(false);

  /** Bumped after any mutation so open screens can refetch. */
  readonly version = signal(0);

  constructor() {
    this.refreshSettings();
    this.refreshBadges();
    this.refreshReferenceDataWhenIdle();
    effect((onCleanup) => {
      const timer = setInterval(() => this.refreshBadges(), this.fallbackRefreshMs());
      onCleanup(() => clearInterval(timer));
    });
  }

  refreshSettings(): void {
    this.refresh(
      'settings',
      () => this.api.settings(),
      (settings) => {
        this.setSettings(settings);
      },
    );
  }

  refreshBadges(): void {
    this.refresh(
      'overview',
      () => this.api.overview(),
      (overview) => {
        this.overview.set(overview);
        this.cache.write('overview', overview);
      },
    );
  }

  refreshStores(): void {
    this.refresh(
      'stores',
      () => this.api.stores(),
      ({ stores }) => {
        this.stores.set(stores);
        this.cache.write('stores', stores);
      },
    );
  }

  refreshTaxLocations(): void {
    this.refresh(
      'tax-locations',
      () => this.api.taxLocations(),
      ({ locations }) => {
        this.taxLocations.set(locations);
        this.cache.write('tax-locations', locations);
      },
    );
  }

  refreshStorageLocations(): void {
    this.refresh(
      'storage-locations',
      () => this.api.storageLocations(),
      ({ storageLocations }) => {
        this.storageLocations.set(storageLocations);
        this.cache.write('storage-locations', storageLocations);
      },
    );
  }

  refreshScanners(): void {
    this.refresh(
      'scanners',
      () => this.api.scanners(),
      ({ scanners }) => {
        this.scanners.set(scanners);
        this.cache.write('scanners', scanners);
      },
    );
  }

  setSettings(settings: HouseholdSettings): void {
    this.settings.set(settings);
    this.cache.write('settings', settings);
  }

  changed(): void {
    this.version.update((v) => v + 1);
    this.cache.remove('overview');
    this.refreshBadges();
  }

  windowDays(): number {
    return this.settings()?.expiringWindowDays ?? 4;
  }

  intakePopupTimeoutMs(): number {
    return (this.settings()?.intakePopupTimeoutSeconds ?? 30) * 1000;
  }

  toastDurationMs(): number {
    return (this.settings()?.toastDurationSeconds ?? 3) * 1000;
  }

  confirmationTimeoutMs(): number {
    return (this.settings()?.confirmationTimeoutSeconds ?? 3) * 1000;
  }

  liveUpdateDebounceMs(): number {
    return this.settings()?.liveUpdateDebounceMs ?? 300;
  }

  fallbackRefreshMs(): number {
    return (this.settings()?.fallbackRefreshSeconds ?? 60) * 1000;
  }

  private refresh<T>(key: string, request: () => Observable<T>, apply: (value: T) => void): void {
    if (this.refreshes.has(key)) {
      return;
    }
    this.refreshes.add(key);
    request()
      .pipe(finalize(() => this.refreshes.delete(key)))
      .subscribe({ next: apply, error: () => undefined });
  }

  private refreshReferenceDataWhenIdle(): void {
    const refresh = () => {
      this.refreshStores();
      this.refreshTaxLocations();
      this.refreshStorageLocations();
    };
    if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(refresh, { timeout: 1000 });
    } else {
      setTimeout(refresh, 0);
    }
  }
}
