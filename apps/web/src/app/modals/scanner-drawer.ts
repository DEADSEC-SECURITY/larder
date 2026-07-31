import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';

import { ApiService } from '../core/api.service';
import { AppStateService } from '../core/app-state.service';
import { ClockService } from '../core/clock.service';
import { countdown, relativeTime } from '../core/format';
import { ScannerMode, ScannerRow } from '../core/models';
import { ToastService } from '../core/toast.service';

const MODE_HINTS: Record<string, string> = {
  ADD: 'Each scan adds one unit to stock at the active store.',
  LIST: 'Each scan adds one to the shopping list without changing inventory.',
  REMOVE: 'Each scan removes one unit.',
  TRASH: 'Each scan marks one unit as wasted.',
  IDLE: 'Scans are ignored until you set a mode.',
};

@Component({
  selector: 'app-scanner-drawer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="scrim" (click)="closed.emit()"></div>
    <aside class="drawer">
      <div class="head">
        <span class="display" style="font-size: 23px">Scanners</span>
        <button type="button" class="close" (click)="closed.emit()">✕</button>
      </div>
      <div class="tabs">
        @for (scanner of scanners(); track scanner.id; let index = $index) {
          <button
            type="button"
            class="chip"
            [class.active]="tab() === index"
            (click)="tab.set(index)"
          >
            <span
              class="tab-dot"
              [style.background]="scanner.online ? 'var(--accent)' : 'var(--danger)'"
            ></span>
            {{ scanner.name }}
          </button>
        } @empty {
          <span class="sub" style="font-size: 13px">No scanners registered yet.</span>
        }
      </div>
      <div class="body stack">
        @if (current(); as scanner) {
          @if (!scanner.online && scanner.lastSeenAt) {
            <div class="offline">
              <div class="offline-title">Offline</div>
              <div class="offline-body">
                “{{ scanner.name }}” hasn’t reported since
                {{ relative(scanner.lastSeenAt) }} ago. Check that it is powered and in
                range, then wake it with any scan.
              </div>
            </div>
          } @else {
            <div class="banner" [class]="'banner-' + bannerTone()">
              <div class="banner-row">
                <span class="banner-mode">{{ modeLabel() }}</span>
                <span class="banner-count tnum">{{ modeCountdown() }}</span>
              </div>
              <div class="banner-bar">
                <div class="banner-fill" [style.width.%]="modePercent()"></div>
              </div>
              <div class="banner-hint">{{ hint() }}</div>
            </div>
          }
          <div class="facts">
            <div class="fact">
              <span class="fact-label">Connection</span>
              <span
                class="fact-value"
                [style.color]="scanner.online ? 'var(--accent)' : 'var(--danger)'"
                >● {{ scanner.online ? 'Online' : 'Offline' }}</span
              >
            </div>
            <div class="fact">
              <span class="fact-label">Active store</span>
              <span class="fact-value">
                {{ scanner.storeName || '—' }}
                @if (scanner.storeExpiresAt) {
                  <span class="faint" style="font-weight: 400">
                    · resets in {{ storeCountdown() }}</span
                  >
                }
              </span>
            </div>
            <div class="fact">
              <span class="fact-label">Last scan</span>
              <span class="fact-value">{{ lastScanLabel() }}</span>
            </div>
          </div>
          <div>
            <div class="field-label" style="margin-bottom: 7px">Set mode</div>
            <div class="hstack" style="gap: 7px">
              @for (mode of modes; track mode) {
                <button
                  type="button"
                  class="chip"
                  [class.active]="scanner.mode === mode"
                  (click)="setMode(scanner, mode)"
                >
                  {{ mode === 'LIST' ? 'Add To List' : mode[0] + mode.slice(1).toLowerCase() }}
                </button>
              }
              <button
                type="button"
                class="btn btn-outline btn-sm spacer"
                (click)="setMode(scanner, null)"
              >
                Clear mode
              </button>
            </div>
          </div>
          <div class="feed">
            <div class="hstack" style="gap: 7px; margin-bottom: 9px">
              <span class="pulse-dot"></span>
              <span style="font-size: 13px; font-weight: 600">Recent scans</span>
            </div>
            <div class="stack" style="gap: 7px">
              @for (row of scanner.feed; track row.at) {
                <div class="feed-row">
                  <span class="feed-rel tnum">{{ relative(row.at) }}</span>
                  <span
                    class="feed-tag"
                    [style.color]="row.ok ? 'var(--accent)' : 'var(--faint)'"
                    >{{ row.tag }}</span
                  >
                  <span class="feed-label">{{ row.label }}</span>
                </div>
              } @empty {
                <span class="faint" style="font-size: 12.5px">No scans yet.</span>
              }
            </div>
          </div>
          <div class="diag">
            <div class="field-label" style="margin-bottom: 8px">Diagnostics</div>
            <div class="diag-grid">
              <span>Code <b>{{ scanner.code }}</b></span>
              <span>Op. timeout <b>{{ scanner.modeTimeoutSeconds }} s</b></span>
              <span>Context timeout <b>{{ storeTimeoutLabel() }}</b></span>
              <span>Enabled <b>{{ scanner.enabled ? 'yes' : 'no' }}</b></span>
            </div>
          </div>
        }
      </div>
    </aside>
  `,
  styles: `
    .scrim {
      position: fixed;
      inset: 0;
      background: var(--scrim);
      z-index: 110;
    }

    .drawer {
      position: fixed;
      top: 0;
      right: 0;
      bottom: 0;
      z-index: 111;
      width: min(430px, 100vw);
      background: var(--card);
      box-shadow: var(--shadow-drawer);
      display: flex;
      flex-direction: column;
    }

    .head {
      display: flex;
      align-items: center;
      padding: 18px 20px 12px;
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

    .tabs {
      display: flex;
      gap: 6px;
      padding: 0 20px 14px;
      border-bottom: 1px solid var(--line);
    }

    .tab-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      display: inline-block;
    }

    .body {
      flex: 1;
      overflow: auto;
      padding: 18px 20px;
    }

    .offline {
      background: var(--danger-bg);
      border-radius: 14px;
      padding: 18px;
    }

    .offline-title {
      font-size: 16px;
      font-weight: 700;
      color: var(--danger);
    }

    .offline-body {
      font-size: 13px;
      margin-top: 6px;
      line-height: 1.5;
    }

    .banner {
      border-radius: 14px;
      padding: 16px 18px;
    }

    .banner-add {
      background: var(--accent-bg);
      color: var(--accent);
    }

    .banner-remove {
      background: var(--warn-bg);
      color: var(--warn);
    }

    .banner-trash {
      background: var(--danger-bg);
      color: var(--danger);
    }

    .banner-idle {
      background: var(--chip);
      color: var(--sub);
    }

    .banner-row {
      display: flex;
      align-items: baseline;
    }

    .banner-mode {
      font-size: 19px;
      font-weight: 700;
    }

    .banner-count {
      margin-left: auto;
      font-size: 15px;
      font-weight: 600;
    }

    .banner-bar {
      height: 5px;
      background: rgba(0, 0, 0, 0.08);
      border-radius: 3px;
      overflow: hidden;
      margin: 10px 0 8px;
    }

    .banner-fill {
      height: 100%;
      background: currentColor;
      border-radius: 3px;
      transition: width 1s linear;
    }

    .banner-hint {
      font-size: 12.5px;
      opacity: 0.85;
    }

    .facts {
      display: flex;
      flex-direction: column;
      gap: 8px;
      font-size: 13px;
    }

    .fact {
      display: flex;
    }

    .fact-label {
      color: var(--sub);
      width: 120px;
      flex: none;
    }

    .fact-value {
      font-weight: 600;
    }

    .feed {
      background: var(--bg);
      border-radius: 14px;
      padding: 14px 16px;
    }

    .feed-row {
      display: flex;
      gap: 8px;
      align-items: baseline;
      font-size: 12.5px;
    }

    .feed-rel {
      color: var(--faint);
      width: 34px;
      flex: none;
    }

    .feed-tag {
      font-weight: 700;
      font-size: 10.5px;
      letter-spacing: 0.05em;
      width: 30px;
      flex: none;
    }

    .feed-label {
      flex: 1;
    }

    .diag {
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }

    .diag-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      font-size: 12px;
      color: var(--sub);

      b {
        color: var(--ink);
      }
    }
  `,
})
export class ScannerDrawer {
  readonly scanners = input.required<ScannerRow[]>();
  readonly closed = output<void>();
  readonly changed = output<void>();

  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly clock = inject(ClockService);
  readonly state = inject(AppStateService);

  readonly tab = signal(0);
  readonly modes: ScannerMode[] = ['ADD', 'LIST', 'REMOVE', 'TRASH'];

  readonly current = computed(() => {
    const list = this.scanners();
    return list[Math.min(this.tab(), Math.max(0, list.length - 1))] ?? null;
  });

  readonly modeLabel = computed(() => {
    const scanner = this.current();
    if (!scanner?.mode) {
      return 'Idle';
    }
    if (scanner.mode === 'LIST') {
      return 'Add To List mode';
    }
    return `${scanner.mode[0]}${scanner.mode.slice(1).toLowerCase()} mode`;
  });

  readonly bannerTone = computed(() => (this.current()?.mode ?? 'idle').toLowerCase());
  readonly hint = computed(() => MODE_HINTS[this.current()?.mode ?? 'IDLE']);

  readonly modeCountdown = computed(() => {
    const scanner = this.current();
    if (!scanner?.mode || !scanner.modeExpiresAt) {
      return '';
    }
    return countdown(scanner.modeExpiresAt, this.clock.now());
  });

  readonly modePercent = computed(() => {
    const scanner = this.current();
    if (!scanner?.mode || !scanner.modeExpiresAt) {
      return 0;
    }
    const remaining =
      (new Date(scanner.modeExpiresAt).getTime() - this.clock.now().getTime()) / 1000;
    return Math.max(0, Math.min(100, (remaining / scanner.modeTimeoutSeconds) * 100));
  });

  readonly storeCountdown = computed(() => {
    const scanner = this.current();
    return scanner?.storeExpiresAt ? countdown(scanner.storeExpiresAt, this.clock.now()) : '—';
  });

  readonly lastScanLabel = computed(() => {
    const feed = this.current()?.feed ?? [];
    if (!feed.length) {
      return '—';
    }
    return `${feed[0].label} · ${relativeTime(feed[0].at, this.clock.now())} ago`;
  });

  readonly storeTimeoutLabel = computed(() => {
    const seconds = this.current()?.storeTimeoutSeconds ?? 0;
    return seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${seconds} s`;
  });

  relative(iso: string): string {
    return relativeTime(iso, this.clock.now());
  }

  setMode(scanner: ScannerRow, mode: ScannerMode | null): void {
    this.api.setScannerMode(scanner.id, mode).subscribe(() => {
      this.toasts.show(mode ? `${scanner.name} → ${mode}` : 'Mode cleared');
      this.changed.emit();
    });
  }
}
