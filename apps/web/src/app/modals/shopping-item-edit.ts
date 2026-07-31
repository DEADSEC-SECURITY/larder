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
import { GroceryItem } from '../core/models';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';
import { StepperComponent } from '../ui/stepper';

/**
 * Row editor reached by long-press on touch or right-click on desktop — the
 * things you actually change mid-shop: what it costs, how many, where, or
 * whether you want it at all.
 */
@Component({
  selector: 'app-shopping-item-edit',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent, StepperComponent],
  template: `
    <ui-modal [title]="item().name" [width]="440" (closed)="closed.emit()">
      <div>
        <div class="field-label">Price each</div>
        <input
          class="input input-on-bg"
          inputmode="decimal"
          [placeholder]="pricePlaceholder()"
          [ngModel]="price()"
          (ngModelChange)="price.set($event)"
        />
        <div class="field-hint">{{ priceHint() }}</div>
      </div>

      <div>
        <div class="field-label">Quantity</div>
        <ui-stepper [value]="quantity()" [min]="1" (valueChange)="quantity.set($event)" />
      </div>

      <div>
        <div class="field-label">Buy at</div>
        <select
          class="select select-block"
          [ngModel]="storeId()"
          (ngModelChange)="storeId.set($event)"
        >
          <option value="auto">Auto</option>
          @for (store of state.stores(); track store.id) {
            <option [value]="store.id">{{ store.name }}</option>
          }
        </select>
      </div>

      <div class="actions">
        <button type="button" class="btn btn-outline danger" (click)="remove()">
          <svg
            viewBox="0 0 24 24"
            width="15"
            height="15"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path
              d="M4 7h16M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7M6.4 7l.8 12.1a2 2 0 0 0 2 1.9h5.6a2 2 0 0 0 2-1.9L17.6 7M10 11v6M14 11v6"
            ></path>
          </svg>
          {{ confirmDelete() ? 'Tap again to remove' : 'Remove' }}
        </button>
        <span class="spacer"></span>
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" (click)="save()">Save</button>
      </div>
    </ui-modal>
  `,
  styles: `
    .actions {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 4px;
    }

    .danger {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--danger);
      border-color: var(--danger);
    }
  `,
})
export class ShoppingItemEditModal {
  readonly item = input.required<GroceryItem>();
  readonly currency = input<string | undefined>(undefined);
  readonly closed = output<void>();
  readonly saved = output<void>();

  readonly state = inject(AppStateService);
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly price = signal('');
  readonly quantity = signal(1);
  readonly storeId = signal('auto');
  readonly confirmDelete = signal(false);

  readonly pricePlaceholder = computed(() => {
    const current = this.item().unitPrice;
    return current == null ? '0.00' : current.toFixed(2);
  });
  readonly priceHint = computed(() => {
    const parsed = this.parsedPrice();
    if (parsed == null) {
      return this.item().priceSource === 'observed'
        ? 'Blank keeps the lowest price on record.'
        : 'Blank leaves this item without a price.';
    }
    return `${money(parsed * this.quantity(), this.currency())} for ${this.quantity()}`;
  });

  constructor() {
    // input() is not readable in a field initializer, so seed on first render.
    queueMicrotask(() => {
      const item = this.item();
      this.price.set(
        item.priceSource === 'entered' && item.unitPrice != null ? String(item.unitPrice) : '',
      );
      this.quantity.set(item.quantity);
      this.storeId.set(
        item.storeAssignment.source === 'manual' && item.storeAssignment.storeId
          ? item.storeAssignment.storeId
          : 'auto',
      );
    });
  }

  private parsedPrice(): number | null {
    const value = parseFloat(this.price().replace(/[^0-9.]/g, ''));
    return isNaN(value) ? null : value;
  }

  save(): void {
    const item = this.item();
    this.api
      .updateShoppingItem(item.barcode, {
        quantity: this.quantity(),
        unitPrice: this.parsedPrice(),
        preferredStoreId: this.storeId() === 'auto' ? null : this.storeId(),
      })
      .subscribe({
        next: () => {
          this.saved.emit();
          this.closed.emit();
        },
        error: () => this.toasts.show('Could not save that change'),
      });
  }

  remove(): void {
    if (!this.confirmDelete()) {
      this.confirmDelete.set(true);
      setTimeout(() => this.confirmDelete.set(false), this.state.confirmationTimeoutMs());
      return;
    }
    this.api.deleteShoppingItem(this.item().barcode).subscribe({
      next: () => {
        this.toasts.show(`Removed ${this.item().name}`);
        this.saved.emit();
        this.closed.emit();
      },
      error: () => this.toasts.show('Could not remove that item'),
    });
  }
}
