import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { LayoutService } from '../core/layout.service';
import { ToastService } from '../core/toast.service';

@Component({
  selector: 'ui-toast-stack',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stack-holder" [style.bottom]="layout.isMobile() ? '92px' : '24px'">
      @for (toast of toasts.toasts(); track toast.id) {
        <div class="toast" [class.has-actions]="toast.actions?.length">
          <span>{{ toast.message }}</span>
          @for (action of toast.actions; track action.label) {
            <button type="button" class="toast-action" (click)="toasts.act(toast, action)">
              {{ action.label }}
            </button>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .stack-holder {
      position: fixed;
      left: 50%;
      transform: translateX(-50%);
      z-index: 200;
      display: flex;
      flex-direction: column;
      gap: 8px;
      align-items: center;
      pointer-events: none;
    }

    .toast {
      background: var(--ink);
      color: var(--bg);
      font-size: 13px;
      font-weight: 600;
      border-radius: 99px;
      padding: 11px 20px;
      box-shadow: 0 8px 24px rgba(20, 17, 12, 0.25);
      animation: ltoast 0.2s ease;
      white-space: nowrap;
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .toast.has-actions {
      pointer-events: auto;
    }

    .toast-action {
      background: none;
      border: none;
      padding: 0;
      color: var(--accent);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
    }

    .toast-action:hover {
      text-decoration: underline;
    }
  `,
})
export class ToastStackComponent {
  readonly toasts = inject(ToastService);
  readonly layout = inject(LayoutService);
}
