import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { UiCacheService } from './ui-cache.service';

const values = new Map<string, string>();
const storage: Storage = {
  get length() {
    return values.size;
  },
  clear: () => values.clear(),
  getItem: (key) => values.get(key) ?? null,
  key: (index) => [...values.keys()][index] ?? null,
  removeItem: (key) => void values.delete(key),
  setItem: (key, value) => void values.set(key, String(value)),
};

Object.defineProperty(window, 'localStorage', {
  configurable: true,
  value: storage,
});

describe('UiCacheService', () => {
  let cache: UiCacheService;

  beforeEach(() => {
    storage.clear();
    cache = new UiCacheService();
  });

  afterEach(() => vi.restoreAllMocks());

  it('round-trips cached UI state', () => {
    const overview = { metrics: { unitsInStock: 4 } };
    cache.write('overview', overview);

    expect(cache.read('overview')).toEqual(overview);
  });

  it('drops state after its freshness window', () => {
    const now = 1_000_000;
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(now);
    cache.write('settings', { removalPolicy: 'FEFO' });
    nowSpy.mockReturnValue(now + 8 * 24 * 60 * 60 * 1000);

    expect(cache.read('settings')).toBeNull();
    expect(storage.length).toBe(0);
  });

  it('removes corrupt entries without breaking startup', () => {
    storage.setItem('larder.ui-cache.v1.scanners', '{not-json');

    expect(cache.read('scanners')).toBeNull();
    expect(storage.length).toBe(0);
  });
});
