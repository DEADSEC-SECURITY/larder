import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../core/api.service';
import { AppStateService } from '../core/app-state.service';
import { PRODUCT_KINDS, formatBarcodeDigits, money } from '../core/format';
import { BarcodeResolution, ProductKindUpper, ProductRow } from '../core/models';
import { ToastService } from '../core/toast.service';
import { BarcodeScannerComponent } from '../ui/barcode-scanner';
import { ModalComponent } from '../ui/modal';
import { StepperComponent } from '../ui/stepper';
import {
  TaxRateSelectComponent,
  findTaxRate,
  taxBreakdownHint,
} from '../ui/tax-rate-select';

@Component({
  selector: 'app-quick-add',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    FormsModule,
    BarcodeScannerComponent,
    ModalComponent,
    StepperComponent,
    TaxRateSelectComponent,
  ],
  template: `
    <ui-modal title="Add items" [width]="540" (closed)="closed.emit()">
      @if (!pickedName()) {
        <div class="stack" style="gap: 8px">
          <div class="hstack" style="gap: 8px">
            <input
              class="input input-on-bg"
              style="flex: 1"
              placeholder="Search products or type a new name…"
              [ngModel]="query()"
              (ngModelChange)="query.set($event)"
            />
            <button
              type="button"
              class="btn btn-outline scan-btn"
              title="Scan a barcode"
              (click)="scannerOpen.set(true)"
            >
              <svg
                viewBox="0 0 24 24"
                width="18"
                height="18"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path
                  d="M3 8V5.5A1.5 1.5 0 0 1 4.5 4H7M17 4h2.5A1.5 1.5 0 0 1 21 5.5V8M21 16v2.5a1.5 1.5 0 0 1-1.5 1.5H17M7 20H4.5A1.5 1.5 0 0 1 3 18.5V16M7 8v8M10.5 8v8M14 8v8M17 8v8"
                ></path>
              </svg>
              Scan
            </button>
          </div>
          <div>
            @for (product of results(); track product.barcode) {
              <div class="result row-hover row-line" (click)="pick(product)">
                <span class="result-name">{{ product.name }}</span>
                <span class="result-note">{{ product.inStock }} in stock</span>
              </div>
            }
            @if (query().trim()) {
              <div class="create row-hover" (click)="createNew()">
                ＋ Create “{{ query().trim() }}”
              </div>
            }
          </div>
        </div>
      } @else {
        <div class="picked">
          <div style="flex: 1; min-width: 0">
            <div class="picked-name">{{ pickedName() }}</div>
            @if (pickedNote()) {
              <div class="picked-note">{{ pickedNote() }}</div>
            }
          </div>
          <button type="button" class="picked-change" (click)="clearPick()">change</button>
        </div>
        @if (!targetBarcode()) {
          <div>
            <div class="field-label">Kind</div>
            <div class="hstack wrap" style="gap: 6px">
              @for (option of kinds; track option.upper) {
                <button
                  type="button"
                  class="chip chip-sm"
                  [class.active]="kind() === option.upper"
                  (click)="kind.set(option.upper)"
                >
                  {{ option.label }}
                </button>
              }
            </div>
          </div>
        }
        <div class="two-col">
          <div>
            <div class="field-label">Quantity</div>
            <ui-stepper [value]="quantity()" [min]="1" (valueChange)="quantity.set($event)" />
          </div>
          <div>
            <div class="field-label">Added on</div>
            <input
              type="date"
              class="input input-on-bg"
              [ngModel]="addedOn()"
              (ngModelChange)="addedOn.set($event)"
            />
          </div>
        </div>
        <div>
          <div class="field-label">Store</div>
          <div class="hstack wrap" style="gap: 7px; margin-bottom: 7px">
            @for (store of state.stores(); track store.id) {
              <button
                type="button"
                class="chip chip-sm"
                [class.active]="storeId() === store.id"
                (click)="pickStore(store.id)"
              >
                {{ store.name }}
              </button>
            }
          </div>
          <input
            class="input input-on-bg"
            placeholder="…or a new store name"
            [ngModel]="newStore()"
            (ngModelChange)="onNewStore($event)"
          />
        </div>
        <div>
          <div class="field-label">Stored in</div>
          <select
            class="select select-block"
            [ngModel]="storageLocationId()"
            (ngModelChange)="storageLocationId.set($event)"
          >
            <option [ngValue]="null">Default location</option>
            @for (location of state.activeStorageLocations(); track location.id) {
              <option [ngValue]="location.id">{{ location.name }}</option>
            }
          </select>
          <div class="field-hint">
            Default = the product’s or category’s usual spot, if one is set.
          </div>
        </div>
        <div class="two-col">
          <div>
            <div class="hstack" style="gap: 7px; margin-bottom: 5px">
              <span class="field-label" style="margin-bottom: 0">Price paid</span>
              <button
                type="button"
                class="chip chip-sm price-mode"
                [class.active]="priceMode() === 'total'"
                (click)="priceMode.set('total')"
              >
                Total
              </button>
              <button
                type="button"
                class="chip chip-sm price-mode"
                [class.active]="priceMode() === 'unit'"
                (click)="priceMode.set('unit')"
              >
                Per unit
              </button>
            </div>
            <input
              class="input input-on-bg"
              [placeholder]="priceMode() === 'total' ? '$0.00 total' : '$0.00 per unit'"
              [ngModel]="price()"
              (ngModelChange)="price.set($event)"
            />
            <div class="field-hint">{{ splitHint() }}</div>
          </div>
          <div>
            <div class="field-label">Expiration</div>
            <input
              type="date"
              class="input input-on-bg"
              [ngModel]="expiration()"
              (ngModelChange)="expiration.set($event)"
            />
            <div class="field-hint">Blank = use category estimate</div>
          </div>
        </div>
        <div>
          <div class="hstack" style="gap: 7px; margin-bottom: 5px">
            <span class="field-label" style="margin-bottom: 0">Tax rate</span>
            <button
              type="button"
              class="chip chip-sm price-mode"
              [class.active]="!taxIncluded()"
              (click)="taxIncluded.set(false)"
            >
              Added on top
            </button>
            <button
              type="button"
              class="chip chip-sm price-mode"
              [class.active]="taxIncluded()"
              (click)="taxIncluded.set(true)"
            >
              Included in price
            </button>
          </div>
          <ui-tax-rate-select [value]="taxRateId()" (valueChange)="onTaxRate($event)" />
          @if (taxHint()) {
            <div class="field-hint" style="margin-top: 5px">{{ taxHint() }}</div>
          }
        </div>
      }
      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button
          type="button"
          class="btn btn-primary"
          [disabled]="!canSave()"
          (click)="save()"
        >
          Add {{ quantity() }} {{ quantity() > 1 ? 'units' : 'unit' }}
        </button>
      </div>
    </ui-modal>

    @if (scannerOpen()) {
      <ui-barcode-scanner
        (detected)="onBarcode($event)"
        (closed)="scannerOpen.set(false)"
      />
    }
  `,
  styles: `
    .result {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 8px;
    }

    .result-name {
      flex: 1;
      font-size: 14px;
      font-weight: 600;
    }

    .result-note {
      font-size: 12px;
      color: var(--sub);
    }

    .create {
      padding: 11px 8px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      color: var(--accent);

      &:hover {
        background: var(--accent-bg);
      }
    }

    .picked {
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--accent-bg);
      border-radius: var(--r-input);
      padding: 12px 14px;
    }

    .picked-name {
      font-size: 14.5px;
      font-weight: 700;
      color: var(--accent);
      overflow-wrap: anywhere;
    }

    .picked-note {
      font-size: 12px;
      color: var(--sub);
      margin-top: 2px;
    }

    .scan-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }

    .picked-change {
      font-size: 12px;
      font-weight: 600;
      color: var(--accent);
      cursor: pointer;
      text-decoration: underline;
      background: none;
      border: none;
      font-family: inherit;
    }

    .two-col {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .price-mode {
      padding: 3px 9px;
      font-size: 10.5px;
    }

    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      margin-top: 4px;
    }
  `,
})
export class QuickAddModal {
  readonly closed = output<void>();

