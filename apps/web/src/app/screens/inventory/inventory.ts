import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { PRODUCT_KINDS, expiryInfo, money, productKindLabel } from '../../core/format';
import { InventoryResponse, ProductKind, ProductRow } from '../../core/models';
import { BulkEditModal } from '../../modals/bulk-edit';
import { EmptyComponent } from '../../ui/empty';
import { ExpiryPillComponent } from '../../ui/expiry-pill';
import { UI_LIST } from '../../ui/list';

type Filter = 'all' | 'expiring' | 'low' | 'needs';
type Sort = 'expiry' | 'name' | 'recent' | 'stock';

@Component({
  selector: 'app-inventory',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, BulkEditModal, EmptyComponent, ExpiryPillComponent, UI_LIST],
  templateUrl: './inventory.html',
  styleUrl: './inventory.scss',
})
export class InventoryScreen {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  readonly state = inject(AppStateService);

  readonly data = signal<InventoryResponse | null>(null);
  readonly search = signal('');
  readonly filter = signal<Filter>('all');
  readonly kind = signal<'all' | ProductKind>('all');
  readonly sort = signal<Sort>('expiry');
  readonly selecting = signal(false);
  readonly selected = signal<ReadonlySet<string>>(new Set());
  readonly bulkOpen = signal(false);

  readonly filters: { key: Filter; label: string }[] = [
    { key: 'all', label: 'Everything' },
    { key: 'expiring', label: 'Expiring' },
    { key: 'low', label: 'Low stock' },
    { key: 'needs', label: 'Needs info' },
  ];
  readonly kinds = PRODUCT_KINDS;
  readonly sorts: { key: Sort; label: string }[] = [
    { key: 'expiry', label: 'Expiry' },
    { key: 'name', label: 'Name' },
    { key: 'recent', label: 'Recent' },
    { key: 'stock', label: 'Stock' },
  ];

  readonly counts = computed(() => {
    const products = this.data()?.products ?? [];
    const windowDays = this.data()?.windowDays ?? 4;
    return {
      all: products.length,
      expiring: products.filter((p) => this.isExpiring(p, windowDays)).length,
      low: products.filter((p) => p.lowStock).length,
      needs: products.filter((p) => p.needsInfo).length,
    };
  });

  readonly rows = computed(() => {
    const data = this.data();
    if (!data) {
      return [];
    }
    let rows = [...data.products];
    const kind = this.kind();
    if (kind !== 'all') {
      rows = rows.filter((p) => p.productKind === kind);
    }
    const query = this.search().toLowerCase().trim();
    if (query) {
      rows = rows.filter((p) =>
        `${p.name ?? ''} ${p.brand ?? ''}`.toLowerCase().includes(query),
      );
    }
    const filter = this.filter();
    if (filter === 'expiring') {
      rows = rows.filter((p) => this.isExpiring(p, data.windowDays));
    } else if (filter === 'low') {
      rows = rows.filter((p) => p.lowStock);
    } else if (filter === 'needs') {
      rows = rows.filter((p) => p.needsInfo);
    }
    const sort = this.sort();
    rows.sort((a, b) => {
      if (sort === 'name') {
        return (a.name ?? '').localeCompare(b.name ?? '');
      }
      if (sort === 'recent') {
        return (b.lastAddedAt ?? '').localeCompare(a.lastAddedAt ?? '');
      }
      if (sort === 'stock') {
        return b.inStock - a.inStock;
      }
      const aDate = a.soonestExpiration?.date ?? '9999';
      const bDate = b.soonestExpiration?.date ?? '9999';
      return aDate.localeCompare(bDate);
    });
    return rows;
  });

  /** Selection pruned to currently visible rows. */
  readonly selectedBarcodes = computed(() => {
    const visible = new Set(this.rows().map((p) => p.barcode));
    return [...this.selected()].filter((barcode) => visible.has(barcode));
  });

  readonly selectedUnitCount = computed(() => {
    const chosen = new Set(this.selectedBarcodes());
    return this.rows()
      .filter((p) => chosen.has(p.barcode))
      .reduce((sum, p) => sum + p.inStock, 0);
  });

  constructor() {
    const route = inject(ActivatedRoute);
    const initial = route.snapshot.queryParamMap.get('filter') as Filter | null;
    if (initial && ['all', 'expiring', 'low', 'needs'].includes(initial)) {
      this.filter.set(initial);
    }
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api.inventory().subscribe((data) => this.data.set(data));
  }

  private isExpiring(product: ProductRow, windowDays: number): boolean {
    const days = product.soonestExpiration?.daysLeft;
    return days != null && days <= windowDays;
  }

  open(product: ProductRow): void {
    if (this.selecting()) {
      this.toggleRow(product);
      return;
    }
    this.router.navigateByUrl(`/inventory/${encodeURIComponent(product.barcode)}`);
  }

  toggleSelecting(): void {
    this.selecting.update((on) => !on);
    this.selected.set(new Set());
  }

  toggleRow(product: ProductRow): void {
    const next = new Set(this.selected());
    if (next.has(product.barcode)) {
      next.delete(product.barcode);
    } else {
      next.add(product.barcode);
    }
    this.selected.set(next);
  }

  selectAllVisible(): void {
    const visible = this.rows().map((p) => p.barcode);
    const allSelected = visible.every((barcode) => this.selected().has(barcode));
    this.selected.set(allSelected ? new Set() : new Set(visible));
  }

  finishBulk(): void {
    this.selecting.set(false);
    this.selected.set(new Set());
  }

  meta(product: ProductRow): string {
    const category = product.category
      ? product.category.parentName
        ? `${product.category.parentName} / ${product.category.name}`
        : product.category.name
      : 'uncategorized';
    // Food is the overwhelming default, so only other kinds earn a mention.
    const kind = product.productKind === 'food' ? null : productKindLabel(product.productKind);
    return [product.brand, category, kind].filter(Boolean).join(' · ');
  }

  badges(product: ProductRow): { label: string; cls: string }[] {
    const out: { label: string; cls: string }[] = [];
    if (product.idStatus === 'pending') {
      out.push({ label: 'Identifying…', cls: 'pill-warn' });
    }
    if (product.idStatus === 'failed') {
      out.push({ label: 'Lookup failed', cls: 'pill-danger' });
    }
    if (product.lowestPrice == null) {
      out.push({ label: 'No price', cls: 'pill-chip' });
    }
    if (product.hasUnknownDate) {
      out.push({ label: 'No date', cls: 'pill-chip' });
    }
    if (product.lowStock) {
      out.push({ label: 'Low stock', cls: 'pill-warn' });
    }
    return out;
  }

  price(product: ProductRow): string {
    return product.lowestPrice != null ? money(product.lowestPrice) : '—';
  }

  dotColor(product: ProductRow): string {
    const info = expiryInfo(
      product.soonestExpiration?.date ?? null,
      product.soonestExpiration?.daysLeft ?? null,
      this.data()?.windowDays ?? 4,
    );
    const colors: Record<string, string> = {
      danger: 'var(--danger)',
      warn: 'var(--warn)',
      chip: 'var(--sub)',
      faint: 'var(--line)',
      none: 'var(--line)',
    };
    return colors[info.tone];
  }
}
