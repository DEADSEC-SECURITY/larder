import {
  ChangeDetectionStrategy,
  Component,
  effect,
  inject,
  signal,
} from '@angular/core';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { CommandRow } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { EmptyComponent } from '../../ui/empty';
import { UI_LIST } from '../../ui/list';

// Scanner command barcodes (control_barcodes): mode commands plus one
// SET_STORE command per store. Printing queues a job for the kitchen printer.
@Component({
  selector: 'app-commands',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EmptyComponent, UI_LIST],
  template: `
    <div class="stack">
      <div class="hstack wrap" style="align-items: baseline; gap: 12px">
        <span class="page-title">Commands</span>
        <span class="sub" style="font-size: 12.5px"
          >{{ commands().length }} command barcodes</span
        >
      </div>

      <div class="callout">
        Scan a command barcode to control a scanner hands-free: set the mode
        (add / add to list / remove / trash), set the active store before scanning
        groceries, or set where scanned items get stored. Print the ones you use and
        keep them near the scanner.
      </div>

      <ui-list>
        @for (command of commands(); track command.payload) {
          <ui-row [hover]="false" [class.disabled]="rowDisabled(command)">
            <ui-row-main>
              <div class="row-title">
                {{ command.description }}
                @if (pill(command); as p) {
                  <span class="pill pill-sm" [class]="'pill pill-sm ' + p.cls">{{
                    p.label
                  }}</span>
                }
                @if (!command.enabled) {
                  <span class="pill pill-sm pill-faint">Disabled</span>
                } @else if (command.storeActive === false) {
                  <span class="pill pill-sm pill-warn">Store inactive</span>
                } @else if (command.storageLocationActive === false) {
                  <span class="pill pill-sm pill-warn">Location archived</span>
                }
              </div>
              <div class="row-meta cmd-payload tnum">{{ command.payload }}</div>
            </ui-row-main>
            <ui-rail [width]="96">
              <button
                type="button"
                class="btn btn-outline btn-sm"
                [disabled]="rowDisabled(command)"
                (click)="print(command)"
              >
                Print
              </button>
            </ui-rail>
          </ui-row>
        } @empty {
          <ui-empty message="No command barcodes found." />
        }
      </ui-list>
    </div>
  `,
  styles: `
    .cmd-payload {
      letter-spacing: 0.08em;
      font-size: 11.5px;
    }

    ui-row.disabled ui-row-main {
      opacity: 0.5;
    }
  `,
})
export class CommandsScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly state = inject(AppStateService);

  readonly commands = signal<CommandRow[]>([]);

  constructor() {
    effect(() => {
      this.state.version();
      this.api.commands().subscribe(({ commands }) => this.commands.set(commands));
    });
  }

  rowDisabled(command: CommandRow): boolean {
    return (
      !command.enabled ||
      command.storeActive === false ||
      command.storageLocationActive === false
    );
  }

  pill(command: CommandRow): { label: string; cls: string } | null {
    switch (command.commandType) {
      case 'SET_MODE':
        switch (command.mode) {
          case 'ADD':
            return { label: 'Mode', cls: 'pill-ok' };
          case 'LIST':
            return { label: 'Shopping', cls: 'pill-ok' };
          case 'REMOVE':
            return { label: 'Mode', cls: 'pill-warn' };
          case 'TRASH':
            return { label: 'Mode', cls: 'pill-danger' };
          default:
            return { label: 'Mode', cls: 'pill-chip' };
        }
      case 'CLEAR_MODE':
        return { label: 'Mode', cls: 'pill-chip' };
      case 'SET_STORE':
      case 'CLEAR_STORE':
        return { label: 'Store', cls: 'pill-chip' };
      case 'SET_LOCATION':
      case 'CLEAR_LOCATION':
        return { label: 'Location', cls: 'pill-chip' };
    }
  }

  print(command: CommandRow): void {
    this.api
      .queuePrintJob({
        kind: 'control',
        title: command.description,
        value: command.payload,
      })
      .subscribe(() => {
        this.toasts.show(`“${command.description}” queued for the kitchen printer`);
      });
  }
}
