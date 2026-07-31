import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { expiryInfo } from '../core/format';

@Component({
  selector: 'ui-expiry-pill',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="pill" [class]="'pill-' + info().tone">{{ info().label }}</span>`,
})
export class ExpiryPillComponent {
  readonly date = input<string | null>(null);
  readonly daysLeft = input<number | null>(null);
  readonly windowDays = input(4);

  readonly info = computed(() => expiryInfo(this.date(), this.daysLeft(), this.windowDays()));
}
