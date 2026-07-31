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
import { StorageLocationRow } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { UI_LIST } from '../../ui/list';

/**
 * Storage locations settings: where units are kept (Pantry, Garage, …) as
 * opposed to the store they were bought at. Locations archive instead of
 * deleting — history keeps pointing at them, selectors hide them.
 */
@Component({
  selector: 'app-storage-locations',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, UI_LIST],
  template: `
    <div class="stack">
      <span class="page-title">Storage locations</span>

      <div class="callout">
        A storage location is where a unit is <b>kept</b> — separate from the store it was
        bought at. New units get their product’s default location first, then the
        category’s; you can override per unit or at scan time. Print a location’s
        command barcode and scan it to store the next items there — for example on the
        freezer door.
      </div>

      <div class="hstack" style="gap: 8px">
        <input
          class="input"
          style="flex: 1"
          placeholder="New location name…"
          [ngModel]="newName()"
          (ngModelChange)="newName.set($event)"
          (keyup.enter)="add()"
        />
        <button type="button" class="btn btn-primary" (click)="add()">Add</button>
      </div>

      <ui-list>
        @for (location of active(); track location.id) {
          <ui-row [hover]="false">
            <ui-row-main>
              @if (renamingId() === location.id) {
                <input
                  class="input"
                  style="max-width: 280px"
                  [ngModel]="renameValue()"
                  (ngModelChange)="renameValue.set($event)"
                  (keyup.enter)="rename(location)"
                  (keyup.escape)="renamingId.set(null)"
                />
              } @else {
                <div class="row-title">
                  <button
                    type="button"
                    class="name-btn"
                    title="Rename"
                    (click)="startRename(location)"
                  >
                    {{ location.name }}
                  </button>
                  <span class="count-chip tnum">×{{ location.inStockUnits }}</span>
                </div>
                <div class="row-meta">
                  {{ location.inStockUnits }} unit{{ location.inStockUnits === 1 ? '' : 's' }}
                  in stock
                </div>
              }
            </ui-row-main>
            @if (renamingId() === location.id) {
              <button type="button" class="btn btn-primary btn-sm" (click)="rename(location)">
                Save
              </button>
              <button
                type="button"
                class="btn btn-outline btn-sm"
                (click)="renamingId.set(null)"
              >
                Cancel
              </button>
            } @else {
              <button
                type="button"
                class="btn btn-outline btn-sm"
                (click)="printControl(location)"
              >
                Print barcode
              </button>
              <button
                type="button"
                class="btn btn-outline btn-sm"
                (click)="setActive(location, false)"
              >
                Archive
              </button>
            }
          </ui-row>
        } @empty {
          <div class="faint" style="padding: 24px; text-align: center; font-size: 13px">
            No storage locations yet — add one above.
          </div>
        }
      </ui-list>

      @if (archived().length) {
        <div>
          <button
            type="button"
            class="chip"
            [class.active]="showArchived()"
            (click)="showArchived.set(!showArchived())"
          >
            Archived locations ({{ archived().length }})
          </button>
        </div>
        @if (showArchived()) {
          <ui-list>
            @for (location of archived(); track location.id) {
              <ui-row [hover]="false">
                <ui-row-main>
                  <div class="row-title" style="opacity: 0.6">{{ location.name }}</div>
                  <div class="row-meta">
                    Hidden from selectors — old units keep this location.
                  </div>
                </ui-row-main>
                <button
                  type="button"
                  class="btn btn-outline btn-sm"
                  (click)="setActive(location, true)"
                >
                  Restore
                </button>
              </ui-row>
            }
          </ui-list>
        }
      }
    </div>
  `,
  styles: `
    .name-btn {
      background: none;
      border: none;
      padding: 0;
      font: inherit;
      font-weight: inherit;
      color: inherit;
      cursor: pointer;

      &:hover {
        color: var(--accent);
      }
    }
  `,
})
export class StorageLocationsScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly state = inject(AppStateService);

  readonly locations = signal<StorageLocationRow[]>([]);
  readonly newName = signal('');
  readonly renamingId = signal<string | null>(null);
  readonly renameValue = signal('');
  readonly showArchived = signal(false);

  readonly active = computed(() => this.locations().filter((l) => l.active));
  readonly archived = computed(() => this.locations().filter((l) => !l.active));

  constructor() {
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api
      .storageLocations()
      .subscribe(({ storageLocations }) => this.locations.set(storageLocations));
  }

  private changed(): void {
    this.load();
    this.state.refreshStorageLocations();
  }

  add(): void {
    const name = this.newName().trim();
    if (!name) {
      return;
    }
    this.api.createStorageLocation(name).subscribe({
      next: (location) => {
        this.toasts.show(`Location “${location.name}” added`);
        this.newName.set('');
        this.changed();
      },
      error: () => this.toasts.show('That location already exists'),
    });
  }

  startRename(location: StorageLocationRow): void {
    this.renamingId.set(location.id);
    this.renameValue.set(location.name);
  }

  rename(location: StorageLocationRow): void {
    const name = this.renameValue().trim();
    if (!name || name === location.name) {
      this.renamingId.set(null);
      return;
    }
    this.api.updateStorageLocation(location.id, { name }).subscribe(() => {
      this.toasts.show(`Renamed to “${name}”`);
      this.renamingId.set(null);
      this.changed();
    });
  }

  /** Queue a CMD:LOC barcode — scanning it stores the next items here. */
  printControl(location: StorageLocationRow): void {
    this.api.ensureLocationControlBarcode(location.id).subscribe(({ payload }) => {
      this.api
        .queuePrintJob({
          kind: 'control',
          title: location.name,
          value: payload,
        })
        .subscribe(() => {
          this.toasts.show(
            `Location barcode for ${location.name} queued for the kitchen printer`,
          );
        });
    });
  }

  setActive(location: StorageLocationRow, active: boolean): void {
    this.api.updateStorageLocation(location.id, { active }).subscribe(() => {
      this.toasts.show(`${location.name} ${active ? 'restored' : 'archived'}`);
      this.changed();
    });
  }
}
