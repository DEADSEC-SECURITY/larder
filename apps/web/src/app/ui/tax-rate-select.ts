import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AppStateService } from '../core/app-state.service';
import { money } from '../core/format';
import { TaxLocationRow, TaxRateRow } from '../core/models';

/** `0.00%`-style label; keeps sub-cent precision (8.875%) when present. */
export function taxPercentLabel(percentage: number): string {
  const rounded = Math.round(percentage * 100) / 100;
  return `${rounded === percentage ? percentage.toFixed(2) : percentage}%`;
}

export function findTaxRate(locations: TaxLocationRow[], id: string | null): TaxRateRow | null {
  if (!id) {
    return null;
  }
  for (const location of locations) {
    const rate = location.rates.find((r) => r.id === id);
    if (rate) {
      return rate;
    }
  }
  return null;
}

/**
 * Live `Subtotal $4.99 + tax $0.44 = $5.43 total` preview for an entered
 * price — the pre-tax subtotal by default, or the final total when the
 * price already includes tax.
 */
export function taxBreakdownHint(
  value: number,
  rate: TaxRateRow | null,
  taxIncluded = false,
): string {
  const fraction = rate?.rate ?? 0;
  if (taxIncluded) {
    const subtotal = Math.round((value / (1 + fraction)) * 100) / 100;
    const tax = Math.round((value - subtotal) * 100) / 100;
    return `Subtotal ${money(subtotal)} + tax ${money(tax)} = ${money(value)} total`;
  }
  const tax = Math.round(value * fraction * 100) / 100;
  return `Subtotal ${money(value)} + tax ${money(tax)} = ${money(value + tax)} total`;
}

/**
 * Grouped tax-rate selector shared by every price form and the household
 * default setting. Value is a rate UUID or null (the explicit no-tax choice —
 * relabel via nullLabel where null means something else).
 */
@Component({
  selector: 'ui-tax-rate-select',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  template: `
    <select class="select select-block" [ngModel]="value()" (ngModelChange)="valueChange.emit($event)">
      <option [ngValue]="null">{{ nullLabel() }}</option>
      @for (location of locations(); track location.id) {
        <optgroup [label]="groupLabel(location)">
          @for (rate of activeRates(location); track rate.id) {
            <option [ngValue]="rate.id">{{ rate.label }} · {{ pct(rate.percentage) }}</option>
          }
        </optgroup>
      }
    </select>
  `,
})
export class TaxRateSelectComponent {
  readonly value = input.required<string | null>();
  readonly nullLabel = input('No tax (0%)');
  readonly valueChange = output<string | null>();

  private readonly state = inject(AppStateService);

  readonly locations = computed(() =>
    this.state.taxLocations().filter((l) => l.active && l.rates.some((r) => r.active)),
  );

  activeRates(location: TaxLocationRow): TaxRateRow[] {
    return location.rates.filter((r) => r.active);
  }

  groupLabel(location: TaxLocationRow): string {
    return `${location.label} · ${location.city}, ${location.state}`;
  }

  pct(percentage: number): string {
    return taxPercentLabel(percentage);
  }
}
