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
import { Router } from '@angular/router';

import { ApiService } from '../core/api.service';
import { BrandRow } from '../core/models';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';
import { TermChipsComponent } from '../ui/term-chips';
import { ToggleComponent } from '../ui/toggle';

/** Create or edit a canonical brand, including its alias list. */
@Component({
  selector: 'app-brand-edit',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent, TermChipsComponent, ToggleComponent],
  template: `
    <ui-modal [title]="brand() ? 'Edit brand' : 'New brand'" [width]="460" (closed)="closed.emit()">
      <div>
        <div class="field-label">Display name</div>
        <input
          class="input input-on-bg"
          placeholder="Kirkland Signature"
          [ngModel]="name()"
          (ngModelChange)="name.set($event)"
        />
      </div>
      <div>
        <div class="field-label">Aliases</div>
        <ui-term-chips
          [value]="aliases()"
          placeholder="Type an alias, press Enter…"
          (valueChange)="aliases.set($event)"
        />
        <div class="field-hint">
          Aliases are alternate source spellings (labels, Open Food Facts strings) that fold
          into this brand — not additional display names.
        </div>
      </div>
      <div class="hstack" style="gap: 14px">
        <div style="flex: 1">
          <div style="font-size: 13.5px; font-weight: 600">Active</div>
          <div class="sub" style="font-size: 12px">
            Inactive brands stop matching new products
          </div>
        </div>
        <ui-toggle [on]="active()" (toggled)="active.set($event)" />
      </div>
      @if (brand(); as b) {
        <div class="rule-link">
          @if (b.routingRuleId) {
            <button type="button" class="link-accent" (click)="openRule(b.routingRuleId)">
              Edit this brand’s store rule →
            </button>
          } @else {
            <button type="button" class="link-accent" (click)="createRule(b)">
              Create a store rule for this brand →
            </button>
          }
        </div>
      }
      @if (conflict()) {
        <div class="conflict">That name or alias is already in use by another brand.</div>
      }
      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button
          type="button"
          class="btn btn-primary"
          [disabled]="!name().trim()"
          (click)="save()"
        >
          Save
        </button>
      </div>
    </ui-modal>
  `,
  styles: `
    .rule-link {
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }

    .conflict {
      background: var(--danger-bg);
      color: var(--danger);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 12.5px;
      font-weight: 600;
    }

    .actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }
  `,
})
export class BrandEditModal {
  readonly brand = input<BrandRow | null>(null);
  readonly closed = output<void>();
  readonly saved = output<void>();

  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly router = inject(Router);

  readonly name = signal('');
  readonly aliases = signal<string[]>([]);
  readonly active = signal(true);
  readonly conflict = signal(false);

  ngOnInit(): void {
    const brand = this.brand();
    if (brand) {
      this.name.set(brand.name);
      this.aliases.set([...brand.aliases]);
      this.active.set(brand.active);
    }
  }

  openRule(ruleId: string): void {
    this.closed.emit();
    this.router.navigate(['/settings/rules'], { queryParams: { rule: ruleId } });
  }

  createRule(brand: BrandRow): void {
    this.closed.emit();
    this.router.navigate(['/settings/rules'], {
      queryParams: { create: 'brand', brandId: brand.id },
    });
  }

  save(): void {
    // A draft still in the chip input was committed by its blur before this
    // click ran, so aliases() is complete.
    const aliases = this.aliases();
    this.conflict.set(false);
    const existing = this.brand();
    const request = existing
      ? this.api.updateBrand(existing.id, {
          name: this.name().trim(),
          aliases,
          active: this.active(),
        })
      : this.api.createBrand({ name: this.name().trim(), aliases });
    request.subscribe({
      next: () => {
        this.toasts.show(existing ? 'Brand updated' : 'Brand created');
        this.saved.emit();
        this.closed.emit();
      },
      error: (error) => {
        if (error?.status === 409) {
          this.conflict.set(true);
          return;
        }
        this.toasts.show('Could not save the brand');
      },
    });
  }
}
