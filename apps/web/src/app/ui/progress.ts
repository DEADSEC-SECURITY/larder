import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'ui-progress',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="track" [style.height.px]="thickness()">
      <div
        class="fill"
        [style.width.%]="percent()"
        [style.background]="color() || 'var(--accent)'"
      ></div>
    </div>
  `,
  styles: `
    .track {
      background: var(--chip);
      border-radius: 4px;
      overflow: hidden;
    }

    .fill {
      height: 100%;
      border-radius: 4px;
      transition: width 0.25s;
    }
  `,
})
export class ProgressComponent {
  readonly percent = input.required<number>();
  readonly thickness = input(6);
  readonly color = input<string>();
}
