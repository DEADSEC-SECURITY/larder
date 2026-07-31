import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../core/api.service';
import { AppStateService } from '../core/app-state.service';
import { ClockService } from '../core/clock.service';
import { GENERIC_SCOPE_LABEL } from '../core/format';
import { ToastService } from '../core/toast.service';
import { IntakeBatch, IntakeConfirmPayload } from '../core/models';
import {
  TaxRateSelectComponent,
  findTaxRate,
  taxBreakdownHint,
} from '../ui/tax-rate-select';

/**
 * Scanner intake popup: surfaces the oldest open intake batch so the user can
 * fill in store/price/expiration for units the scanner already created. Open
 * batches are a FIFO queue; confirming or keeping defaults advances to the
 * next one. Closing with ✕ only snoozes the batch locally — a new scan on the
 * same product (quantity bump) resurfaces it.
 *
 * When a different product is scanned while a popup is open, the new batch
 * takes over the popup; the one being edited is auto-confirmed first if the
 * user had changed anything, otherwise it stays open in the queue.
 *
 * A popup left untouched for 30 seconds auto-confirms and closes: edits made
 * so far are saved, an unedited batch just keeps its scan-time defaults.
 * Edits and rescans of the shown product reset the countdown.
 */
@Component({
  selector: 'app-intake-popup',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, TaxRateSelectComponent],
  template: `
    @if (current(); as batch) {
      <div class="scrim" (click)="snooze(batch.id)"></div>
      <div class="holder">
        <div class="panel" role="dialog" aria-label="Scanner intake">
          <div class="head">
            <span class="title display">Just scanned</span>
            @if (queueCount() > 1) {
              <span class="queue-chip tnum">{{ queueCount() - 1 }} more waiting</span>
            }
            <button type="button" class="close" (click)="snooze(batch.id)" aria-label="Later">
              ✕
            </button>
          </div>

          <div class="product">
            @if (batch.imageUrl) {
              <img class="thumb" [src]="batch.imageUrl" alt="" />
            } @else {
              <div class="thumb thumb-empty"></div>
            }
            <div class="product-text">
              <div class="product-name">{{ batch.productName || 'Identifying…' }}</div>
              <div class="product-meta">
                @if (batch.brand) {
                  {{ batch.brand }} ·
                }
                <span class="tnum">{{ batch.barcode }}</span>
              </div>
              <div class="product-meta">
                {{ batch.scannerName || batch.scannerCode || 'Scanner' }}
              </div>
              @if (groupLine(); as line) {
                <div class="group-line">{{ line }}</div>
              }
            </div>
            <div class="qty" [class.pulse]="pulsing()">
              <span class="qty-num tnum">{{ batch.quantity }}</span>
              <span class="qty-label">{{ batch.quantity === 1 ? 'item' : 'items' }}</span>
            </div>
          </div>

          <div class="store-row">
            <div>
              <div class="field-label">Store</div>
              <select
                class="select select-block"
                [ngModel]="storeId()"
                (ngModelChange)="touchStore($event)"
              >
                <option [ngValue]="null">— no store —</option>
                @for (store of state.stores(); track store.id) {
                  <option [ngValue]="store.id">{{ store.name }}</option>
                }
              </select>
            </div>
            <div>
              <div class="field-label">Stored in</div>
              <select
                class="select select-block"
                [ngModel]="storageLocationId()"
                (ngModelChange)="touchStorage($event)"
              >
                <option [ngValue]="null">No location</option>
                @for (location of state.activeStorageLocations(); track location.id) {
                  <option [ngValue]="location.id">{{ location.name }}</option>
                }
              </select>
            </div>
          </div>

          <div class="price-row">
            <div>
              <div class="hstack" style="gap: 6px; margin-bottom: 5px">
                <span class="field-label" style="margin-bottom: 0">Price</span>
                <button
                  type="button"
                  class="chip chip-sm mode-chip"
                  [class.active]="!taxIncluded()"
                  (click)="setTaxIncluded(false)"
                >
                  + tax
                </button>
                <button
                  type="button"
                  class="chip chip-sm mode-chip"
                  [class.active]="taxIncluded()"
                  (click)="setTaxIncluded(true)"
                >
                  Tax incl.
                </button>
              </div>
              <input
                class="input input-on-bg"
                [placeholder]="taxIncluded() ? '$0.00 incl. tax' : '$0.00 before tax'"
                [ngModel]="price()"
                (ngModelChange)="touchPrice($event)"
              />
            </div>
            <div>
              <div class="field-label">Tax rate</div>
              <ui-tax-rate-select [value]="taxRateId()" (valueChange)="touchTax($event)" />
            </div>
            <div class="field-hint price-row-hint">
              @if (taxPreview(); as preview) {
                {{ preview }}
              } @else {
                Per unit, applied to every item.
              }
            </div>
          </div>

          <div>
            <div class="field-label">Expiration date</div>
            <div class="hstack" style="gap: 8px">
              <input
                type="date"
                class="input input-on-bg"
                style="flex: 1"
                [disabled]="noExpiration()"
                [ngModel]="expiration()"
                (ngModelChange)="touchExpiration($event)"
              />
              <button
                type="button"
                class="btn btn-outline"
                [class.btn-active]="noExpiration()"
                (click)="toggleNoExpiration()"
              >
                No expiration
              </button>
            </div>
            @if (!expirationTouched && !noExpiration() && batch.expirationKind === 'estimated') {
              <div class="field-hint" style="margin-top: 5px">
                Estimated from the product’s category — set a date to confirm it.
              </div>
            }
          </div>

          <div class="actions">
            @if (idleSeconds(); as secs) {
              <span class="autosave-hint">
                Auto-saves in <span class="tnum">{{ secs }}s</span>
              </span>
            }
            <button type="button" class="btn btn-outline" [disabled]="busy()" (click)="dismiss(batch)">
              Keep defaults
            </button>
            <button type="button" class="btn btn-primary" [disabled]="busy()" (click)="confirm(batch)">
              Save {{ batch.editableQuantity }} {{ batch.editableQuantity === 1 ? 'item' : 'items' }}
            </button>
          </div>
        </div>
      </div>
    } @else if (snoozedCount() > 0) {
      <button type="button" class="pending-chip" (click)="reopen()">
        <span class="pending-dot"></span>
        {{ snoozedCount() }} pending {{ snoozedCount() === 1 ? 'scan' : 'scans' }}
      </button>
    }
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
      width: 460px;
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
      gap: 10px;
    }

    .title {
      font-size: 24px;
    }

    .queue-chip {
      font-size: 12px;
      font-weight: 700;
      color: var(--faint);
      background: var(--bg);
      border-radius: 999px;
      padding: 4px 10px;
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

    .product {
      display: flex;
      align-items: center;
      gap: 14px;
      background: var(--bg);
      border-radius: 14px;
      padding: 14px 16px;
    }

    .thumb {
      width: 56px;
      height: 56px;
      border-radius: 10px;
      object-fit: cover;
      background: var(--card);
      flex-shrink: 0;
    }

    .thumb-empty {
      background: var(--card);
    }

    .product-text {
      flex: 1;
      min-width: 0;
    }

    .product-name {
      font-size: 15px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .product-meta {
      font-size: 12px;
      color: var(--faint);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .group-line {
      font-size: 11.5px;
      color: var(--accent);
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .qty {
      text-align: center;
      flex-shrink: 0;
      min-width: 52px;
    }

    .qty-num {
      display: block;
      font-size: 32px;
      font-weight: 800;
      line-height: 1.1;
    }

    .qty-label {
      font-size: 11px;
      color: var(--faint);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    .qty.pulse .qty-num {
      animation: qty-pulse 0.5s ease;
    }

    @keyframes qty-pulse {
      0% {
        transform: scale(1);
      }
      35% {
        transform: scale(1.35);
      }
      100% {
        transform: scale(1);
      }
    }

    .store-row,
    .price-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .mode-chip {
      padding: 3px 9px;
      font-size: 10.5px;
    }

    .price-row-hint {
      grid-column: 1 / -1;
      margin-top: -3px;
    }

    .btn-active {
      background: var(--ink);
      color: var(--card);
      border-color: var(--ink);
    }

    .actions {
      display: flex;
      align-items: center;
      gap: 10px;
      justify-content: flex-end;
    }

    .autosave-hint {
      margin-right: auto;
      font-size: 12px;
      color: var(--faint);
    }

    .pending-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex: none;
      background: var(--accent);
      animation: lpulse 1.4s infinite;
    }

    .pending-chip {
      position: fixed;
      right: 20px;
      bottom: 20px;
      z-index: 90;
      display: flex;
      align-items: center;
      gap: 8px;
      background: var(--card);
      border: none;
      border-radius: 999px;
      padding: 10px 16px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: var(--shadow-modal);
    }

    @media (max-width: 760px) {
      .pending-chip {
        bottom: 96px;
      }
    }
  `,
})
export class IntakePopup {
  readonly state = inject(AppStateService);
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly clock = inject(ClockService);

