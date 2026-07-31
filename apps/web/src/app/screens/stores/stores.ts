import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { relativeTime } from '../../core/format';
import { StoreRow } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { ProgressComponent } from '../../ui/progress';
import { ToggleComponent } from '../../ui/toggle';

@Component({
  selector: 'app-stores',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ProgressComponent, ToggleComponent],
  templateUrl: './stores.html',
  styleUrl: './stores.scss',
})
export class StoresScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  readonly state = inject(AppStateService);

  readonly stores = signal<StoreRow[]>([]);
  readonly adding = signal(false);
  readonly newName = signal('');

  constructor() {
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api.stores().subscribe(({ stores }) => this.stores.set(stores));
  }

  lastShop(store: StoreRow): string {
    return store.lastObservedAt ? `${relativeTime(store.lastObservedAt)} ago` : '—';
  }

  toggleActive(store: StoreRow, active: boolean): void {
    this.api.updateStore(store.id, { active }).subscribe(() => {
      this.toasts.show(`${store.name} ${active ? 'activated' : 'deactivated'}`);
      this.load();
      this.state.refreshStores();
    });
  }

  add(): void {
    const name = this.newName().trim();
    if (!name) {
      return;
    }
    this.api.createStore(name).subscribe(() => {
      this.toasts.show(`Store “${name}” added`);
      this.newName.set('');
      this.adding.set(false);
      this.load();
      this.state.refreshStores();
    });
  }

  printControl(store: StoreRow): void {
    this.api.ensureControlBarcode(store.id).subscribe(({ payload }) => {
      this.api
        .queuePrintJob({
          kind: 'control',
          title: store.name,
          value: payload,
        })
        .subscribe(() => {
          this.toasts.show(`Control barcode for ${store.name} queued for the kitchen printer`);
        });
    });
  }
}
