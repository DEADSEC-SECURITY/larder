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
import { CategoryRow, ProductDetail, ProductKind } from '../core/models';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';

/**
 * Edit product metadata: identity, classification, package details, and
 * defaults. Only fields that actually changed are sent — a PATCHed field is
 * marked user-owned on the backend and protected from provider enrichment,
 * so untouched fields must stay out of the payload.
 */
@Component({
  selector: 'app-product-edit',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent],
  template: `
    <ui-modal title="Edit product" [width]="560" (closed)="closed.emit()">
      <div class="section-label">Identity</div>
      <div class="two-col">
        <div>
          <div class="field-label">Name</div>
          <input
            class="input input-on-bg"
            placeholder="Product name"
            [ngModel]="name()"
            (ngModelChange)="name.set($event)"
          />
        </div>
        <div>
          <div class="field-label">Brand</div>
          <input
            class="input input-on-bg"
            placeholder="Brand"
            [ngModel]="brand()"
            (ngModelChange)="brand.set($event)"
          />
        </div>
      </div>
      <div>
        <div class="field-label">Description</div>
        <input
          class="input input-on-bg"
          placeholder="Short description"
          [ngModel]="description()"
          (ngModelChange)="description.set($event)"
        />
      </div>

      <div class="section-label">Classification</div>
      <div>
        <div class="field-label">Kind</div>
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
        <div class="field-label">Category</div>
        <select
          class="select select-block"
          [ngModel]="categoryId()"
          (ngModelChange)="categoryId.set($event)"
        >
          <option [ngValue]="null">Uncategorized</option>
          @for (category of categoryOptions(); track category.id) {
            <option [ngValue]="category.id">{{ category.label }}</option>
          }
        </select>
      </div>

      <div class="section-label">Package details</div>
      <div class="two-col">
        <div>
          <div class="field-label">Manufacturer</div>
          <input
            class="input input-on-bg"
            placeholder="e.g. Acme Industries"
            [ngModel]="manufacturer()"
            (ngModelChange)="manufacturer.set($event)"
          />
        </div>
        <div>
          <div class="field-label">Package size</div>
          <input
            class="input input-on-bg"
            placeholder="e.g. 100 Count"
            [ngModel]="packageSize()"
            (ngModelChange)="packageSize.set($event)"
          />
        </div>
        <div>
          <div class="field-label">Material</div>
          <input
            class="input input-on-bg"
            placeholder="e.g. Nitrile"
            [ngModel]="material()"
            (ngModelChange)="material.set($event)"
          />
        </div>
        <div>
          <div class="field-label">Variant</div>
          <input
            class="input input-on-bg"
            placeholder="e.g. Large"
            [ngModel]="variant()"
            (ngModelChange)="variant.set($event)"
          />
        </div>
      </div>

      <div class="section-label">Defaults</div>
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
          New units land here unless a location is chosen at scan time. Without one, the
          category default applies.
        </div>
      </div>

      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" [disabled]="!canSave()" (click)="save()">
          Save
        </button>
      </div>
    </ui-modal>
  `,
  styles: `
    .section-label {
      font-size: 11.5px;
      font-weight: 700;
      letter-spacing: 0.07em;
      text-transform: uppercase;
      color: var(--faint);
      margin-bottom: -6px;

      &:not(:first-child) {
        margin-top: 6px;
      }
    }

    .two-col {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    @media (max-width: 560px) {
      .two-col {
        grid-template-columns: 1fr;
      }
    }

    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      margin-top: 4px;
    }
  `,
})
export class ProductEditModal {
  readonly product = input.required<ProductDetail>();
  readonly closed = output<void>();
  readonly saved = output<void>();

  readonly state = inject(AppStateService);
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly kinds = PRODUCT_KINDS;

  readonly name = signal('');
  readonly brand = signal('');
  readonly description = signal('');
  readonly kind = signal<ProductKind>('food');
  readonly categoryId = signal<string | null>(null);
  readonly manufacturer = signal('');
  readonly packageSize = signal('');
  readonly material = signal('');
  readonly variant = signal('');
  readonly storageLocationId = signal<string | null>(null);

  private readonly categories = signal<CategoryRow[]>([]);
  readonly categoryOptions = computed(() =>
    this.categories()
      // The null option already covers Uncategorized.
      .filter((c) => c.id !== 'local:uncategorized')
      .map((c) => ({
        id: c.id,
        label: c.parentName ? `${c.parentName} / ${c.name}` : c.name,
      }))
      .sort((a, b) => a.label.localeCompare(b.label)),
  );

  readonly canSave = computed(() => !!this.name().trim());

  constructor() {
    this.api.categories().subscribe(({ categories }) => this.categories.set(categories));
    this.state.refreshStorageLocations();
  }

  ngOnInit(): void {
    const product = this.product();
    this.name.set(product.name ?? '');
    this.brand.set(product.brand ?? '');
    this.description.set(product.description ?? '');
    this.kind.set(product.productKind);
    this.categoryId.set(product.category?.id ?? null);
    this.manufacturer.set(product.manufacturer ?? '');
    this.packageSize.set(product.packageSize ?? '');
    this.material.set(product.material ?? '');
    this.variant.set(product.variant ?? '');
    this.storageLocationId.set(product.defaultStorageLocationId);
  }

  save(): void {
    const product = this.product();
    const payload: Parameters<ApiService['updateProduct']>[1] = {};
    const text = (value: string) => value.trim() || null;
    if (this.name().trim() && this.name().trim() !== (product.name ?? '')) {
      payload.name = this.name().trim();
    }
    if (text(this.brand()) !== (product.brand ?? null)) {
      payload.brand = text(this.brand());
    }
    if (this.kind() !== product.productKind) {
      payload.productKind = productKindUpper(this.kind());
    }
    if (text(this.description()) !== (product.description ?? null)) {
      payload.description = text(this.description());
    }
    if (text(this.manufacturer()) !== (product.manufacturer ?? null)) {
      payload.manufacturer = text(this.manufacturer());
    }
    if (text(this.packageSize()) !== (product.packageSize ?? null)) {
      payload.packageSize = text(this.packageSize());
    }
    if (text(this.material()) !== (product.material ?? null)) {
      payload.material = text(this.material());
    }
    if (text(this.variant()) !== (product.variant ?? null)) {
      payload.variant = text(this.variant());
    }
    if (this.categoryId() !== (product.category?.id ?? null)) {
      payload.categoryId = this.categoryId();
    }
    if (this.storageLocationId() !== product.defaultStorageLocationId) {
      payload.defaultStorageLocationId = this.storageLocationId();
    }
    if (!Object.keys(payload).length) {
      this.closed.emit();
      return;
    }
    this.api.updateProduct(product.barcode, payload).subscribe({
      next: () => {
        this.toasts.show('Product updated');
        this.saved.emit();
        this.closed.emit();
      },
      error: () => this.toasts.show('Could not save — check the form'),
    });
  }
}