  private readonly batches = signal<IntakeBatch[]>([]);
  /** Batch ids the user closed with ✕; cleared when the batch gets rescanned. */
  private readonly snoozed = signal<ReadonlySet<string>>(new Set());
  readonly busy = signal(false);
  readonly pulsing = signal(false);

  /** Batch to show: the latest arrival takes over; otherwise FIFO oldest. */
  private readonly displayId = signal<string | null>(null);

  readonly current = computed(() => {
    const visible = this.batches().filter((b) => !this.snoozed().has(b.id));
    return visible.find((b) => b.id === this.displayId()) ?? visible[0] ?? null;
  });
  readonly queueCount = computed(() => this.batches().length - this.snoozedShown());
  readonly snoozedCount = computed(
    () => this.batches().filter((b) => this.snoozed().has(b.id)).length,
  );

  // Form state for the batch currently shown. Untouched fields keep following
  // the server value (enrichment can fill in a rule-derived store late);
  // touched fields are the ones included in the confirm payload.
  readonly storeId = signal<string | null>(null);
  readonly storageLocationId = signal<string | null>(null);
  readonly price = signal('');
  readonly taxIncluded = signal(false);
  readonly taxRateId = signal<string | null>(null);
  readonly expiration = signal('');
  readonly noExpiration = signal(false);
  storeTouched = false;
  storageTouched = false;
  priceTouched = false;
  taxTouched = false;
  expirationTouched = false;

