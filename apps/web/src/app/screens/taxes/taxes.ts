import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { ToastService } from '../../core/toast.service';
import {
  TaxClass,
  TaxClassInfo,
  TaxClassesResponse,
  TaxLocationRow,
  TaxRateRow,
} from '../../core/models';
import { TaxRateSelectComponent, taxPercentLabel } from '../../ui/tax-rate-select';
import { ToggleComponent } from '../../ui/toggle';

interface ManualRateDraft {
  taxClass: TaxClass;
  pct: string;
}

@Component({
  selector: 'app-taxes',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, RouterLink, TaxRateSelectComponent, ToggleComponent],
  template: `
    <div class="stack">
      <div class="hstack wrap" style="align-items: baseline; gap: 12px">
        <span class="page-title">Tax locations</span>
        <div class="spacer">
          <button type="button" class="btn btn-primary" (click)="adding.set(!adding())">
            + Add location
          </button>
        </div>
      </div>

      <div class="callout">
        Prices are entered <b>before tax</b>; Larder applies the selected rate and stores the final
        total. Old purchases keep the rate that applied when they were saved.
      </div>

      <div class="card stack" style="gap: 8px">
        <span class="section-label">Household default rate</span>
        <ui-tax-rate-select
          [value]="defaultRateId()"
          nullLabel="No default (no tax applied)"
          (valueChange)="setDefault($event)"
        />
        <div class="field-hint">
          Applied whenever a price is saved without picking a rate — scans, quick add, and edits
          alike.
        </div>
      </div>

      @if (adding()) {
        <div class="card stack" style="gap: 14px">
          <div class="hstack" style="gap: 7px">
            <span class="section-label" style="flex: 1">New location</span>
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="mode() === 'lookup'"
              (click)="mode.set('lookup')"
            >
              Look up (ZipTax)
            </button>
            <button
              type="button"
              class="chip chip-sm"
              [class.active]="mode() === 'manual'"
              (click)="mode.set('manual')"
            >
              Enter manually
            </button>
          </div>

          <div class="form-grid">
            <input
              class="input"
              placeholder="Label (e.g. Home)"
              [ngModel]="label()"
              (ngModelChange)="label.set($event)"
            />
            <input
              class="input"
              [placeholder]="
                mode() === 'lookup'
                  ? 'Street address (required for accuracy)'
                  : 'Street address (optional)'
              "
              [ngModel]="address()"
              (ngModelChange)="address.set($event)"
            />
            <input
              class="input"
              placeholder="City"
              [ngModel]="city()"
              (ngModelChange)="city.set($event)"
            />
            <div class="hstack" style="gap: 8px">
              <input
                class="input"
                style="width: 80px"
                placeholder="State"
                [ngModel]="stateCode()"
                (ngModelChange)="stateCode.set($event)"
              />
              <input
                class="input"
                style="flex: 1"
                placeholder="ZIP"
                [ngModel]="postal()"
                (ngModelChange)="postal.set($event)"
              />
            </div>
          </div>

          @if (mode() === 'lookup') {
            <div>
              <div class="field-label">Rates to fetch</div>
              <div class="hstack wrap" style="gap: 7px">
                @for (info of classes(); track info.id) {
                  <button
                    type="button"
                    class="chip chip-sm"
                    [class.active]="lookupClasses().includes(info.id)"
                    (click)="toggleLookupClass(info.id)"
                  >
                    {{ info.label }}
                    @if (info.providerPlan === 'pro') {
                      <span class="pro-mark">Pro</span>
                    }
                  </button>
                }
              </div>
              <div class="field-hint" style="margin-top: 6px">
                Use a full street address — county and district rates can differ within a city.
                Product-specific rates need a ZipTax Pro or Enterprise account.
                @if (!providerConfigured()) {
                  <b>
                    No ZipTax key is configured —
                    <a routerLink="/settings/integrations">add one under Integrations</a>
                    or use manual entry.
                  </b>
                }
              </div>
            </div>
          } @else {
            <div>
              <div class="field-label">Rates</div>
              <div class="stack" style="gap: 8px">
                @for (draft of manualRates(); track $index) {
                  <div class="hstack" style="gap: 8px">
                    <select
                      class="select select-block"
                      style="flex: 1; width: auto"
                      [ngModel]="draft.taxClass"
                      (ngModelChange)="setDraftClass($index, $event)"
                    >
                      @for (info of classes(); track info.id) {
                        <option [ngValue]="info.id">{{ info.label }}</option>
                      }
                    </select>
                    <input
                      class="input"
                      style="width: 110px"
                      placeholder="% e.g. 8.875"
                      [ngModel]="draft.pct"
                      (ngModelChange)="setDraftPct($index, $event)"
                    />
                    @if (manualRates().length > 1) {
                      <button
                        type="button"
                        class="btn btn-outline btn-sm"
                        (click)="removeDraft($index)"
                      >
                        ✕
                      </button>
                    }
                  </div>
                }
                <button
                  type="button"
                  class="btn btn-outline btn-sm"
                  style="align-self: start"
                  (click)="addDraft()"
                >
                  + Another class
                </button>
              </div>
            </div>
          }

          <div class="hstack" style="justify-content: flex-end; gap: 10px">
            <button type="button" class="btn btn-outline" (click)="adding.set(false)">
              Cancel
            </button>
            <button
              type="button"
              class="btn btn-primary"
              [disabled]="!canSave() || saving()"
              (click)="save()"
            >
              {{ mode() === 'lookup' ? 'Look up & save' : 'Save location' }}
            </button>
          </div>
        </div>
      }

      <div class="stack" style="gap: 14px">
        @for (location of locations(); track location.id) {
          <div class="card stack" style="gap: 10px">
            <div class="hstack" style="gap: 10px">
              @if (editingLabel() === location.id) {
                <input
                  class="input"
                  style="width: 180px"
                  [ngModel]="labelDraft()"
                  (ngModelChange)="labelDraft.set($event)"
                  (keyup.enter)="saveLabel(location)"
                />
                <button type="button" class="btn btn-primary btn-sm" (click)="saveLabel(location)">
                  Save
                </button>
              } @else {
                <button
                  type="button"
                  class="location-name"
                  title="Rename"
                  (click)="editLabel(location)"
                >
                  {{ location.label }}
                </button>
              }
              <span class="sub" style="font-size: 12.5px">
                {{ location.city }}, {{ location.state }} {{ location.postalCode }}
              </span>
              @if (location.provider !== 'manual') {
                <span class="badge">{{ location.provider }}</span>
              }
              <div class="spacer" style="text-align: right">
                <button type="button" class="btn btn-outline btn-sm" (click)="remove(location)">
                  Remove
                </button>
              </div>
            </div>
            @if (location.normalizedAddress) {
              <div class="faint" style="font-size: 12px">{{ location.normalizedAddress }}</div>
            }
            <div class="rate-table">
              @for (rate of location.rates; track rate.id) {
                <div class="rate-row" [class.inactive]="!rate.active">
                  <span class="rate-label">{{ rate.label }}</span>
                  <span class="rate-class faint">{{ className(rate.taxClass) }}</span>
                  @if (editingRate() === rate.id) {
                    <input
                      class="input rate-input"
                      [ngModel]="editPct()"
                      (ngModelChange)="editPct.set($event)"
                      (keyup.enter)="saveRate(rate)"
                    />
                    <button type="button" class="btn btn-primary btn-sm" (click)="saveRate(rate)">
                      Save
                    </button>
                  } @else {
                    <button
                      type="button"
                      class="rate-pct tnum"
                      (click)="editRate(rate)"
                      title="Edit rate"
                    >
                      {{ pct(rate.percentage) }}
                    </button>
                  }
                  <ui-toggle [on]="rate.active" (toggled)="toggleRate(rate, $event)" />
                </div>
              }
            </div>
          </div>
        } @empty {
          <div class="card faint" style="text-align: center; font-size: 13.5px">
            No tax locations yet — add one to start recording tax on prices.
          </div>
        }
      </div>

      @if (archived().length) {
        <div>
          <button type="button" class="archived-toggle" (click)="showArchived.update((v) => !v)">
            <span class="archived-caret" [class.open]="showArchived()">▸</span>
            Archived locations
            <span class="tnum">({{ archived().length }})</span>
          </button>
          @if (showArchived()) {
            <div class="stack" style="gap: 8px; margin-top: 10px">
              @for (location of archived(); track location.id) {
                <div class="archived-row">
                  <span class="archived-name">{{ location.label }}</span>
                  <span class="sub" style="font-size: 12px">
                    {{ location.addressLine1 ? location.addressLine1 + ', ' : ''
                    }}{{ location.city }}, {{ location.state }} {{ location.postalCode }}
                  </span>
                  <span class="faint" style="font-size: 11.5px">
                    {{ location.rates.length }} rate{{ location.rates.length === 1 ? '' : 's' }}
                  </span>
                  <div class="spacer" style="text-align: right">
                    <button
                      type="button"
                      class="btn btn-outline btn-sm"
                      (click)="restore(location)"
                    >
                      Restore
                    </button>
                  </div>
                </div>
              }
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .section-label {
      font-size: 14px;
      font-weight: 700;
    }

    .form-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    @media (max-width: 760px) {
      .form-grid {
        grid-template-columns: 1fr;
      }
    }

    .pro-mark {
      font-size: 9px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      opacity: 0.6;
      margin-left: 4px;
    }

    .badge {
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      background: var(--chip);
      border-radius: 999px;
      padding: 3px 8px;
    }

    .rate-table {
      display: flex;
      flex-direction: column;
    }

    .rate-row {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 8px 0;

      &.inactive .rate-label,
      &.inactive .rate-pct {
        opacity: 0.45;
      }
    }

    .rate-label {
      font-size: 13.5px;
      font-weight: 600;
      flex: 1;
    }

    .rate-class {
      font-size: 11.5px;
    }

    .rate-pct {
      font-size: 13.5px;
      font-weight: 700;
      background: none;
      border: none;
      cursor: pointer;
      color: var(--ink);
      padding: 4px 8px;
      border-radius: 8px;

      &:hover {
        background: var(--chip);
      }
    }

    .rate-input {
      width: 90px;
    }

    .location-name {
      font-size: 17px;
      font-weight: 700;
      background: none;
      border: none;
      color: var(--ink);
      cursor: pointer;
      padding: 2px 6px;
      margin-left: -6px;
      border-radius: 8px;

      &:hover {
        background: var(--chip);
      }
    }

    .archived-toggle {
      display: flex;
      align-items: center;
      gap: 7px;
      background: none;
      border: none;
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      color: var(--sub);
      padding: 4px 0;
    }

    .archived-caret {
      display: inline-block;
      transition: transform 0.15s ease;

      &.open {
        transform: rotate(90deg);
      }
    }

    .archived-row {
      display: flex;
      align-items: center;
      gap: 12px;
      background: var(--card);
      border-radius: 12px;
      padding: 10px 16px;
      opacity: 0.75;
    }

    .archived-name {
      font-size: 13.5px;
      font-weight: 700;
    }
  `,
})
export class TaxesScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  readonly state = inject(AppStateService);

  private readonly allLocations = signal<TaxLocationRow[]>([]);
  readonly locations = computed(() => this.allLocations().filter((l) => l.active));
  readonly archived = computed(() => this.allLocations().filter((l) => !l.active));
  readonly showArchived = signal(false);
  readonly classInfo = signal<TaxClassesResponse | null>(null);

  readonly editingLabel = signal<string | null>(null);
  readonly labelDraft = signal('');

  readonly adding = signal(false);
  readonly saving = signal(false);
  readonly mode = signal<'lookup' | 'manual'>('lookup');
  readonly label = signal('');
  readonly address = signal('');
  readonly city = signal('');
  readonly stateCode = signal('');
  readonly postal = signal('');
  readonly lookupClasses = signal<TaxClass[]>(['GENERAL']);
  readonly manualRates = signal<ManualRateDraft[]>([{ taxClass: 'GENERAL', pct: '' }]);

  readonly editingRate = signal<string | null>(null);
  readonly editPct = signal('');

  readonly classes = computed(() => this.classInfo()?.classes ?? []);
  readonly providerConfigured = computed(() => this.classInfo()?.provider.configured ?? false);
  readonly defaultRateId = computed(() => this.state.settings()?.defaultTaxRateId ?? null);

  readonly canSave = computed(() => {
    if (
      !this.label().trim() ||
      !this.city().trim() ||
      this.stateCode().trim().length < 2 ||
      this.postal().trim().length < 5
    ) {
      return false;
    }
    if (this.mode() === 'lookup') {
      return (
        this.providerConfigured() && !!this.address().trim() && this.lookupClasses().length > 0
      );
    }
    return this.manualRates().every((d) => {
      const pct = parseFloat(d.pct);
      return !isNaN(pct) && pct >= 0 && pct <= 100;
    });
  });

  constructor() {
    this.api.taxClasses().subscribe((info) => this.classInfo.set(info));
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api.taxLocations(true).subscribe(({ locations }) => this.allLocations.set(locations));
  }

  className(taxClass: TaxClass): string {
    return this.classes().find((c: TaxClassInfo) => c.id === taxClass)?.label ?? taxClass;
  }

  pct(percentage: number): string {
    return taxPercentLabel(percentage);
  }

  setDefault(rateId: string | null): void {
    this.api.updateSettings({ defaultTaxRateId: rateId }).subscribe((settings) => {
      this.state.setSettings(settings);
      this.toasts.show(rateId ? 'Default tax rate updated' : 'Default tax rate cleared');
    });
  }

  toggleLookupClass(taxClass: TaxClass): void {
    this.lookupClasses.update((list) =>
      list.includes(taxClass) ? list.filter((c) => c !== taxClass) : [...list, taxClass],
    );
  }

  addDraft(): void {
    const used = new Set(this.manualRates().map((d) => d.taxClass));
    const next = this.classes().find((c: TaxClassInfo) => !used.has(c.id))?.id ?? 'GENERAL';
    this.manualRates.update((list) => [...list, { taxClass: next, pct: '' }]);
  }

  removeDraft(index: number): void {
    this.manualRates.update((list) => list.filter((_, i) => i !== index));
  }

  setDraftClass(index: number, taxClass: TaxClass): void {
    this.manualRates.update((list) => list.map((d, i) => (i === index ? { ...d, taxClass } : d)));
  }

  setDraftPct(index: number, pct: string): void {
    this.manualRates.update((list) => list.map((d, i) => (i === index ? { ...d, pct } : d)));
  }

  save(): void {
    if (!this.canSave() || this.saving()) {
      return;
    }
    this.saving.set(true);
    const base = {
      label: this.label().trim(),
      addressLine1: this.address().trim() || undefined,
      city: this.city().trim(),
      state: this.stateCode().trim().toUpperCase(),
      postalCode: this.postal().trim(),
    };
    const request =
      this.mode() === 'lookup'
        ? this.api.lookupTaxLocation({
            ...base,
            addressLine1: this.address().trim(),
            taxClasses: this.lookupClasses(),
          })
        : this.api.createTaxLocation({
            ...base,
            rates: this.manualRates().map((d) => ({
              taxClass: d.taxClass,
              rate: parseFloat(d.pct) / 100,
            })),
          });
    request.subscribe({
      next: (location) => {
        this.saving.set(false);
        this.toasts.show(`Tax location “${location.label}” added`);
        this.resetForm();
        this.load();
        this.state.refreshTaxLocations();
      },
      error: (err) => {
        this.saving.set(false);
        this.toasts.show(err?.error?.detail ?? 'Could not save the location');
      },
    });
  }

  private resetForm(): void {
    this.adding.set(false);
    this.label.set('');
    this.address.set('');
    this.city.set('');
    this.stateCode.set('');
    this.postal.set('');
    this.lookupClasses.set(['GENERAL']);
    this.manualRates.set([{ taxClass: 'GENERAL', pct: '' }]);
  }

  editLabel(location: TaxLocationRow): void {
    this.editingLabel.set(location.id);
    this.labelDraft.set(location.label);
  }

  saveLabel(location: TaxLocationRow): void {
    const label = this.labelDraft().trim();
    if (!label || label === location.label) {
      this.editingLabel.set(null);
      return;
    }
    this.api.updateTaxLocation(location.id, { label }).subscribe({
      next: () => {
        this.editingLabel.set(null);
        this.toasts.show(`Renamed to “${label}”`);
        this.load();
        this.state.refreshTaxLocations();
      },
      error: (err) => this.toasts.show(err?.error?.detail ?? 'Could not rename'),
    });
  }

  restore(location: TaxLocationRow): void {
    this.api.updateTaxLocation(location.id, { active: true }).subscribe({
      next: () => {
        this.toasts.show(`“${location.label}” restored`);
        this.load();
        this.state.refreshTaxLocations();
      },
      error: (err) => this.toasts.show(err?.error?.detail ?? 'Could not restore'),
    });
  }

  editRate(rate: TaxRateRow): void {
    this.editingRate.set(rate.id);
    this.editPct.set(String(rate.percentage));
  }

  saveRate(rate: TaxRateRow): void {
    const pct = parseFloat(this.editPct());
    if (isNaN(pct) || pct < 0 || pct > 100) {
      this.toasts.show('Enter a percentage between 0 and 100');
      return;
    }
    this.api.updateTaxRate(rate.id, { rate: pct / 100 }).subscribe(() => {
      this.editingRate.set(null);
      this.toasts.show(`${rate.label} · ${taxPercentLabel(pct)}`);
      this.load();
      this.state.refreshTaxLocations();
    });
  }

  toggleRate(rate: TaxRateRow, active: boolean): void {
    this.api.updateTaxRate(rate.id, { active }).subscribe(() => {
      this.load();
      this.state.refreshTaxLocations();
    });
  }

  remove(location: TaxLocationRow): void {
    this.api.deleteTaxLocation(location.id).subscribe(() => {
      this.toasts.show(`“${location.label}” removed`);
      this.load();
      this.state.refreshTaxLocations();
      // A removed location may have held the household default.
      this.state.refreshSettings();
    });
  }
}