  readonly state = inject(AppStateService);
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly kinds = PRODUCT_KINDS;
  readonly query = signal('');
  readonly products = signal<ProductRow[]>([]);
  readonly pickedProduct = signal<ProductRow | null>(null);
  /** A scanned/typed barcode whose product isn't in the loaded list yet. */
  readonly resolved = signal<BarcodeResolution | null>(null);
  readonly scannerOpen = signal(false);
  readonly newName = signal('');
  readonly kind = signal<ProductKindUpper>('FOOD');
  readonly storageLocationId = signal<string | null>(null);
  readonly quantity = signal(1);
  readonly addedOn = signal(new Date().toISOString().slice(0, 10));
  readonly storeId = signal<string | null>(null);
  readonly newStore = signal('');
  readonly price = signal('');
  readonly priceMode = signal<'total' | 'unit'>('total');
  readonly expiration = signal('');
  readonly taxRateId = signal<string | null>(null);
  readonly taxIncluded = signal(false);
  private taxTouched = false;

  /** Set whenever we're adding to an existing product rather than a new name. */
  readonly targetBarcode = computed(
    () => this.pickedProduct()?.barcode ?? this.resolved()?.barcode ?? null,
  );
  readonly pickedName = computed(() => {
    const scanned = this.resolved();
    return (
      this.pickedProduct()?.name ||
      scanned?.name ||
      // Enrichment may not have named it yet; show the digits so the row is
      // still identifiable.
      (scanned ? formatBarcodeDigits(scanned.barcode) : '') ||
      this.newName()
    );
  });
  readonly pickedNote = computed(() => {
    const scanned = this.resolved();
    if (!scanned) {
      return '';
    }
    if (scanned.idStatus === 'pending') {
      return scanned.created
        ? 'New barcode — looking up the product now'
        : 'Still identifying this product';
    }
    if (scanned.idStatus === 'failed') {
      return 'Not found in any catalogue — you can rename it later';
    }
    return `${formatBarcodeDigits(scanned.barcode)} · ${scanned.inStock} in stock`;
  });
  readonly results = computed(() => {
    const query = this.query().toLowerCase().trim();
    return this.products()
      .filter((p) => p.idStatus === 'ok' && p.name)
      .filter((p) => !query || `${p.name} ${p.brand}`.toLowerCase().includes(query))
      .slice(0, 5);
  });
  readonly splitHint = computed(() => {
    const value = parseFloat(this.price().replace('$', ''));
    const quantity = this.quantity();
    if (isNaN(value) || quantity <= 0) {
      return 'Split across units automatically';
    }
    if (this.priceMode() === 'total') {
      return `= ${money(value / quantity)} per unit`;
    }
    return `= ${money(value * quantity)} total for ${quantity} unit${quantity === 1 ? '' : 's'}`;
  });
  readonly canSave = computed(() => {
    if (!this.pickedName()) {
      return false;
    }
    if (this.state.settings()?.requireStoreOnManualAdd) {
      return !!this.storeId() || !!this.newStore().trim();
    }
    return true;
  });

