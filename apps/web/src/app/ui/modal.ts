import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

@Component({
  selector: 'ui-modal',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="scrim" (click)="closed.emit()"></div>
    <div class="holder">
      <div class="panel" [style.width.px]="width()" role="dialog" [attr.aria-label]="title()">
        <div class="head">
          <span class="title display">{{ title() }}</span>
          <button type="button" class="close" (click)="closed.emit()" aria-label="Close">
            ✕
          </button>
        </div>
        <ng-content />
      </div>
    </div>
  `,
  styles: `
    .scrim {
      position: fixed;
      inset: 0;
      background: var(--scrim);
      z-index: 100;
    }

    .holder {
      position: fixed;
      inset: 0;
      z-index: 101;
      display: flex;
      align-items: center;
      justify-content: center;
      pointer-events: none;
      padding: 16px;
    }

    .panel {
      pointer-events: auto;
      max-width: 100%;
      max-height: 90vh;
      overflow: auto;
      background: var(--card);
      border-radius: var(--r-modal);
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      box-shadow: var(--shadow-modal);
    }

    .head {
      display: flex;
      align-items: center;
    }

    .title {
      font-size: 24px;
    }

    .close {
      margin-left: auto;
      cursor: pointer;
      font-size: 20px;
      color: var(--faint);
      padding: 4px 10px;
      background: none;
      border: none;
    }
  `,
})
export class ModalComponent {
  readonly title = input.required<string>();
  readonly width = input(440);
  readonly closed = output<void>();
}
