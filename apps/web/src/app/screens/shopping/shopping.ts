import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import {
  GENERIC_SCOPE_LABEL,
  assignmentSourceLabel,
  money,
  productKindLabel,
} from '../../core/format';
import { GroceryItem, GroceryResponse } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { ShoppingAddModal } from '../../modals/shopping-add';
import { ShoppingItemEditModal } from '../../modals/shopping-item-edit';
import { BarcodeScannerComponent } from '../../ui/barcode-scanner';
import { UI_LIST } from '../../ui/list';

/** Hold this long to open the row editor on touch. */
const LONG_PRESS_MS = 450;
/** Finger drift beyond this cancels the long press — it was a scroll. */
const LONG_PRESS_SLOP_PX = 12;

interface ShoppingGroup {
  store: string;
  subtotal: number;
  items: GroceryItem[];
}

@Component({
  selector: 'app-shopping',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, BarcodeScannerComponent, ShoppingAddModal, ShoppingItemEditModal, UI_LIST],
  templateUrl: './shopping.html',
  styleUrl: './shopping.scss',
})
export class ShoppingScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  readonly state = inject(AppStateService);

  readonly data = signal<GroceryResponse | null>(null);
  readonly grouped = signal(true);
  readonly shopMode = signal(false);
  readonly addOpen = signal(false);
  readonly scanOpen = signal(false);
  readonly editing = signal<GroceryItem | null>(null);
  readonly checkingOut = signal(false);
  /** One store for the whole trip, or 'auto' to keep each row's own. */
  readonly tripStoreId = signal('auto');

  private pressTimer: ReturnType<typeof setTimeout> | null = null;
  private pressOrigin: { x: number; y: number } | null = null;
  /** Set when a long press fires so the ensuing click does not also toggle. */
  private suppressClick = false;

  readonly checkedCount = computed(
    () => (this.data()?.items ?? []).filter((i) => i.checked).length,
  );
  readonly totalCount = computed(() => this.data()?.items.length ?? 0);
  readonly progressPct = computed(() =>
    this.totalUnits() ? Math.round((this.cartUnits() / this.totalUnits()) * 100) : 0,
  );

  readonly groups = computed<ShoppingGroup[]>(() => {
    const items = this.data()?.items ?? [];
    if (!this.grouped()) {
      return [{ store: 'All items', subtotal: this.subtotal(items), items }];
    }
    const map = new Map<string, GroceryItem[]>();
    for (const item of items) {
      const key = item.storeName ?? 'Any store';
      map.set(key, [...(map.get(key) ?? []), item]);
    }
    return [...map.entries()].map(([store, groupItems]) => ({
      store,
      subtotal: this.subtotal(groupItems),
      items: groupItems,
    }));
  });

  constructor() {
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api.shopping().subscribe((data) => this.data.set(data));
  }

  private subtotal(items: GroceryItem[]): number {
    return items
      .filter((i) => !i.checked)
      .reduce((sum, i) => sum + (i.unitPrice ?? 0) * i.quantity, 0);
  }

  toggle(item: GroceryItem): void {
    if (this.suppressClick) {
      this.suppressClick = false;
      return;
    }
    this.api
      .updateShoppingItem(item.barcode, { checked: !item.checked })
      .subscribe(() => this.refresh());
  }

  // ---- Row editor: long-press on touch, right-click on desktop ----

  openEditor(item: GroceryItem, event?: Event): void {
    event?.preventDefault();
    this.cancelPress();
    this.editing.set(item);
  }

  onPressStart(item: GroceryItem, event: PointerEvent): void {
    // Mouse users get the context menu instead; this is the touch path.
    if (event.pointerType === 'mouse') {
      return;
    }
    this.pressOrigin = { x: event.clientX, y: event.clientY };
    this.pressTimer = setTimeout(() => {
      this.suppressClick = true;
      navigator.vibrate?.(30);
      this.openEditor(item);
    }, LONG_PRESS_MS);
  }

  onPressMove(event: PointerEvent): void {
    if (!this.pressOrigin) {
      return;
    }
    const drifted =
      Math.abs(event.clientX - this.pressOrigin.x) > LONG_PRESS_SLOP_PX ||
      Math.abs(event.clientY - this.pressOrigin.y) > LONG_PRESS_SLOP_PX;
    if (drifted) {
      this.cancelPress();
    }
  }

  cancelPress(): void {
    if (this.pressTimer) {
      clearTimeout(this.pressTimer);
      this.pressTimer = null;
    }
    this.pressOrigin = null;
  }

  // ---- Scanning ----

  onScanned(barcode: string): void {
    this.scanOpen.set(false);
    this.api.scanShoppingItem(barcode).subscribe({
      next: (result) => {
        const label = result.name ?? result.barcode;
        if (result.added) {
          this.toasts.show(`Added ${label} to the list and your cart`);
        } else if (result.checked) {
          this.toasts.show(`${label} — all ${result.quantity} in the cart`);
        } else {
          this.toasts.show(`${label} — ${result.checkedQuantity} of ${result.quantity}`);
        }
        this.refresh();
      },
      error: (error: unknown) => {
        const code = (error as { error?: { detail?: { code?: string } } })?.error?.detail?.code;
        this.toasts.show(
          code === 'GTIN_NOT_DECIMAL' || code === 'GTIN_BAD_LENGTH'
            ? 'That barcode was not readable'
            : 'Could not match that barcode',
        );
      },
    });
  }

  // ---- Checkout ----

  checkout(): void {
    if (this.checkingOut()) {
      return;
    }
    this.checkingOut.set(true);
    const store = this.tripStoreId();
    this.api.checkoutShopping(store === 'auto' ? null : store).subscribe({
      next: (result) => {
        this.checkingOut.set(false);
        if (!result.added && !result.skipped.length) {
          this.toasts.show('Nothing in the cart yet');
          return;
        }
        const unit = result.added === 1 ? 'item' : 'items';
        if (result.skipped.length) {
          // Skipped rows stay on the list, so say what to fix rather than
          // implying the trip is done.
          const needsStore = result.skipped.some((s) => s.reason.includes('store'));
          this.toasts.show(
            needsStore
              ? `Stocked ${result.added} ${unit} · ${result.skipped.length} still need a store — pick a trip store above`
              : `Stocked ${result.added} ${unit} · ${result.skipped.length} could not be stocked`,
          );
          this.refresh();
          return;
        }
        this.toasts.show(`Stocked ${result.added} ${unit} — trip done`);
        this.shopMode.set(false);
        this.refresh();
      },
      error: () => {
        this.checkingOut.set(false);
        this.toasts.show('Checkout failed — nothing was stocked');
      },
    });
  }

  provenance(item: GroceryItem): string {
    return assignmentSourceLabel(item.storeAssignment.source, item.storeAssignment.reason);
  }

  kindTag(item: GroceryItem): string {
    return item.productKind === 'food' ? '' : productKindLabel(item.productKind);
  }

  /** Group rows carry the generic scope instead of a brand line. */
  scopeMeta(item: GroceryItem): string {
    if (item.itemType !== 'stock_group') {
      return '';
    }
    const parts = [item.scopeLabel || GENERIC_SCOPE_LABEL];
    if (item.memberCount != null) {
      parts.push(`${item.memberCount} accepted product${item.memberCount === 1 ? '' : 's'}`);
    }
    if (item.inStock != null) {
      parts.push(`${item.inStock} in stock`);
    }
    return parts.join(' · ');
  }

  /** Value driving the per-item override select: a store id or 'auto'. */
  storeChoice(item: GroceryItem): string {
    return item.storeAssignment.source === 'manual' && item.storeAssignment.storeId
      ? item.storeAssignment.storeId
      : 'auto';
  }

  setStore(item: GroceryItem, choice: string): void {
    this.api
      .updateShoppingItem(item.barcode, {
        preferredStoreId: choice === 'auto' ? null : choice,
      })
      .subscribe(() => {
        this.toasts.show(choice === 'auto' ? 'Back to automatic assignment' : 'Store set manually');
        this.refresh();
      });
  }

  clearChecks(): void {
    this.api.clearShoppingChecks().subscribe((result) => {
      const noun = result.removed === 1 ? 'item' : 'items';
      this.toasts.show(
        result.removed ? `Removed ${result.removed} checked ${noun}` : 'No checked items to clear',
      );
      this.refresh();
    });
  }

  finishShopping(): void {
    this.shopMode.set(false);
  }

  /** Progress counts individual units, so a ×3 row moves the bar three times. */
  readonly cartUnits = computed(() =>
    (this.data()?.items ?? []).reduce((sum, item) => sum + item.checkedQuantity, 0),
  );
  readonly totalUnits = computed(() =>
    (this.data()?.items ?? []).reduce((sum, item) => sum + item.quantity, 0),
  );

  refresh(): void {
    this.load();
    this.state.refreshBadges();
  }

  money(value: number | null): string {
    return money(value, this.data()?.currency);
  }

  itemTotal(item: GroceryItem): number {
    return (item.unitPrice ?? 0) * item.quantity;
  }
}