  /** Live subtotal + tax = total line for the entered price and selected rate. */
  readonly taxPreview = computed(() => {
    const subtotal = parseFloat(this.price().replace('$', '').trim());
    const rate = findTaxRate(this.state.taxLocations(), this.taxRateId());
    if (isNaN(subtotal) || !rate) {
      return '';
    }
    return taxBreakdownHint(subtotal, rate, this.taxIncluded());
  });

  /** Passive membership note ("Counts toward Strawberries · …") — informational
   *  only; a pending review belongs in the settings queue, never a blocking popup. */
  readonly groupLine = signal<string | null>(null);
  private groupLineKey: string | null = null;

  private shownId: string | null = null;
  private shownQuantity = 0;
  private loadedOnce = false;
  private pulseTimer: ReturnType<typeof setTimeout> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    // Initial run + every SSE-driven version bump.
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  private snoozedShown(): number {
    const cur = this.current();
    return this.batches().filter((b) => this.snoozed().has(b.id) && b.id !== cur?.id).length;
  }

  private load(): void {
    this.api.intakeBatches('open').subscribe(({ batches }) => {
      const previous = this.batches();
      const openIds = new Set(batches.map((b) => b.id));
      // Un-snooze anything whose quantity grew (new scan) and drop finalized ids.
      const stillSnoozed = new Set<string>();
      for (const id of this.snoozed()) {
        const batch = batches.find((b) => b.id === id);
        const prev = previous.find((b) => b.id === id);
        if (batch && (!prev || batch.quantity <= prev.quantity)) {
          stillSnoozed.add(id);
        }
      }
      this.snoozed.set(stillSnoozed);
      const displayed = this.current();
      this.batches.set(batches);
      if (this.shownId && !openIds.has(this.shownId)) {
        this.shownId = null;
      }
      // A freshly scanned product takes over the popup. If the user had
      // edited the one on display, save those edits first; an untouched
      // batch just stays open in the queue. Skipped on the very first load
      // so a pre-existing backlog still starts at the oldest batch.
      const prevIds = new Set(previous.map((b) => b.id));
      const arrivals = batches.filter((b) => !prevIds.has(b.id));
      if (this.loadedOnce && arrivals.length) {
        const newest = arrivals[arrivals.length - 1];
        if (displayed && displayed.id !== newest.id && openIds.has(displayed.id)) {
          if (this.anyTouched()) {
            this.autoConfirm(displayed);
          }
        }
        this.displayId.set(newest.id);
      }
      this.loadedOnce = true;
      this.syncForm();
    });
  }

