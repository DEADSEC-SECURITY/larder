import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { PRODUCT_KINDS, formatBarcodeDigits, productKindLabel } from '../../core/format';
import { ProductKind, ProductRow } from '../../core/models';
import { EmptyComponent } from '../../ui/empty';
import { UI_LIST } from '../../ui/list';

@Component({
  selector: 'app-products',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EmptyComponent, FormsModule, UI_LIST],
  template: `
    <div class="stack">
      <div class="hstack wrap" style="align-items: baseline; gap: 12px">
        <span class="page-title">Products</span>
        <span class="sub" style="font-size: 12.5px">{{ summary() }}</span>
      </div>
      <div class="hstack wrap" style="gap: 8px">
        <input
          class="input"
          style="flex: 1; min-width: 200px"
          placeholder="Search by name, brand, or barcode…"
          [ngModel]="search()"
          (ngModelChange)="search.set($event)"
        />
        <select
          class="select"
          [ngModel]="kind()"
          (ngModelChange)="kind.set($event)"
          title="Filter by product kind"
        >
          <option value="all">All kinds</option>
          @for (k of kinds; track k.kind) {
            <option [value]="k.kind">{{ k.label }}</option>
          }
        </select>
      </div>
      <ui-list>
        @for (product of filtered(); track product.barcode) {
          <ui-row (click)="open(product)">
            <ui-row-main>
              <div class="row-title">
                {{ product.name || 'Unidentified item' }}
                <span class="count-chip tnum">×{{ product.inStock }}</span>
                @if (idPill(product); as pill) {
                  <span class="pill pill-sm" [class]="'pill pill-sm ' + pill.cls">{{
                    pill.label
                  }}</span>
                }
              </div>
              <div class="row-meta">{{ meta(product) }}</div>
            </ui-row-main>
            <ui-rail [width]="128">
              <span class="p-bar tnum faint">{{ barLabel(product) }}</span>
            </ui-rail>
          </ui-row>
        } @empty {
          <ui-empty
            [message]="
              search().trim()
                ? 'Nothing matches — try a different search.'
                : 'No products yet — scan something or use Quick add.'
            "
          />
        }
      </ui-list>
    </div>
  `,
  styles: `
    .p-bar {
      font-size: 11.5px;
      letter-spacing: 0.1em;
    }
  `,
})
export class ProductsScreen {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  readonly state = inject(AppStateService);

  readonly products = signal<ProductRow[]>([]);
  readonly search = signal('');
  readonly kind = signal<'all' | ProductKind>('all');
  readonly kinds = PRODUCT_KINDS;

  readonly filtered = computed(() => {
    const query = this.search().toLowerCase().trim();
    const kind = this.kind();
    return this.products()
      .filter((p) => kind === 'all' || p.productKind === kind)
      .filter(
        (p) =>
          !query || `${p.name ?? ''} ${p.brand ?? ''} ${p.barcode}`.toLowerCase().includes(query),
      );
  });

  readonly summary = computed(() => {
    const list = this.products();
    const identified = list.filter((p) => p.idStatus === 'ok').length;
    return `${list.length} products · ${identified} identified`;
  });

  constructor() {
    effect(() => {
      this.state.version();
      this.api.products().subscribe(({ products }) => this.products.set(products));
    });
  }

  open(product: ProductRow): void {
    this.router.navigateByUrl(`/inventory/${encodeURIComponent(product.barcode)}`);
  }

  meta(product: ProductRow): string {
    const category = product.category
      ? product.category.parentName
        ? `${product.category.parentName} / ${product.category.name}`
        : product.category.name
      : 'uncategorized';
    const kind = product.productKind === 'food' ? null : productKindLabel(product.productKind);
    return [product.brand, category, kind].filter(Boolean).join(' · ');
  }

  barLabel(product: ProductRow): string {
    return /^\d+$/.test(product.barcode)
      ? formatBarcodeDigits(product.barcode)
      : 'manual';
  }

  // Identified is the default state — only exceptions get a pill.
  idPill(product: ProductRow): { label: string; cls: string } | null {
    switch (product.idStatus) {
      case 'pending':
        return { label: 'Identifying…', cls: 'pill-warn' };
      case 'failed':
        return { label: 'Lookup failed', cls: 'pill-danger' };
      default:
        return null;
    }
  }
}
