import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { IntegrationRow } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { taxPercentLabel } from '../../ui/tax-rate-select';

const DESCRIPTIONS: Record<string, string> = {
  ziptax:
    'Looks up sales-tax rates by street address for tax locations. Product-specific ' +
    'rates (groceries, candy, soft drinks…) need a ZipTax Pro or Enterprise account; ' +
    'the basic plan covers general merchandise only.',
};

@Component({
  selector: 'app-integrations',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  template: `
    <div class="stack">
      <span class="page-title">Integrations</span>

      <div class="callout">
        Keys are stored server-side and never sent back to the browser in full. A key
        saved here overrides the matching server environment variable; clearing it
        falls back to the environment.
      </div>

      @for (row of integrations(); track row.provider) {
        <div class="card stack" style="gap: 12px">
          <div class="hstack" style="gap: 10px">
            <span style="font-size: 17px; font-weight: 700">{{ row.label }}</span>
            @if (row.configured) {
              <span class="status ok">
                Configured · {{ row.source === 'env' ? 'environment' : 'saved key' }}
                @if (row.keyMask) {
                  <span class="tnum">{{ row.keyMask }}</span>
                }
              </span>
            } @else {
              <span class="status">Not configured</span>
            }
          </div>

          <div class="sub" style="font-size: 13px">{{ description(row.provider) }}</div>
          @if (row.baseUrl) {
            <div class="faint" style="font-size: 11.5px">Endpoint: {{ row.baseUrl }}</div>
          }

          <div class="hstack wrap" style="gap: 8px">
            <input
              class="input"
              style="flex: 1; min-width: 220px"
              type="password"
              autocomplete="off"
              [placeholder]="row.configured ? 'Paste a new API key to replace…' : 'Paste your API key…'"
              [ngModel]="draft(row.provider)"
              (ngModelChange)="setDraft(row.provider, $event)"
              (keyup.enter)="save(row)"
            />
            <button
              type="button"
              class="btn btn-primary"
              [disabled]="!draft(row.provider).trim() || busy()"
              (click)="save(row)"
            >
              Save key
            </button>
            @if (row.source === 'db') {
              <button type="button" class="btn btn-outline" [disabled]="busy()" (click)="clear(row)">
                Clear saved key
              </button>
            }
            @if (row.provider === 'ziptax' && row.configured) {
              <button type="button" class="btn btn-outline" [disabled]="busy()" (click)="test()">
                Test lookup
              </button>
            }
          </div>

          @if (row.provider === 'ziptax' && testResult()) {
            <div class="field-hint">{{ testResult() }}</div>
          }
        </div>
      } @empty {
        <div class="card faint" style="text-align: center">Loading…</div>
      }
    </div>
  `,
  styles: `
    .status {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      background: var(--chip);
      border-radius: 999px;
      padding: 4px 10px;

      &.ok {
        background: var(--accent-bg);
        color: var(--accent);
      }

      .tnum {
        margin-left: 5px;
        text-transform: none;
        letter-spacing: 0;
      }
    }
  `,
})
export class IntegrationsScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);

  readonly integrations = signal<IntegrationRow[]>([]);
  readonly busy = signal(false);
  readonly testResult = signal('');
  private readonly drafts = signal<Record<string, string>>({});

  constructor() {
    this.load();
  }

  load(): void {
    this.api.integrations().subscribe(({ integrations }) => this.integrations.set(integrations));
  }

  description(provider: string): string {
    return DESCRIPTIONS[provider] ?? '';
  }

  draft(provider: string): string {
    return this.drafts()[provider] ?? '';
  }

  setDraft(provider: string, value: string): void {
    this.drafts.update((d) => ({ ...d, [provider]: value }));
  }

  save(row: IntegrationRow): void {
    const key = this.draft(row.provider).trim();
    if (!key) {
      return;
    }
    this.busy.set(true);
    this.api.setIntegrationKey(row.provider, key).subscribe({
      next: () => {
        this.busy.set(false);
        this.setDraft(row.provider, '');
        this.testResult.set('');
        this.toasts.show(`${row.label} key saved`);
        this.load();
      },
      error: (err) => {
        this.busy.set(false);
        this.toasts.show(err?.error?.detail ?? 'Could not save the key');
      },
    });
  }

  clear(row: IntegrationRow): void {
    this.busy.set(true);
    this.api.setIntegrationKey(row.provider, null).subscribe({
      next: () => {
        this.busy.set(false);
        this.testResult.set('');
        this.toasts.show(`${row.label} saved key cleared`);
        this.load();
      },
      error: () => {
        this.busy.set(false);
        this.toasts.show('Could not clear the key');
      },
    });
  }

  test(): void {
    this.busy.set(true);
    this.testResult.set('');
    this.api.testZiptax().subscribe({
      next: (result) => {
        this.busy.set(false);
        this.testResult.set(
          `Lookup OK — ${result.normalizedAddress ?? result.address} · general rate ${taxPercentLabel(result.percentage)}`,
        );
      },
      error: (err) => {
        this.busy.set(false);
        this.testResult.set(`Lookup failed: ${err?.error?.detail ?? 'unknown error'}`);
      },
    });
  }
}
