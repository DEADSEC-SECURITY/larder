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
import { PRODUCT_KINDS, productKindUpper } from '../core/format';
import { CategoryRow, ProductKind } from '../core/models';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';
import { StepperComponent } from '../ui/stepper';

const PRESETS = [3, 7, 14, 30, 90, 180, 365];

/**
 * Category defaults: product kind, expiration rule, and storage location.
 * "No expiration default" is a valid state — the category may then inherit a
 * parent rule, or leave undated units at "No date".
 */
@Component({
  selector: 'app-edit-rule',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent, StepperComponent],
  template: `
    <ui-modal [title]="category().name" [width]="440" (closed)="closed.emit()">
      <div style="font-size: 13px; color: var(--sub); line-height: 1.5">{{ description() }}</div>
      <div>
        <div class="field-label">Product kind</div>
        <div class="hstack wrap" style="gap: 6px">
          @for (option of kinds; track option.kind) {
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="kind() === option.kind"
              (click)="kind.set(option.kind)"
            >
              {{ option.label }}
            </button>
          }
        </div>
      </div>
      <div>
        <div class="hstack" style="gap: 8px; margin-bottom: 5px">
          <span class="field-label" style="margin-bottom: 0">Default shelf life</span>
          <button
            type="button"
            class="chip chip-sm"
            [class.active]="noDays()"
            (click)="noDays.set(!noDays())"
          >
            No expiration default
          </button>
        </div>
        @if (!noDays()) {
          <ui-stepper
            [value]="days()"
            [min]="1"
            [max]="3650"
            [label]="days() + ' days'"
            (valueChange)="days.set($event)"
          />
          <div class="hstack wrap" style="gap: 6px; margin-top: 8px">
            @for (preset of presets; track preset) {
              <button
                type="button"
                class="chip chip-sm"
                [class.active]="days() === preset"
                (click)="days.set(preset)"
              >
                {{ preset }}d
              </button>
            }
          </div>
          <div class="field-hint" style="margin-top: 6px">
            Units added without a printed date will be estimated to expire
            {{ days() }} days after they’re added. Confirmed dates are never changed.
          </div>
        } @else {
          <div class="field-hint">{{ noDaysHint() }}</div>
        }
      </div>
      <div>
        <div class="field-label">Default storage location</div>
        <select
          class="select select-block"
          [ngModel]="storageLocationId()"
          (ngModelChange)="storageLocationId.set($event)"
        >
          <option [ngValue]="null">No default</option>
          @for (location of state.activeStorageLocations(); track location.id) {
            <option [ngValue]="location.id">{{ location.name }}</option>
          }
        </select>
        <div class="field-hint" style="margin-top: 5px">
          New units in this category land here unless the product has its own default.
        </div>
      </div>
      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" (click)="save()">Save</button>
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
export class EditRuleModal {
  readonly category = input.required<CategoryRow>();
  readonly closed = output<void>();
  readonly saved = output<void>();

  readonly state = inject(AppStateService);
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly presets = PRESETS;
  readonly kinds = PRODUCT_KINDS;
  readonly days = signal(7);
  readonly noDays = signal(false);
  readonly kind = signal<ProductKind>('food');
  readonly storageLocationId = signal<string | null>(null);

  readonly description = computed(() => {
    const category = this.category();
    if (category.parentName) {
      const fallback =
        category.inheritedDays != null
          ? `${category.inheritedDays} days from ${category.parentName}.`
          : 'no rule.';
      return (
        `Rule for ${category.parentName} / ${category.name}. A subcategory rule overrides ` +
        `the parent; removing it falls back to ${fallback}`
      );
    }
    return `Defaults for everything in ${category.name} without its own subcategory rule.`;
  });

  readonly noDaysHint = computed(() => {
    const category = this.category();
    return category.inheritedDays != null && category.parentName
      ? `No default of its own — inherits ${category.inheritedDays} days from ${category.parentName}.`
      : 'No expiration default — undated units show “No date”.';
  });

  ngOnInit(): void {
    const category = this.category();
    this.noDays.set(category.days == null);
    this.days.set(category.days ?? category.inheritedDays ?? 7);
    this.kind.set(category.productKind);
    this.storageLocationId.set(category.defaultStorageLocationId);
  }

  save(): void {
    const days = this.noDays() ? null : this.days();
    this.api
      .updateCategory(this.category().id, {
        days,
        productKind: productKindUpper(this.kind()),
        defaultStorageLocationId: this.storageLocationId(),
      })
      .subscribe(() => {
        this.toasts.show(days == null ? 'Saved — no expiration default' : `Saved · ${days} days`);
        this.saved.emit();
        this.closed.emit();
      });
  }
}
