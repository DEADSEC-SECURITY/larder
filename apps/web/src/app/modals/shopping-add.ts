import { ChangeDetectionStrategy, Component, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../core/api.service';
import { GENERIC_SCOPE_LABEL, PRODUCT_KINDS } from '../core/format';
import { ProductKindUpper, StockGroupSummary } from '../core/models';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';
import { StepperComponent } from '../ui/stepper';

@Component({
  selector: 'app-shopping-add',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent, StepperComponent],
  template: `
    <ui-modal title="Add to shopping list" [width]="420" (closed)="closed.emit()">
      @if (groups().length) {
        <div>
          <div class="field-label">What to add</div>
          <select
            class="select select-block"
            [ngModel]="groupId()"
            (ngModelChange)="groupId.set($event)"
          >
            <option [ngValue]="null">A specific item…</option>
            @for (group of groups(); track group.id) {
              <option [ngValue]="group.id">{{ group.name }} — {{ scopeLabel }}</option>
            }
          </select>
        </div>
      }
      @if (!groupId()) {
        <input
          class="input input-on-bg"
          placeholder="Item name…"
          [ngModel]="name()"
          (ngModelChange)="name.set($event)"
        />
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
      } @else {
        <div class="field-hint">
          Any accepted product in the group satisfies this — the store and price come from
          the cheapest compatible option.
        </div>
      }
      <div style="width: 160px">
        <ui-stepper [value]="quantity()" [min]="1" (valueChange)="quantity.set($event)" />
      </div>
      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button
          type="button"
          class="btn btn-primary"
          [disabled]="!groupId() && !name().trim()"
          (click)="save()"
        >
          Add item
        </button>
      </div>
    </ui-modal>
  `,
  styles: `
    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }
  `,
})
export class ShoppingAddModal {
  readonly closed = output<void>();
  readonly saved = output<void>();

  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly scopeLabel = GENERIC_SCOPE_LABEL.toLowerCase();
  readonly kinds = PRODUCT_KINDS;
  readonly name = signal('');
  readonly kind = signal<ProductKindUpper>('FOOD');
  readonly quantity = signal(1);
  readonly groups = signal<StockGroupSummary[]>([]);
  readonly groupId = signal<string | null>(null);

  ngOnInit(): void {
    this.api
      .stockGroups()
      .subscribe(({ stockGroups }) => this.groups.set(stockGroups.filter((g) => g.active)));
  }

  save(): void {
    const groupId = this.groupId();
    if (!groupId && !this.name().trim()) {
      return;
    }
    const payload = groupId
      ? { stockGroupId: groupId, quantity: this.quantity() }
      : {
          name: this.name().trim(),
          quantity: this.quantity(),
          productKind: this.kind(),
        };
    this.api.addShoppingItem(payload).subscribe(() => {
      this.toasts.show('Added to shopping list');
      this.saved.emit();
      this.closed.emit();
    });
  }
}
