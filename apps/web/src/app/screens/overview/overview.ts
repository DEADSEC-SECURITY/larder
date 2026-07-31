import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { ClockService } from '../../core/clock.service';
import { money, relativeTime } from '../../core/format';
import { ActivityEvent } from '../../core/models';
import { UiCacheService } from '../../core/ui-cache.service';
import { ExpiryPillComponent } from '../../ui/expiry-pill';
import { MetricCardComponent } from '../../ui/metric-card';
import { UI_LIST } from '../../ui/list';

const KIND_LABELS: Record<string, [string, string]> = {
  added: ['Added', 'var(--accent)'],
  removed: ['Removed', 'var(--warn)'],
  wasted: ['Wasted', 'var(--danger)'],
  changed: ['Changed', 'var(--sub)'],
  estimated: ['Estimated', 'var(--sub)'],
  ignored: ['Ignored', 'var(--faint)'],
  failed: ['No match', 'var(--danger)'],
};

@Component({
  selector: 'app-overview',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MetricCardComponent, ExpiryPillComponent, RouterLink, UI_LIST],
  templateUrl: './overview.html',
  styleUrl: './overview.scss',
})
export class OverviewScreen {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly clock = inject(ClockService);
  private readonly cache = inject(UiCacheService);
  readonly state = inject(AppStateService);

  readonly data = computed(() => this.state.overview());
  readonly recent = signal(this.cache.read<ActivityEvent[]>('activity') ?? []);
  readonly kindLabels = KIND_LABELS;
  private activityLoading = false;

  readonly attentionRows = computed(() => {
    const data = this.data();
    if (!data) {
      return [];
    }
    const rows: { label: string; count: string; tone: string; link: string }[] = [];
    if (data.attention.pendingIdentification) {
      rows.push({
        label: 'Awaiting identification',
        count: String(data.attention.pendingIdentification),
        tone: 'var(--warn)',
        link: '/products',
      });
    }
    if (data.attention.failedLookups) {
      rows.push({
        label: 'Lookup failed — will retry',
        count: String(data.attention.failedLookups),
        tone: 'var(--danger)',
        link: '/products',
      });
    }
    if (data.attention.missingPrices) {
      rows.push({
        label: 'Products missing prices',
        count: String(data.attention.missingPrices),
        tone: 'var(--warn)',
        link: '/inventory?filter=needs',
      });
    }
    if (data.attention.missingRules) {
      rows.push({
        label: 'No expiration rule',
        count: String(data.attention.missingRules),
        tone: 'var(--sub)',
        link: '/settings/categories',
      });
    }
    for (const name of data.attention.offlineScanners) {
      rows.push({ label: `Scanner “${name}” offline`, count: '', tone: 'var(--danger)', link: '' });
    }
    return rows;
  });

  constructor() {
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    if (this.activityLoading) {
      return;
    }
    this.activityLoading = true;
    this.api
      .activity(4)
      .pipe(finalize(() => (this.activityLoading = false)))
      .subscribe({
        next: ({ events }) => {
          this.recent.set(events);
          this.cache.write('activity', events);
        },
        error: () => undefined,
      });
  }

  go(link: string): void {
    if (!link) {
      this.state.drawerOpen.set(true);
      return;
    }
    this.router.navigateByUrl(link);
  }

  money(value: number | null): string {
    return money(value, this.data()?.currency);
  }

  relative(iso: string): string {
    return relativeTime(iso, this.clock.now());
  }

  expMeta(item: { brand: string | null; storeName: string | null; inStock: number }): string {
    return [item.brand, item.storeName, `${item.inStock} in stock`].filter(Boolean).join(' · ');
  }

  groceryPreviewLabel(item: { name: string; quantity: number; unitPrice: number | null }): string {
    let label = item.name;
    if (item.quantity > 1) {
      label += ` ×${item.quantity}`;
    }
    if (item.unitPrice != null) {
      label += ` · ${this.money(item.unitPrice * item.quantity)}`;
    }
    return label;
  }
}
