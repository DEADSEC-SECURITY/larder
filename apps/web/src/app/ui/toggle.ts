import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

@Component({
  selector: 'ui-toggle',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button
      type="button"
      class="track"
      [class.on]="on()"
      role="switch"
      [attr.aria-checked]="on()"
      (click)="toggled.emit(!on())"
    >
      <span class="knob"></span>
    </button>
  `,
  styles: `
    .track {
      width: 42px;
      height: 25px;
      border-radius: 99px;
      background: var(--chip);
      padding: 3px;
      border: none;
      cursor: pointer;
      transition: background 0.15s;
      display: inline-flex;
      flex: none;
    }

    .track.on {
      background: var(--accent);
    }

    .knob {
      width: 19px;
      height: 19px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
      transition: transform 0.15s;
    }

    .track.on .knob {
      transform: translateX(17px);
    }
  `,
})
export class ToggleComponent {
  readonly on = input.required<boolean>();
  readonly toggled = output<boolean>();
}
