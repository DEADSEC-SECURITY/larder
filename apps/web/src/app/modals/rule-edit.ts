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
import {
  BrandRow,
  CategoryRow,
  ProductRow,
  RoutingRule,
  RuleBehavior,
  RuleMatchType,
} from '../core/models';
import { ToastService } from '../core/toast.service';
import { ModalComponent } from '../ui/modal';
import { ToggleComponent } from '../ui/toggle';

interface MatcherOption {
  id: string;
  label: string;
  note: string;
}

/**
 * Create or edit a store-routing rule. The matcher is immutable on existing
 * rules (delete and recreate to change it), matching the backend contract.
 */
@Component({
  selector: 'app-rule-edit',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent, ToggleComponent],
  template: `
    <ui-modal [title]="rule() ? 'Edit rule' : 'New shopping rule'" [width]="480" (closed)="closed.emit()">
      @if (!rule()) {
        <div>
          <div class="field-label">Match</div>
          <div class="hstack" style="gap: 7px; margin-bottom: 8px">
            @for (type of matchTypes; track type) {
              <button
                type="button"
                class="chip chip-sm"
                [class.active]="matchType() === type"
                (click)="setMatchType(type)"
              >
                {{ type[0].toUpperCase() + type.slice(1) }}
              </button>
            }
          </div>
          @if (picked(); as chosen) {
            <div class="picked">
              <span class="picked-name">{{ chosen.label }}</span>
              <button type="button" class="picked-change" (click)="clearPick()">change</button>
            </div>
          } @else {
            <input
              class="input input-on-bg"
              [placeholder]="'Search ' + matchType() + 's…'"
              [ngModel]="query()"
              (ngModelChange)="query.set($event)"
            />
            <div class="options">
              @for (option of options(); track option.id) {
                <div class="option row-hover row-line" (click)="pick(option)">
                  <span style="flex: 1; font-size: 13.5px; font-weight: 600">{{
                    option.label
                  }}</span>
                  <span class="sub" style="font-size: 12px">{{ option.note }}</span>
                </div>
              } @empty {
                <div class="faint" style="padding: 10px 6px; font-size: 12.5px">
                  Nothing matches.
                </div>
              }
            </div>
          }
        </div>
      } @else {
        <div class="picked" style="background: var(--chip)">
          <span class="sub" style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em"
            >{{ rule()!.matchType }}</span
          >
          <span class="picked-name" style="color: var(--ink)">{{ rule()!.match.label }}</span>
        </div>
        <div class="field-hint" style="margin-top: -8px">
          The matcher can’t be changed — delete this rule and create a new one instead.
        </div>
        <div>
          <div class="field-label">Rule name</div>
          <input class="input input-on-bg" [ngModel]="name()" (ngModelChange)="name.set($event)" />
        </div>
      }

      <div>
        <div class="field-label">Buy at</div>
        <select class="select select-block" [ngModel]="storeId()" (ngModelChange)="storeId.set($event)">
          <option [ngValue]="null">— choose a store —</option>
          @for (store of state.stores(); track store.id) {
            <option [ngValue]="store.id">
              {{ store.name }}{{ store.active ? '' : ' (inactive)' }}
            </option>
          }
        </select>
      </div>

      <div>
        <div class="field-label">Behavior</div>
        <div class="hstack" style="gap: 7px; margin-bottom: 6px">
          <button
            type="button"
            class="chip chip-sm"
            [class.active]="behavior() === 'prefer'"
            (click)="behavior.set('prefer')"
          >
            Prefer
          </button>
          <button
            type="button"
            class="chip chip-sm"
            [class.active]="behavior() === 'require'"
            (click)="behavior.set('require')"
          >
            Require
          </button>
        </div>
        <div class="field-hint">
          {{
            behavior() === 'prefer'
              ? 'Use this store while it is active; fall back to price history if it goes inactive.'
              : 'Always keep this store, even when inactive — the list will show a warning instead of rerouting.'
          }}
        </div>
      </div>

      <details class="advanced">
        <summary>Advanced</summary>
        <div class="hstack" style="gap: 10px; margin-top: 10px">
          <span style="font-size: 13px; flex: 1">
            Priority
            <span class="field-hint" style="display: block; margin-top: 2px">
              Tie-breaker among rules of the same kind — higher wins
            </span>
          </span>
          <input
            type="number"
            class="select"
            style="width: 90px"
            min="-1000"
            max="1000"
            [ngModel]="priority()"
            (ngModelChange)="priority.set($event)"
          />
        </div>
      </details>

      <div class="hstack" style="gap: 14px">
        <div style="flex: 1">
          <div style="font-size: 13.5px; font-weight: 600">Enabled</div>
        </div>
        <ui-toggle [on]="enabled()" (toggled)="enabled.set($event)" />
      </div>

      @if (conflict()) {
        <div class="conflict">
          A rule already exists for this {{ matchType() }}.
          <button type="button" class="link-accent" (click)="conflictOpen.emit()">
            Open the existing rule
          </button>
        </div>
      }

      <div class="actions">
        <button type="button" class="btn btn-outline" (click)="closed.emit()">Cancel</button>
        <button type="button" class="btn btn-primary" [disabled]="!canSave()" (click)="save()">
          Save rule
        </button>
      </div>
    </ui-modal>
  `,
  styles: `
    .picked {
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--accent-bg);
      border-radius: var(--r-input);
      padding: 12px 14px;
    }

    .picked-name {
      font-size: 14px;
      font-weight: 700;
      color: var(--accent);
      flex: 1;
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

    .options {
      max-height: 200px;
      overflow: auto;
      margin-top: 6px;
    }

    .option {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 9px 8px;
    }

    .advanced summary {
      font-size: 12.5px;
      font-weight: 600;
      color: var(--sub);
      cursor: pointer;
    }

    .conflict {
      background: var(--warn-bg);
      color: var(--warn);
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
export class RuleEditModal {
  readonly rule = input<RoutingRule | null>(null);
  /** Prefill for create mode (e.g. arriving from a brand's "create rule" link). */
  readonly presetMatchType = input<RuleMatchType | null>(null);
  readonly presetMatcherId = input<string | null>(null);
  readonly closed = output<void>();
  readonly saved = output<void>();
  readonly conflictOpen = output<void>();

  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  readonly state = inject(AppStateService);

  readonly matchTypes: RuleMatchType[] = ['product', 'brand', 'category'];
  readonly matchType = signal<RuleMatchType>('brand');
  readonly picked = signal<MatcherOption | null>(null);
  readonly query = signal('');
  readonly name = signal('');
  readonly storeId = signal<string | null>(null);
  readonly behavior = signal<RuleBehavior>('prefer');
  readonly priority = signal(0);
  readonly enabled = signal(true);
  readonly conflict = signal(false);

  private readonly products = signal<ProductRow[]>([]);
  private readonly brands = signal<BrandRow[]>([]);
  private readonly categories = signal<CategoryRow[]>([]);

  readonly options = computed<MatcherOption[]>(() => {
    const query = this.query().toLowerCase().trim();
    const limit = 6;
    if (this.matchType() === 'product') {
      return this.products()
        .filter((p) => p.name && (!query || p.name.toLowerCase().includes(query)))
        .slice(0, limit)
        .map((p) => ({
          id: p.barcode,
          label: p.name!,
          note: p.brand ?? '',
        }));
    }
    if (this.matchType() === 'brand') {
      return this.brands()
        .filter((b) => !query || b.name.toLowerCase().includes(query))
        .slice(0, limit)
        .map((b) => ({
          id: b.id,
          label: b.name,
          note: `${b.productCount} products`,
        }));
    }
    return this.categories()
      .filter((c) => !query || c.name.toLowerCase().includes(query))
      .slice(0, limit)
      .map((c) => ({
        id: c.id,
        label: c.parentName ? `${c.parentName} / ${c.name}` : c.name,
        note: `${c.productCount} products`,
      }));
  });

  readonly canSave = computed(() => {
    if (!this.storeId()) {
      return false;
    }
    return this.rule() !== null || this.picked() !== null;
  });

  ngOnInit(): void {
    const rule = this.rule();
    if (rule) {
      this.matchType.set(rule.matchType);
      this.name.set(rule.name);
      this.storeId.set(rule.store?.id ?? null);
      this.behavior.set(rule.behavior);
      this.priority.set(rule.priority);
      this.enabled.set(rule.enabled);
      return;
    }
    this.api.products().subscribe(({ products }) => this.products.set(products));
    this.api.brands().subscribe(({ brands }) => {
      this.brands.set(brands);
      const presetId = this.presetMatcherId();
      if (this.presetMatchType() === 'brand' && presetId) {
        const brand = brands.find((b) => b.id === presetId);
        if (brand) {
          this.picked.set({
            id: brand.id,
            label: brand.name,
            note: `${brand.productCount} products`,
          });
        }
      }
    });
    this.api.categories().subscribe(({ categories }) => this.categories.set(categories));
    if (this.presetMatchType()) {
      this.matchType.set(this.presetMatchType()!);
    }
  }

  setMatchType(type: RuleMatchType): void {
    this.matchType.set(type);
    this.picked.set(null);
    this.query.set('');
    this.conflict.set(false);
  }

  pick(option: MatcherOption): void {
    this.picked.set(option);
    this.conflict.set(false);
  }

  clearPick(): void {
    this.picked.set(null);
  }

  save(): void {
    this.conflict.set(false);
    const existing = this.rule();
    if (existing) {
      this.api
        .updateRoutingRule(existing.id, {
          name: this.name().trim() || undefined,
          storeId: this.storeId()!,
          behavior: this.behavior(),
          priority: this.priority(),
          enabled: this.enabled(),
        })
        .subscribe({
          next: () => {
            this.toasts.show('Rule updated');
            this.saved.emit();
            this.closed.emit();
          },
          error: (error) => this.fail(error),
        });
      return;
    }
    const type = this.matchType();
    const matcher = this.picked()!;
    this.api
      .createRoutingRule({
        matchType: type,
        productBarcode: type === 'product' ? matcher.id : undefined,
        brandId: type === 'brand' ? matcher.id : undefined,
        categoryId: type === 'category' ? matcher.id : undefined,
        storeId: this.storeId()!,
        behavior: this.behavior(),
        priority: this.priority(),
        enabled: this.enabled(),
      })
      .subscribe({
        next: () => {
          this.toasts.show('Rule created');
          this.saved.emit();
          this.closed.emit();
        },
        error: (error) => this.fail(error),
      });
  }

  private fail(error: { status?: number }): void {
    if (error?.status === 409) {
      this.conflict.set(true);
      return;
    }
    if (error?.status === 404) {
      this.toasts.show('The target no longer exists — refreshing');
      this.saved.emit();
      this.closed.emit();
      return;
    }
    this.toasts.show('Could not save the rule');
  }
}
