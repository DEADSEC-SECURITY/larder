import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import {
  expiryInfo,
  formatBarcodeDigits,
  kindLabel,
  money,
  productKindLabel,
  shortDate,
  shortDateTime,
} from '../../core/format';
import { ProductDetail, StoreAssignment, UnitRow } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { BrandPickerModal } from '../../modals/brand-picker';
import { BulkEditModal } from '../../modals/bulk-edit';
import { EditUnitModal } from '../../modals/edit-unit';
import { ProductEditModal } from '../../modals/product-edit';
import { RestockPolicyModal } from '../../modals/restock-policy';

@Component({
  selector: 'app-product-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    BrandPickerModal,
    BulkEditModal,
    EditUnitModal,
    ProductEditModal,
    RestockPolicyModal,
  ],
  templateUrl: './product-detail.html',
  styleUrl: './product-detail.scss',
})
export class ProductDetailScreen {
  readonly barcode = input.required<string>();

  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly router = inject(Router);
  readonly state = inject(AppStateService);

  readonly detail = signal<ProductDetail | null>(null);
  readonly unitFilter = signal<'in' | 'all'>('in');
  readonly editingUnit = signal<UnitRow | null>(null);
  readonly policyOpen = signal(false);
  readonly selected = signal<ReadonlySet<string>>(new Set());
  readonly bulkOpen = signal(false);
  readonly confirmDelete = signal(false);
  readonly brandsOpen = signal(false);
  readonly editOpen = signal(false);
  readonly assignment = signal<StoreAssignment | null>(null);

  readonly displayName = computed(() => this.detail()?.name || 'Unidentified item');

  readonly idPill = computed(() => {
    switch (this.detail()?.idStatus) {
      case 'pending':
        return { label: 'Identifying…', cls: 'pill-warn' };
      case 'failed':
        return { label: 'Lookup failed', cls: 'pill-danger' };
      default:
        return { label: 'Identified', cls: 'pill-ok' };
    }
  });