  private anyTouched(): boolean {
    return (
      this.storeTouched || this.storageTouched || this.priceTouched || this.expirationTouched
    );
  }

  /** Confirm a batch the user was editing that got displaced by a new scan. */
  private autoConfirm(batch: IntakeBatch): void {
    const payload = this.buildPayload();
    this.batches.update((list) => list.filter((b) => b.id !== batch.id));
    this.api.confirmIntakeBatch(batch.id, payload).subscribe({
      next: () => {
        this.toasts.show(
          `Saved ${batch.editableQuantity} ${batch.editableQuantity === 1 ? 'item' : 'items'}` +
            (batch.productName ? ` — ${batch.productName}` : ''),
        );
        this.state.changed();
      },
      // Already finalized elsewhere — the refetch above dropped it anyway.
      error: () => {},
    });
  }

  /** Refetch the membership note when the shown product (or its enrichment
   *  state, which can trigger a late auto-match) changes. */
  private syncGroupLine(batch: IntakeBatch): void {
    const key = `${batch.barcode}|${batch.enrichmentStatus ?? ''}`;
    if (key === this.groupLineKey) {
      return;
    }
    if (this.groupLineKey?.split('|')[0] !== batch.barcode) {
      this.groupLine.set(null);
    }
    this.groupLineKey = key;
    this.api.productStockGroupMatches(batch.barcode).subscribe({
      next: (matches) => {
        if (this.current()?.barcode !== batch.barcode) {
          return;
        }
        this.groupLine.set(
          matches.status === 'matched' && matches.stockGroup
            ? `Counts toward ${matches.stockGroup.name} · ${GENERIC_SCOPE_LABEL}`
            : null,
        );
      },
      error: () => this.groupLine.set(null),
    });
  }

  private syncForm(): void {
    const batch = this.current();
    if (!batch) {
      this.shownId = null;
      this.groupLineKey = null;
      this.groupLine.set(null);
      this.clearIdle();
      return;
    }
    this.syncGroupLine(batch);
    if (batch.id !== this.shownId) {
      this.shownId = batch.id;
      this.shownQuantity = batch.quantity;
      this.startIdle();
      this.storeTouched =
        this.storageTouched =
        this.priceTouched =
        this.taxTouched =
        this.expirationTouched =
          false;
      this.noExpiration.set(false);
      this.storeId.set(batch.storeId);
      this.storageLocationId.set(batch.storageLocationId);
      this.price.set(batch.unitPrice != null ? batch.unitPrice.toFixed(2) : '');
      this.taxIncluded.set(false);
      this.taxRateId.set(this.defaultTaxRateId(batch));
      this.expiration.set(batch.expirationDate ?? '');
      return;
    }
    if (batch.quantity > this.shownQuantity) {
      this.pulse();
      // Still scanning this product — hold the popup open.
      this.startIdle();
    }
    this.shownQuantity = batch.quantity;
    // Late enrichment/routing may fill fields the user hasn't edited yet.
    if (!this.storeTouched) {
      this.storeId.set(batch.storeId);
    }
    if (!this.storageTouched) {
      this.storageLocationId.set(batch.storageLocationId);
    }
    if (!this.priceTouched) {
      this.price.set(batch.unitPrice != null ? batch.unitPrice.toFixed(2) : '');
    }
    if (!this.taxTouched) {
      this.taxRateId.set(this.defaultTaxRateId(batch));
    }
    if (!this.expirationTouched && !this.noExpiration()) {
      this.expiration.set(batch.expirationDate ?? '');
    }
  }

  private pulse(): void {
    if (this.pulseTimer) {
      clearTimeout(this.pulseTimer);
    }
    this.pulsing.set(false);
    // Retrigger the CSS animation even if a pulse is still running.
    requestAnimationFrame(() => this.pulsing.set(true));
    this.pulseTimer = setTimeout(() => this.pulsing.set(false), 600);
  }

  private readonly idleDeadline = signal<number | null>(null);

  /** Seconds until the idle auto-save fires, for the countdown hint. */
  readonly idleSeconds = computed(() => {
    const deadline = this.idleDeadline();
    if (deadline == null) {
      return null;
    }
    return Math.max(1, Math.ceil((deadline - this.clock.now().getTime()) / 1000));
  });

