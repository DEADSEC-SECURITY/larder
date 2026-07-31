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
import { ToastService } from '../core/toast.service';
import { UnitRow } from '../core/models';
import { BarcodeComponent } from '../ui/barcode';
import { ModalComponent } from '../ui/modal';
import { TaxRateSelectComponent, findTaxRate, taxBreakdownHint } from '../ui/tax-rate-select';
import { money } from '../core/format';

@Component({
  selector: 'app-edit-unit',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent, BarcodeComponent, TaxRateSelectComponent],
  template: `
    <ui-modal title="Edit unit" [width]="440" (closed)="closed.emit()">
      <div class="preview">
        <div style="font-size: 14px; font-weight: 700; margin-bottom: 10px">
          {{ productName() }}
        </div>
        <ui-barcode [value]="barcode()" />
        <div class="field-hint" style="margin-top: 4px">
          Match this barcode against the item in hand
        </div>
      </div>
      <div>
        <div class="field-label">Store</div>
        <select class="select select-block" [ngModel]="storeId()" (ngModelChange)="storeId.set($event)">
          <option [ngValue]="null">— no store —</option>
          @for (store of state.stores(); track store.id) {
            <option [ngValue]="store.id">{{ store.name }}</option>
          }
        </select>
      </div>
      <div>
        <div class="field-label">Stored in</div>
        <select
          class="select select-block"
          [ngModel]="storageLocationId()"
          (ngModelChange)="storageLocationId.set($event)"
        >
          <option [ngValue]="null">No location</option>
          @for (location of state.activeStorageLocations(); track location.id) {
            <option [ngValue]="location.id">{{ location.name }}</option>
          }
          @if (archivedLocationName(); as name) {
            <option [ngValue]="unit().storageLocationId">{{ name }} (archived)</option>
          }
        </select>
      </div>
      <div>
        <div class="field-label">Expiration date</div>
        <div class="hstack" style="gap: 8px">
          <input
            type="date"
            class="input input-on-bg"
            style="flex: 1"
            [ngModel]="expiration()"
            (ngModelChange)="expiration.set($event)"
          />
          <button type="button" class="btn btn-outline" (click)="expiration.set('')">
            Clear
          </button>
        </div>
        <div class="field-hint" style="margin-top: 5px">
          Setting a date makes it <b>confirmed</b>; clearing reverts to the category estimate.
        </div>
      </div>
      <div>
        <div class="hstack" style="gap: 6px; margin-bottom: 5px">
          <span class="field-label" style="margin-bottom: 0">Price</span>
          <button
            type="button"
            class="chip chip-sm mode-chip"
            [class.active]="!taxIncluded()"
            (click)="taxIncluded.set(false)"
          >
            + tax
          </button>
          <button
            type="button"
            class="chip chip-sm mode-chip"
            [class.active]="taxIncluded()"
            (click)="taxIncluded.set(true)"
          >
            Tax incl.
          </button>
        </div>
        <input
          class="input input-on-bg"
          [placeholder]="taxIncluded() ? '$0.00 incl. tax' : '$0.00 before tax'"
          [ngModel]="price()"
          (ngModelChange)="price.set($event)"
        />
        <div class="field-hint" style="margin-top: 5px">
          Per unit. Leave blank for “no price”. With a store set, it’s also recorded in
          that store’s price history.
        </div>
      </div>
      <div>
        <div class="field-label">Tax rate</div>
        <ui-tax-rate-select [value]="taxRateId()" (valueChange)="taxRateId.set($event)" />
        @if (taxHint()) {
          <div class="field-hint" style="margin-top: 5px">{{ taxHint() }}</div>
        }
      </div>
      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" (click)="save()">Save</button>
      </div>
    </ui-modal>
  `,
  styles: `
    .preview {
      text-align: center;
      background: var(--bg);
      border-radius: 14px;
      padding: 16px;
    }

    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }

    .mode-chip {
      padding: 3px 9px;
      font-size: 10.5px;
    }
  `,
})
export class EditUnitModal {
  readonly unit = input.required<UnitRow>();
  readonly productName = input.required<string>();
  readonly barcode = input.required<string>();
  readonly closed = output<void>();
  readonly saved = output<void>();

  readonly state = inject(AppStateService);
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly storeId = signal<string | null>(null);
  readonly storageLocationId = signal<string | null>(null);
  readonly expiration = signal('');
  readonly price = signal('');
  readonly taxRateId = signal<string | null>(null);
  readonly taxIncluded = signal(false);

  /** Recorded breakdown for the unit, or a live preview once inputs change. */
  readonly taxHint = computed(() => {
    const subtotal = parseFloat(this.price().replace('$', '').trim());
    const rate = findTaxRate(this.state.taxLocations(), this.taxRateId());
    if (!isNaN(subtotal) && rate) {
      return taxBreakdownHint(subtotal, rate, this.taxIncluded());
    }
    const unit = this.unit();
    if (unit.priceSubtotal != null && unit.taxAmount != null && unit.price != null) {
      return `Subtotal ${money(unit.priceSubtotal)} + tax ${money(unit.taxAmount)} = ${money(unit.price)} total`;
    }
    return '';
  });

  /** The unit's current location when it's archived (hidden from the active list). */
  archivedLocationName(): string | null {
    const unit = this.unit();
    if (!unit.storageLocationId) {
      return null;
    }
    const active = this.state
      .activeStorageLocations()
      .some((l) => l.id === unit.storageLocationId);
    return active ? null : unit.storageLocationName;
  }

  ngOnInit(): void {
    const unit = this.unit();
    this.storeId.set(unit.storeId);
    this.storageLocationId.set(unit.storageLocationId);
    this.expiration.set(unit.expirationDate ?? '');
    // The input edits the pre-tax subtotal; older units only recorded a total.
    const subtotal = unit.priceSubtotal ?? unit.price;
    this.price.set(subtotal != null ? subtotal.toFixed(2) : '');
    // Never-recorded tax (all-null fields) defaults to the household rate.
    this.taxRateId.set(
      unit.taxRateId ??
        (unit.taxRate == null ? (this.state.settings()?.defaultTaxRateId ?? null) : null),
    );
  }

  save(): void {
    const parsed = parseFloat(this.price().replace('$', '').trim());
    this.api
      .updateUnit(this.unit().id, {
        storeId: this.storeId(),
        storageLocationId: this.storageLocationId(),
        expirationDate: this.expiration() || null,
        price: isNaN(parsed) ? null : parsed,
        taxRateId: this.taxRateId(),
        priceIncludesTax: this.taxIncluded(),
      })
      .subscribe(() => {
        this.toasts.show('Unit updated');
        this.saved.emit();
        this.closed.emit();
      });
  }
}
