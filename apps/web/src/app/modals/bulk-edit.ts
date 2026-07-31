import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../core/api.service';
import { AppStateService } from '../core/app-state.service';
import { money } from '../core/format';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';
import { TaxRateSelectComponent, findTaxRate, taxBreakdownHint } from '../ui/tax-rate-select';

type ExpirationChoice = 'keep' | 'date' | 'estimate';

/**
 * Applies store, expiration, and/or price changes to many units at once.
 * Selection is either explicit unit ids (product detail) or whole products
 * (inventory), in which case every in-stock unit of those products is edited.
 */
@Component({
  selector: 'app-bulk-edit',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent, TaxRateSelectComponent],
  template: `
    <ui-modal title="Bulk edit" [width]="460" (closed)="closed.emit()">
      <div style="font-size: 13px; color: var(--sub)">
        Applies to <b style="color: var(--ink)">{{ scopeLabel() }}</b
        >. Fields on “Keep” are not touched.
      </div>

      <div class="grid-rows">
        <span class="grid-label">Store</span>
        <select
          class="select select-block"
          [ngModel]="storeChoice()"
          (ngModelChange)="storeChoice.set($event)"
        >
          <option value="keep">Keep</option>
          <option value="none">No store</option>
          @for (store of state.stores(); track store.id) {
            <option [value]="store.id">{{ store.name }}</option>
          }
        </select>

        <span class="grid-label">Stored in</span>
        <select
          class="select select-block"
          [ngModel]="storageChoice()"
          (ngModelChange)="storageChoice.set($event)"
        >
          <option value="keep">Keep</option>
          <option value="none">No location</option>
          @for (location of state.activeStorageLocations(); track location.id) {
            <option [value]="location.id">{{ location.name }}</option>
          }
        </select>

        <span class="grid-label">Expiration</span>
        <div class="grid-control">
          <div class="hstack wrap" style="gap: 6px">
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="expirationChoice() === 'keep'"
              (click)="expirationChoice.set('keep')"
            >
              Keep
            </button>
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="expirationChoice() === 'date'"
              (click)="expirationChoice.set('date')"
            >
              Date
            </button>
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="expirationChoice() === 'estimate'"
              (click)="expirationChoice.set('estimate')"
            >
              Estimate
            </button>
          </div>
          @if (expirationChoice() === 'date') {
            <input
              type="date"
              class="input input-on-bg"
              [ngModel]="date()"
              (ngModelChange)="date.set($event)"
            />
            <div class="field-hint">Applied as a <b>confirmed</b> expiration.</div>
          } @else if (expirationChoice() === 'estimate') {
            <div class="field-hint">
              Dates revert to the category rule estimate (or “No date” without a rule).
            </div>
          }
        </div>

        <span class="grid-label">Price</span>
        <div class="grid-control">
          <div class="hstack wrap" style="gap: 6px">
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="!setPrice()"
              (click)="setPrice.set(false)"
            >
              Keep
            </button>
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="setPrice() && priceMode() === 'unit'"
              (click)="setPrice.set(true); priceMode.set('unit')"
            >
              Per unit
            </button>
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="setPrice() && priceMode() === 'total'"
              (click)="setPrice.set(true); priceMode.set('total')"
            >
              Total
            </button>
          </div>
          @if (setPrice()) {
            <div class="hstack wrap" style="gap: 6px">
              <button
                type="button"
                class="chip chip-sm"
                [class.active]="!taxIncluded()"
                (click)="taxIncluded.set(false)"
              >
                Tax added on top
              </button>
              <button
                type="button"
                class="chip chip-sm"
                [class.active]="taxIncluded()"
                (click)="taxIncluded.set(true)"
              >
                Tax included
              </button>
            </div>
            <input
              class="input input-on-bg"
              [placeholder]="
                (priceMode() === 'unit' ? '$0.00 per unit' : '$0.00 total') +
                (taxIncluded() ? ' (incl. tax)' : ' (before tax)')
              "
              [ngModel]="price()"
              (ngModelChange)="price.set($event)"
            />
            <ui-tax-rate-select [value]="taxRateId()" (valueChange)="taxRateId.set($event)" />
            <div class="field-hint">
              @if (taxHint()) {
                <b>{{ taxHint() }}</b> ·
              } @else if (priceConversion()) {
                <b>{{ priceConversion() }}</b> ·
              }
              Recorded once per store in price history. Blank clears prices.
            </div>
          }
        </div>
      </div>

      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" [disabled]="!canApply()" (click)="apply()">
          Apply to {{ scopeLabel() }}
        </button>
      </div>
    </ui-modal>
  `,
  styles: `
    .grid-rows {
      display: grid;
      grid-template-columns: 90px 1fr;
      gap: 14px 12px;
      align-items: start;
    }

    .grid-label {
      font-size: 13px;
      font-weight: 600;
      padding-top: 7px;
    }

    .grid-control {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }
  `,
})
export class BulkEditModal {
  /** Explicit unit ids (product detail selection)… */
  readonly itemIds = input<string[]>();
  /** …or whole products (inventory selection). */
  readonly barcodes = input<string[]>();
  readonly unitCount = input.required<number>();
  readonly closed = output<void>();
  readonly saved = output<void>();