  readonly metaLine = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return '';
    }
    const category = detail.category
      ? detail.category.parentName
        ? `${detail.category.parentName} / ${detail.category.name}`
        : detail.category.name
      : 'uncategorized';
    const kind = detail.productKind === 'food' ? null : productKindLabel(detail.productKind);
    return [kind, category, detail.quantityText ?? detail.packageSize].filter(Boolean).join(' · ');
  });

  /** Package/detail rows for the Product details card; empty fields drop out. */
  readonly detailRows = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return [];
    }
    return [
      { k: 'Kind', v: productKindLabel(detail.productKind) },
      { k: 'Manufacturer', v: detail.manufacturer },
      { k: 'Package size', v: detail.packageSize },
      { k: 'Material', v: detail.material },
      { k: 'Variant', v: detail.variant },
      { k: 'Default storage', v: this.storageLocationName(detail.defaultStorageLocationId) },
    ].filter((row): row is { k: string; v: string } => !!row.v);
  });

  private storageLocationName(id: string | null): string | null {
    return this.state.storageLocations().find((l) => l.id === id)?.name ?? null;
  }

  readonly soonestLabel = computed(() => {
    const detail = this.detail();
    const soonest = detail?.soonestExpiration;
    if (!soonest) {
      return '—';
    }
    return expiryInfo(soonest.date, soonest.daysLeft, detail!.rule ? detail!.rule.days : 4).label;
  });

  readonly units = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return [];
    }
    return this.unitFilter() === 'in'
      ? detail.units.filter((u) => u.status === 'in_stock')
      : detail.units;
  });

  /** Selection restricted to in-stock units (the only editable ones). */
  readonly selectedIds = computed(() => {
    const stocked = new Set(
      (this.detail()?.units ?? []).filter((u) => u.status === 'in_stock').map((u) => u.id),
    );
    return [...this.selected()].filter((id) => stocked.has(id));
  });

  readonly allSelected = computed(() => {
    const stocked = this.units().filter((u) => u.status === 'in_stock');
    return stocked.length > 0 && stocked.every((u) => this.selected().has(u.id));
  });

  readonly parLabel = computed(() => {
    const policy = this.detail()?.policy;
    return policy ? `${policy.reorderPoint} → ${policy.targetStock}` : 'none';
  });

  readonly parNote = computed(() => {
    const policy = this.detail()?.policy;
    if (!policy) {
      return 'never auto-added to shopping';
    }
    const scope = policy.scope === 'stock_group' ? ' · any compatible brand or size' : '';
    return `min ${policy.reorderPoint} · restock to ${policy.targetStock}${scope}`;
  });

  /** Stock-group card: combined stock across every accepted member. */
  readonly groupCard = computed(() => {
    const group = this.detail()?.stockGroup;
    if (!group || !group.active) {
      return null;
    }
    return {
      id: group.id,
      name: group.name,
      note:
        `${group.scopeLabel} · ${group.memberCount} ` +
        `product${group.memberCount === 1 ? '' : 's'}` +
        (group.source === 'automatic' ? ' · matched automatically' : ''),
      inStock: group.inStock,
    };
  });

  readonly ruleText = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return '';
    }
    if (detail.rule) {
      return (
        `Default shelf life: ${detail.rule.days} days (${detail.rule.categoryName}). ` +
        `Units without a printed date get an estimated expiration ${detail.rule.days} ` +
        `days after they’re added.`
      );
    }
    return (
      'No expiration rule applies — units without a printed date show “No date”. ' +
      'Add a rule in Categories & rules.'
    );
  });

  readonly nutritionRows = computed(() => {
    const nutrition = this.detail()?.nutrition;
    if (!nutrition) {
      return [];
    }
    const rows: { k: string; basis: string; serving: string | null }[] = [];
    const push = (
      k: string,
      basisValue: number | null,
      servingValue: number | null | undefined,
      unit: string,
    ) => {
      if (basisValue != null) {
        rows.push({
          k,
          basis: this.nutritionValue(basisValue, unit),
          serving: servingValue == null ? null : this.nutritionValue(servingValue, unit),
        });
      }
    };
    const serving = nutrition.perServing;
    push('Energy', nutrition.energyKcal, serving?.energyKcal, 'kcal');
    push('Fat', nutrition.fatG, serving?.fatG, 'g');
    push('Saturated fat', nutrition.saturatedFatG, serving?.saturatedFatG, 'g');
    push('Carbs', nutrition.carbohydratesG, serving?.carbohydratesG, 'g');
    push('Sugars', nutrition.sugarsG, serving?.sugarsG, 'g');
    push('Fiber', nutrition.fiberG, serving?.fiberG, 'g');
    push('Protein', nutrition.proteinG, serving?.proteinG, 'g');
    push('Salt', nutrition.saltG, serving?.saltG, 'g');
    push('Sodium', nutrition.sodiumG, serving?.sodiumG, 'g');
    return rows;
  });

  readonly nutritionBasisLabel = computed(() => {
    const nutrition = this.detail()?.nutrition;
    return nutrition ? `per ${nutrition.basisAmount} ${nutrition.basisUnit}` : '';
  });

  readonly nutritionServingLabel = computed(
    () => this.detail()?.nutrition?.servingSize || 'per serving',
  );

  constructor() {
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api.productDetail(this.barcode()).subscribe((detail) => this.detail.set(detail));
    this.api
      .productStoreAssignment(this.barcode())
      .subscribe(({ assignment }) => this.assignment.set(assignment));
  }

  refresh(): void {
    this.load();
    this.state.changed();
  }

  unitTitle(unit: UnitRow): string {
    return unit.expirationDate ? `Expires ${shortDate(unit.expirationDate)}` : 'No expiration';
  }

  unitMeta(unit: UnitRow): string {
    const parts = [
      `added ${shortDateTime(unit.dateAdded)}`,
      unit.storeName ?? 'no store',
      unit.storageLocationName,
      unit.price != null ? money(unit.price) : 'no price',
    ];
    return parts.filter(Boolean).join(' · ');
  }

  kindPill(unit: UnitRow): { label: string; cls: string } {
    if (unit.expirationKind === 'confirmed') {
      return { label: 'confirmed', cls: 'pill-ok' };
    }
    if (unit.expirationKind === 'estimated') {
      return { label: 'estimated', cls: 'pill-chip' };
    }
    return { label: kindLabel('unknown'), cls: 'pill-faint' };
  }

  removeUnit(unit: UnitRow): void {
    this.api.removeUnit(unit.id).subscribe(() => {
      this.refresh();
      this.toasts.show(`Removed 1 × ${this.displayName()}`, [
        { label: 'Undo', run: () => this.correctUnit(unit.id, 'in_stock') },
        { label: 'Waste instead', run: () => this.correctUnit(unit.id, 'trashed') },
      ]);
    });
  }

  wasteUnit(unit: UnitRow): void {
    this.api.wasteUnit(unit.id).subscribe(() => {
      this.refresh();
      this.toasts.show(`${this.displayName()} marked wasted`, [
        { label: 'Undo', run: () => this.correctUnit(unit.id, 'in_stock') },
        { label: 'Remove instead', run: () => this.correctUnit(unit.id, 'removed') },
      ]);
    });
  }

  correctUnit(id: string, status: 'in_stock' | 'removed' | 'trashed'): void {
    this.api.setUnitStatus(id, status).subscribe(() => {
      const note =
        status === 'in_stock'
          ? 'Back in stock'
          : status === 'removed'
            ? 'Changed to removed'
            : 'Changed to wasted';
      this.toasts.show(`${note} — ${this.displayName()}`);
      this.refresh();
    });
  }

  retryEnrichment(): void {
    this.api.retryEnrichment(this.barcode()).subscribe(() => {
      this.toasts.show('Lookup queued');
      this.load();
    });
  }

  printBarcode(): void {
    this.api
      .queuePrintJob({
        kind: 'product',
        title: this.displayName(),
        value: this.barcode(),
      })
      .subscribe(() => {
        this.toasts.show(`Barcode for ${this.displayName()} queued for the kitchen printer`);
      });
  }

  toggleSelected(unit: UnitRow): void {
    if (unit.status !== 'in_stock') {
      return;
    }
    const next = new Set(this.selected());
    if (next.has(unit.id)) {
      next.delete(unit.id);
    } else {
      next.add(unit.id);
    }
    this.selected.set(next);
  }

  toggleSelectAll(): void {
    if (this.allSelected()) {
      this.selected.set(new Set());
      return;
    }
    this.selected.set(
      new Set(
        this.units()
          .filter((u) => u.status === 'in_stock')
          .map((u) => u.id),
      ),
    );
  }

  clearSelection(): void {
    this.selected.set(new Set());
  }

  bulkTerminate(action: 'remove' | 'waste'): void {
    const ids = this.selectedIds();
    if (!ids.length) {
      return;
    }
    this.api.bulkUnits({ itemIds: ids, action }).subscribe(({ updated }) => {
      this.clearSelection();
      this.refresh();
      this.toasts.show(
        `${action === 'remove' ? 'Removed' : 'Wasted'} ${updated} × ${this.displayName()}`,
        [{ label: 'Undo', run: () => this.restoreUnits(ids) }],
      );
    });
  }

  private restoreUnits(ids: string[]): void {
    forkJoin(ids.map((id) => this.api.setUnitStatus(id, 'in_stock'))).subscribe(() => {
      this.toasts.show(`Back in stock — ${ids.length} × ${this.displayName()}`);
      this.refresh();
    });
  }

  deleteProduct(): void {
    if ((this.detail()?.inStock ?? 0) > 0) {
      this.toasts.show('Remove or waste the units in stock first');
      return;
    }
    if (!this.confirmDelete()) {
      this.confirmDelete.set(true);
      setTimeout(() => this.confirmDelete.set(false), this.state.confirmationTimeoutMs());
      return;
    }
    this.api.deleteProduct(this.barcode()).subscribe(({ deleted }) => {
      this.toasts.show(deleted ? 'Product deleted' : 'Product archived — history kept');
      this.state.changed();
      this.router.navigateByUrl('/products');
    });
  }

  restoreProduct(): void {
    this.api.restoreProduct(this.barcode()).subscribe(() => {
      this.toasts.show('Product restored');
      this.refresh();
    });
  }

  money(value: number | null): string {
    return money(value);
  }

  historyDate(iso: string): string {
    return shortDateTime(iso);
  }

  barcodeCaption(): string {
    const value = this.barcode();
    return /^\d+$/.test(value) ? formatBarcodeDigits(value) : value;
  }

  private nutritionValue(value: number, unit: string): string {
    return `${Number(value.toFixed(2))} ${unit}`;
  }
}
