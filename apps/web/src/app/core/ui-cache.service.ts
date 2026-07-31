import { Injectable } from '@angular/core';

export type UiCacheKey =
  | 'settings'
  | 'overview'
  | 'activity'
  | 'stores'
  | 'tax-locations'
  | 'storage-locations'
  | 'scanners';

interface CacheEnvelope<T> {
  schema: number;
  savedAt: number;
  value: T;
}

const CACHE_SCHEMA = 1;
const CACHE_PREFIX = `larder.ui-cache.v${CACHE_SCHEMA}.`;
const HOUR_MS = 60 * 60 * 1000;
const MAX_AGE: Record<UiCacheKey, number> = {
  settings: 7 * 24 * HOUR_MS,
  overview: 12 * HOUR_MS,
  activity: 12 * HOUR_MS,
  stores: 7 * 24 * HOUR_MS,
  'tax-locations': 7 * 24 * HOUR_MS,
  'storage-locations': 7 * 24 * HOUR_MS,
  scanners: HOUR_MS,
};

/**
 * Small stale-while-revalidate cache for instantly hydrating an iframe reopen.
 * Every cached value is refreshed from the API in the background; this never
 * caches mutations, credentials, or HTTP responses themselves.
 */
@Injectable({ providedIn: 'root' })
export class UiCacheService {
  read<T>(key: UiCacheKey): T | null {
    const storage = this.storage();
    if (!storage) {
      return null;
    }
    const storageKey = CACHE_PREFIX + key;
    try {
      const raw = storage.getItem(storageKey);
      if (!raw) {
        return null;
      }
      const cached = JSON.parse(raw) as Partial<CacheEnvelope<T>>;
      const fresh =
        cached.schema === CACHE_SCHEMA &&
        typeof cached.savedAt === 'number' &&
        Date.now() - cached.savedAt <= MAX_AGE[key] &&
        'value' in cached;
      if (!fresh) {
        this.removeFrom(storage, storageKey);
        return null;
      }
      return cached.value as T;
    } catch {
      this.removeFrom(storage, storageKey);
      return null;
    }
  }

  write<T>(key: UiCacheKey, value: T): void {
    const storage = this.storage();
    if (!storage) {
      return;
    }
    try {
      const cached: CacheEnvelope<T> = {
        schema: CACHE_SCHEMA,
        savedAt: Date.now(),
        value,
      };
      storage.setItem(CACHE_PREFIX + key, JSON.stringify(cached));
    } catch {
      // Storage can be unavailable or quota-limited in embedded browsers.
    }
  }

  remove(key: UiCacheKey): void {
    const storage = this.storage();
    if (!storage) {
      return;
    }
    this.removeFrom(storage, CACHE_PREFIX + key);
  }

  private removeFrom(storage: Storage, key: string): void {
    try {
      storage.removeItem(key);
    } catch {
      // Embedded browser storage may be blocked; caching is only an optimization.
    }
  }

  private storage(): Storage | null {
    try {
      return typeof window === 'undefined' ? null : window.localStorage;
    } catch {
      return null;
    }
  }
}
