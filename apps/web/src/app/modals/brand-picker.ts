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
import { BrandRef, BrandRow } from '../core/models';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';

/** Multi-select brand assignment for one product, with a single primary brand. */
@Component({
  selector: 'app-brand-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent],
  template: `
    <ui-modal title="Edit brands" [width]="460" (closed)="closed.emit()">
      @if (selected().length) {
        <div class="stack" style="gap: 6px">
          @for (id of selected(); track id) {
            <div class="chosen">
              <button
                type="button"
                class="primary-dot"
                [class.on]="primary() === id"
                title="Make primary"
                (click)="primary.set(id)"
              ></button>
              <span style="flex: 1; font-size: 13.5px; font-weight: 600">{{
                brandName(id)
              }}</span>
              @if (primary() === id) {
                <span class="pill pill-sm pill-ok">primary</span>
              }
              <button type="button" class="alias-x" (click)="toggle(id)">✕</button>
            </div>
          }
        </div>
        <div class="field-hint">The dot marks the primary brand shown across the app.</div>
      } @else {
        <div class="faint" style="font-size: 13px">
          No brands assigned — pick from the list or search below.
        </div>
      }
      <input
        class="input input-on-bg"
        placeholder="Search brands…"
        [ngModel]="query()"
        (ngModelChange)="query.set($event)"
      />
      <div class="options">
        @for (brand of options(); track brand.id) {
          <div class="option row-hover row-line" (click)="toggle(brand.id)">
            <span style="flex: 1; font-size: 13.5px; font-weight: 600">{{ brand.name }}</span>
            <span class="sub" style="font-size: 12px">{{ brand.productCount }} products</span>
          </div>
        } @empty {
          <div class="faint" style="padding: 10px 6px; font-size: 12.5px">
            No matching active brands — create it on the Brands screen first.
          </div>
        }
      </div>
      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" (click)="save()">Save</button>
      </div>
    </ui-modal>
  `,
  styles: `
    .chosen {
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--accent-bg);
      border-radius: 10px;
      padding: 8px 12px;
    }

    .primary-dot {
      width: 16px;
      height: 16px;
      border-radius: 50%;
      border: 2px solid var(--accent);
      background: transparent;
      cursor: pointer;
      flex: none;
      padding: 0;

      &.on {
        background: var(--accent);
      }
    }

    .alias-x {
      border: none;
      background: none;
      color: var(--faint);
      cursor: pointer;
      font-size: 11px;
      padding: 1px 4px;

      &:hover {
        color: var(--danger);
      }
    }

    .options {
      max-height: 190px;
      overflow: auto;
    }

    .option {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 9px 8px;
    }

    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }
  `,
})
export class BrandPickerModal {
  readonly barcode = input.required<string>();
  readonly current = input.required<BrandRef[]>();
  readonly currentPrimary = input<string | null>(null);
  readonly closed = output<void>();
  readonly saved = output<void>();

  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly all = signal<BrandRow[]>([]);
  readonly selected = signal<string[]>([]);
  readonly primary = signal<string | null>(null);
  readonly query = signal('');

  readonly options = computed(() => {
    const query = this.query().toLowerCase().trim();
    const chosen = new Set(this.selected());
    return this.all()
      .filter((brand) => brand.active && !chosen.has(brand.id))
      .filter((brand) => !query || brand.name.toLowerCase().includes(query))
      .slice(0, 6);
  });

  ngOnInit(): void {
    this.selected.set(this.current().map((brand) => brand.id));
    this.primary.set(this.currentPrimary() ?? this.current()[0]?.id ?? null);
    this.api.brands().subscribe(({ brands }) => this.all.set(brands));
  }

  brandName(id: string): string {
    return (
      this.all().find((brand) => brand.id === id)?.name ??
      this.current().find((brand) => brand.id === id)?.name ??
      id
    );
  }

  toggle(id: string): void {
    const list = this.selected();
    if (list.includes(id)) {
      this.selected.set(list.filter((entry) => entry !== id));
      if (this.primary() === id) {
        this.primary.set(this.selected()[0] ?? null);
      }
    } else {
      this.selected.set([...list, id]);
      if (!this.primary()) {
        this.primary.set(id);
      }
    }
  }

  save(): void {
    this.api
      .setProductBrands(this.barcode(), this.selected(), this.primary() ?? undefined)
      .subscribe({
        next: () => {
          this.toasts.show('Brands updated');
          this.saved.emit();
          this.closed.emit();
        },
        error: () => this.toasts.show('Could not update brands'),
      });
  }
}
