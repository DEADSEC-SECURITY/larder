import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

@Component({
  selector: 'ui-metric-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="metric card" (click)="pressed.emit()" [class.clickable]="true">
      <div class="label">{{ label() }}</div>
      <div class="value display" [style.color]="tone() || 'var(--ink)'">{{ value() }}</div>
      <div class="note">{{ note() }}</div>
    </div>
  `,
  styles: `
    .metric {
      cursor: pointer;
      transition: border-color 0.12s;
    }

    .metric:hover {
      border-color: var(--accent);
    }

    .label {
      font-size: 12.5px;
      color: var(--sub);
      font-weight: 500;
    }

    .value {
      font-size: 36px;
      line-height: 1.2;
    }

    .note {
      font-size: 11.5px;
      color: var(--faint);
    }
  `,
})
export class MetricCardComponent {
  readonly label = input.required<string>();
  readonly value = input.required<string | number>();
  readonly note = input('');
  readonly tone = input<string>();
  readonly pressed = output<void>();
}
