import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'ui-empty',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div class="empty">{{ message() }}</div>`,
  styles: `
    .empty {
      padding: 36px;
      text-align: center;
      color: var(--faint);
      font-size: 13.5px;
    }
  `,
})
export class EmptyComponent {
  readonly message = input.required<string>();
}