  /** (Re)arm the auto-save countdown for the batch on display. */
  private startIdle(): void {
    this.clearIdle();
    const timeoutMs = this.state.intakePopupTimeoutMs();
    if (timeoutMs <= 0) {
      return;
    }
    this.idleDeadline.set(Date.now() + timeoutMs);
    this.idleTimer = setTimeout(() => {
      const batch = this.current();
      if (batch && !this.busy()) {
        this.confirm(batch);
      }
    }, timeoutMs);
  }

  private clearIdle(): void {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    this.idleDeadline.set(null);
  }

  touchStore(value: string | null): void {
    this.storeId.set(value);
    this.storeTouched = true;
    this.startIdle();
  }

  touchStorage(value: string | null): void {
    this.storageLocationId.set(value);
    this.storageTouched = true;
    this.startIdle();
  }

  touchPrice(value: string): void {
    this.price.set(value);
    this.priceTouched = true;
    this.startIdle();
  }

  setTaxIncluded(value: boolean): void {
    if (this.taxIncluded() !== value) {
      this.taxIncluded.set(value);
      // Changes what the entered price means, so it rides with the price field.
      this.priceTouched = true;
      this.startIdle();
    }
  }

  touchTax(value: string | null): void {
    this.taxRateId.set(value);
    this.taxTouched = true;
    this.startIdle();
  }

  /** Selector state for an unconfirmed batch: recorded rate, else household default. */
  private defaultTaxRateId(batch: IntakeBatch): string | null {
    return batch.taxRateId ?? this.state.settings()?.defaultTaxRateId ?? null;
  }

  touchExpiration(value: string): void {
    this.expiration.set(value);
    this.expirationTouched = true;
    this.startIdle();
  }

  toggleNoExpiration(): void {
    this.noExpiration.update((v) => !v);
    this.expirationTouched = true;
    this.startIdle();
  }

  /** Only touched fields are sent: omitted = keep default, null = clear. */
  private buildPayload(): IntakeConfirmPayload {
    const payload: IntakeConfirmPayload = {};
    if (this.storeTouched) {
      payload.storeId = this.storeId();
    }
    if (this.storageTouched) {
      payload.storageLocationId = this.storageLocationId();
    }
    if (this.priceTouched) {
      const parsed = parseFloat(this.price().replace('$', '').trim());
      payload.unitPrice = isNaN(parsed) ? null : parsed;
    }
    if (this.taxTouched) {
      payload.taxRateId = this.taxRateId();
      // The API requires unitPrice alongside taxRateId.
      if (payload.unitPrice === undefined) {
        const parsed = parseFloat(this.price().replace('$', '').trim());
        payload.unitPrice = isNaN(parsed) ? null : parsed;
      }
    }
    if (payload.unitPrice != null) {
      payload.priceIncludesTax = this.taxIncluded();
    }
    if (this.noExpiration()) {
      payload.expirationDate = null;
    } else if (this.expirationTouched && this.expiration()) {
      payload.expirationDate = this.expiration();
    }
    return payload;
  }

  confirm(batch: IntakeBatch): void {
    this.clearIdle();
    const payload = this.buildPayload();
    this.busy.set(true);
    this.api.confirmIntakeBatch(batch.id, payload).subscribe({
      next: () => {
        this.toasts.show(
          `Saved ${batch.editableQuantity} ${batch.editableQuantity === 1 ? 'item' : 'items'}`,
        );
        this.finish(batch.id);
      },
      // 409 = already finalized elsewhere; either way drop it and move on.
      error: () => this.finish(batch.id),
    });
  }

  dismiss(batch: IntakeBatch): void {
    this.clearIdle();
    this.busy.set(true);
    this.api.dismissIntakeBatch(batch.id).subscribe({
      next: () => this.finish(batch.id),
      error: () => this.finish(batch.id),
    });
  }

  snooze(id: string): void {
    this.snoozed.update((set) => new Set(set).add(id));
    this.displayId.set(null);
    this.syncForm();
  }

  reopen(): void {
    this.snoozed.set(new Set());
    this.syncForm();
  }

  private finish(id: string): void {
    this.busy.set(false);
    this.batches.update((list) => list.filter((b) => b.id !== id));
    this.shownId = null;
    this.displayId.set(null);
    this.syncForm();
    this.state.changed();
  }
}
