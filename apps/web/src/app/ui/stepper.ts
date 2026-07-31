import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

@Component({
  selector: 'ui-stepper',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="frame">
      <button type="button" (click)="step(-1)" aria-label="Decrease">−</button>
      <span class="value">{{ label() || value() }}</span>
      <button type="button" (click)="step(1)" aria-label="Increase">+</button>
    </div>
  `,
  styles: `
    .frame {
      display: flex;
      align-items: center;
      gap: 10px;
      height: var(--h-input);
      border: 1px solid var(--line);
      border-radius: var(--r-input);
      padding: 5px;
    }

    button {
      width: 32px;
      height: 32px;
      border: none;
      background: var(--chip);
      border-radius: 8px;
      font-size: 16px;
      cursor: pointer;
      color: var(--ink);
      font-family: inherit;
    }

    .value {
      flex: 1;
      text-align: center;
      font-size: 15px;
      font-weight: 700;
    }
  `,
})
export class StepperComponent {
  readonly value = input.required<number>();
  readonly min = input(0);
  readonly max = input(999);
  readonly label = input<string>();
  readonly valueChange = output<number>();

  step(delta: number): void {
    const next = Math.min(this.max(), Math.max(this.min(), this.value() + delta));
    if (next !== this.value()) {
      this.valueChange.emit(next);
    }
  }
}