  readonly state = inject(AppStateService);
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly storeChoice = signal<'keep' | 'none' | string>('keep');
  readonly storageChoice = signal<'keep' | 'none' | string>('keep');
  readonly expirationChoice = signal<ExpirationChoice>('keep');
  readonly date = signal('');
  readonly setPrice = signal(false);
  readonly price = signal('');
  readonly priceMode = signal<'unit' | 'total'>('unit');
  readonly taxRateId = signal<string | null>(this.state.settings()?.defaultTaxRateId ?? null);
  readonly taxIncluded = signal(false);

  /** Per-unit subtotal + tax = total when a rate is selected. */
  readonly taxHint = computed(() => {
    const perUnit = this.unitPrice();
    const rate = findTaxRate(this.state.taxLocations(), this.taxRateId());
    if (perUnit == null || !rate) {
      return '';
    }
    return `Per unit: ${taxBreakdownHint(perUnit, rate, this.taxIncluded())}`;
  });

  readonly scopeLabel = computed(() => {
    const count = this.unitCount();
    return `${count} unit${count === 1 ? '' : 's'}`;
  });

  readonly priceConversion = computed(() => {
    const value = parseFloat(this.price().replace('$', '').trim());
    const count = this.unitCount();
    if (isNaN(value) || value < 0 || count === 0) {
      return '';
    }
    if (this.priceMode() === 'unit') {
      return `= ${money(value * count)} total for ${count} unit${count === 1 ? '' : 's'}`;
    }
    return `= ${money(value / count)} per unit`;
  });

  readonly canApply = computed(() => {
    if (
      this.storeChoice() === 'keep' &&
      this.storageChoice() === 'keep' &&
      this.expirationChoice() === 'keep' &&
      !this.setPrice()
    ) {
      return false;
    }
    return this.expirationChoice() !== 'date' || !!this.date();
  });

  /** Per-unit price actually sent to the API, whichever entry mode was used. */
  private unitPrice(): number | null {
    const value = parseFloat(this.price().replace('$', '').trim());
    if (isNaN(value) || value < 0) {
      return null;
    }
    return this.priceMode() === 'total' && this.unitCount() > 0
      ? value / this.unitCount()
      : value;
  }

  apply(): void {
    const storeChoice = this.storeChoice();
    const storageChoice = this.storageChoice();
    const expirationChoice = this.expirationChoice();
    this.api
      .bulkUnits({
        itemIds: this.itemIds(),
        barcodes: this.barcodes(),
        action: 'update',
        setStore: storeChoice !== 'keep',
        storeId: storeChoice === 'keep' || storeChoice === 'none' ? null : storeChoice,
        setStorageLocation: storageChoice !== 'keep',
        storageLocationId:
          storageChoice === 'keep' || storageChoice === 'none' ? null : storageChoice,
        expiration: expirationChoice === 'keep' ? undefined : expirationChoice,
        expirationDate: expirationChoice === 'date' ? this.date() : undefined,
        setPrice: this.setPrice(),
        price: this.setPrice() ? this.unitPrice() : null,
        taxRateId: this.setPrice() ? this.taxRateId() : undefined,
        priceIncludesTax: this.setPrice() ? this.taxIncluded() : undefined,
      })
      .subscribe(({ updated }) => {
        this.toasts.show(`Updated ${updated} unit${updated === 1 ? '' : 's'}`);
        this.state.changed();
        this.saved.emit();
        this.closed.emit();
      });
  }
}
