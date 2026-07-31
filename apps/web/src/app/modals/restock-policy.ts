import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';

import { ApiService } from '../core/api.service';
import { ToastService } from '../core/toast.service';
import { GENERIC_SCOPE_LABEL } from '../core/format';
import { PolicyRef, ProductStockGroup } from '../core/models';
import { ModalComponent } from '../ui/modal';
import { StepperComponent } from '../ui/stepper';

type PolicyScope = 'exact' | 'group';

@Component({
  selector: 'app-restock-policy',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ModalComponent, StepperComponent],
  template: `
    <ui-modal title="Restock policy" [width]="440" (closed)="closed.emit()">
      <div style="font-size: 13px; color: var(--sub); line-height: 1.5">
        When stock of <b style="color: var(--ink)">{{ productName() }}</b> falls below the
        minimum, it’s added to the shopping list with enough quantity to reach the restock-to
        level.
      </div>

      <div>
        <div class="field-label">Applies to</div>
        <div class="scope-grid">
          <button
            type="button"
            class="scope-option"
            [class.active]="scope() === 'exact'"
            (click)="scope.set('exact')"
          >
            <span class="scope-name">Exact product</span>
            <span class="scope-note">Only this barcode counts toward stock.</span>
          </button>
          <button
            type="button"
            class="scope-option"
            [class.active]="scope() === 'group'"
            (click)="scope.set('group')"
          >
            <span class="scope-name">{{ genericLabel }}</span>
            <span class="scope-note">{{ groupNote() }}</span>
          </button>
        </div>
        @if (scopeWarning(); as warning) {
          <div class="field-hint scope-warning">{{ warning }}</div>
        }
      </div>

      <div class="two-col">
        <div>
          <div class="field-label">Minimum</div>
          <ui-stepper [value]="minimum()" (valueChange)="onMinimum($event)" />
        </div>
        <div>
          <div class="field-label">Restock to</div>
          <ui-stepper [value]="target()" [min]="1" (valueChange)="target.set($event)" />
        </div>
      </div>
      <div class="field-hint">{{ preview() }}</div>
      <div class="actions">
        <button
          type="button"
          class="btn btn-danger-outline"
          style="margin-right: auto"
          [disabled]="busy()"
          (click)="clear()"
        >
          No policy
        </button>
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" [disabled]="busy()" (click)="save()">
          Save
        </button>
      </div>
    </ui-modal>
  `,
  styles: `
    .two-col {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .scope-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .scope-option {
      display: flex;
      flex-direction: column;
      gap: 3px;
      text-align: left;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1.5px solid var(--line);
      background: none;
      cursor: pointer;
      font: inherit;
      color: inherit;

      &.active {
        border-color: var(--accent);
        background: var(--chip);
      }
    }

    .scope-name {
      font-size: 13px;
      font-weight: 600;
    }

    .scope-note {
      font-size: 11.5px;
      color: var(--faint);
      line-height: 1.4;
    }

    .scope-warning {
      margin-top: 6px;
    }

    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }
  `,
})
export class RestockPolicyModal {
  readonly barcode = input.required<string>();
  readonly productName = input.required<string>();
  readonly policy = input<PolicyRef | null>(null);
  readonly stockGroup = input<ProductStockGroup | null>(null);
  readonly closed = output<void>();
  readonly saved = output<void>();

  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly genericLabel = GENERIC_SCOPE_LABEL;
  readonly minimum = signal(0);
  readonly target = signal(1);
  readonly scope = signal<PolicyScope>('exact');
  readonly busy = signal(false);

  private grouped(): ProductStockGroup | null {
    const group = this.stockGroup();
    return group?.active ? group : null;
  }

  readonly groupNote = computed(() => {
    const group = this.grouped();
    if (group) {
      const products = `${group.memberCount} product${group.memberCount === 1 ? '' : 's'}`;
      return `${group.name} · ${products} · ${group.inStock} in stock combined`;
    }
    return 'Compatible products found later count too — matching is automatic.';
  });

  readonly scopeWarning = computed(() => {
    const group = this.grouped();
    if (group && this.scope() === 'exact') {
      return `Saving removes ${this.productName()} from the ${group.name} group; future matches won’t re-add it automatically.`;
    }
    return '';
  });

  readonly preview = computed(() => {
    if (this.minimum() === 0) {
      return 'Minimum 0 = no auto-restock; save to disable the policy.';
    }
    const what = this.scope() === 'group' ? 'combined stock' : 'stock';
    return `Below ${this.minimum()} ${what} → shopping list tops it back up to ${this.target()}.`;
  });

  ngOnInit(): void {
    const policy = this.policy();
    if (policy) {
      this.minimum.set(policy.reorderPoint);
      this.target.set(Math.max(policy.targetStock, policy.reorderPoint, 1));
    }
    if (this.grouped()) {
      this.scope.set('group');
    }
  }

  onMinimum(value: number): void {
    this.minimum.set(value);
    if (this.target() < value) {
      this.target.set(value);
    }
  }

  clear(): void {
    const group = this.grouped();
    this.busy.set(true);
    const done = {
      next: () => {
        this.toasts.show('Restock policy removed');
        this.saved.emit();
        this.closed.emit();
      },
      error: () => this.busy.set(false),
    };
    if (group && this.scope() === 'group') {
      this.api.setStockGroupPolicy(group.id, 0, 0).subscribe(done);
    } else if (group) {
      // Exact + grouped: leave the group, then drop the barcode policy. Going
      // through the product endpoint while grouped would clear the whole
      // group's policy instead.
      this.api.removeStockGroupMember(group.id, this.barcode(), true).subscribe({
        next: () => this.api.setStockPolicy(this.barcode(), 0, 0).subscribe(done),
        error: () => this.busy.set(false),
      });
    } else {
      this.api.setStockPolicy(this.barcode(), 0, 0).subscribe(done);
    }
  }

  save(): void {
    const minimum = this.minimum();
    if (minimum === 0) {
      this.clear();
      return;
    }
    const target = Math.max(this.target(), minimum);
    const group = this.grouped();
    this.busy.set(true);

    if (this.scope() === 'group') {
      const request = group
        ? this.api.setStockGroupPolicy(group.id, minimum, target)
        : this.api.createStockGroup({
            seedBarcode: this.barcode(),
            reorderPoint: minimum,
            targetStock: target,
          });
      request.subscribe({
        next: () => {
          this.toasts.show(
            group
              ? 'Restock policy saved'
              : `Keeping ${this.productName()} stocked — compatible products will count too`,
          );
          this.saved.emit();
          this.closed.emit();
        },
        error: () => this.busy.set(false),
      });
      return;
    }

    // Exact scope. If the product is grouped, leaving the group is explicit:
    // membership is removed (and remembered) before the barcode policy is set.
    const setExact = () =>
      this.api.setStockPolicy(this.barcode(), minimum, target).subscribe({
        next: () => {
          this.toasts.show('Restock policy saved');
          this.saved.emit();
          this.closed.emit();
        },
        error: () => this.busy.set(false),
      });
    if (group) {
      this.api.removeStockGroupMember(group.id, this.barcode(), true).subscribe({
        next: () => setExact(),
        error: () => this.busy.set(false),
      });
    } else {
      setExact();
    }
  }
}