  /** Per-unit subtotal + tax = total for the selected rate. */
  readonly taxHint = computed(() => {
    const rate = findTaxRate(this.state.taxLocations(), this.taxRateId());
    const value = parseFloat(this.price().replace('$', ''));
    if (!rate || isNaN(value)) {
      return '';
    }
    const perUnit = this.priceMode() === 'total' ? value / Math.max(1, this.quantity()) : value;
    return `Per unit: ${taxBreakdownHint(perUnit, rate, this.taxIncluded())}`;
  });

  constructor() {
    this.api.products().subscribe(({ products }) => this.products.set(products));
    this.state.refreshStores();
    this.state.refreshTaxLocations();
    this.state.refreshStorageLocations();
    this.taxRateId.set(this.state.settings()?.defaultTaxRateId ?? null);
  }

  onTaxRate(value: string | null): void {
    this.taxRateId.set(value);
    this.taxTouched = true;
  }

  pick(product: ProductRow): void {
    this.pickedProduct.set(product);
  }

  createNew(): void {
    this.newName.set(this.query().trim());
  }

  clearPick(): void {
    this.pickedProduct.set(null);
    this.resolved.set(null);
    this.newName.set('');
    this.query.set('');
  }

  onBarcode(code: string): void {
    this.scannerOpen.set(false);
    this.api.resolveBarcode(code).subscribe({
      next: (result) => {
        // Prefer the fully-populated row when the catalogue already has it.
        const existing = this.products().find((p) => p.barcode === result.barcode);
        if (existing) {
          this.pickedProduct.set(existing);
          this.resolved.set(null);
        } else {
          this.resolved.set(result);
          this.pickedProduct.set(null);
        }
        if (result.created) {
          this.toasts.show('New barcode added — identifying it now');
        }
      },
      error: (error: unknown) => {
        const detail = (error as { error?: { detail?: { code?: string } } })?.error?.detail;
        this.toasts.show(
          detail?.code === 'GTIN_BAD_CHECK_DIGIT'
            ? 'That barcode’s check digit is wrong — re-scan or retype it'
            : 'That barcode was not recognised',
        );
      },
    });
  }

  pickStore(id: string): void {
    this.storeId.set(this.storeId() === id ? null : id);
    this.newStore.set('');
  }

  onNewStore(value: string): void {
    this.newStore.set(value);
    if (value.trim()) {
      this.storeId.set(null);
    }
  }

  save(): void {
    if (!this.canSave()) {
      return;
    }
    const priceValue = parseFloat(this.price().replace('$', ''));
    const priceTotal =
      this.priceMode() === 'unit' ? priceValue * this.quantity() : priceValue;
    const barcode = this.targetBarcode();
    this.api
      .quickAdd({
        barcode: barcode ?? undefined,
        name: barcode ? undefined : this.newName(),
        // Kind only applies when creating a product; existing ones keep theirs.
        productKind: barcode ? undefined : this.kind(),
        storageLocationId: this.storageLocationId() ?? undefined,
        quantity: this.quantity(),
        storeId: this.storeId() ?? undefined,
        newStoreName: this.newStore().trim() || undefined,
        priceTotal: isNaN(priceTotal) ? undefined : priceTotal,
        // taxRateId requires priceTotal; untouched = let the backend default apply.
        taxRateId: this.taxTouched && !isNaN(priceTotal) ? this.taxRateId() : undefined,
        priceIncludesTax: !isNaN(priceTotal) ? this.taxIncluded() : undefined,
        addedOn: this.addedOn() || undefined,
        expirationDate: this.expiration() || undefined,
      })
      .subscribe({
        next: ({ created }) => {
          this.toasts.show(`Added ${created} × ${this.pickedName()}`);
          this.state.changed();
          this.state.refreshStores();
          this.closed.emit();
        },
        error: () => this.toasts.show('Could not add — check the form'),
      });
  }
}
