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
import { PRODUCT_KINDS, productKindLabel } from '../../core/format';
import { CategoryRow, ProductKind, ProductKindUpper } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { EditRuleModal } from '../../modals/edit-rule';

interface CategoryNode extends CategoryRow {
  children: CategoryRow[];
}

@Component({
  selector: 'app-categories',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, EditRuleModal],
  templateUrl: './categories.html',
  styleUrl: './categories.scss',
})
export class CategoriesScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly state = inject(AppStateService);

  readonly categories = signal<CategoryRow[]>([]);
  readonly newName = signal('');
  readonly newKind = signal<ProductKindUpper>('FOOD');
  readonly kindFilter = signal<'all' | ProductKind>('all');
  readonly editing = signal<CategoryRow | null>(null);

  readonly kinds = PRODUCT_KINDS;

  /** Kinds that actually occur, for the filter chip row. */
  readonly presentKinds = computed(() => {
    const present = new Set(this.categories().map((c) => c.productKind));
    return PRODUCT_KINDS.filter((k) => present.has(k.kind));
  });

  constructor() {
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  readonly tree = computed<CategoryNode[]>(() => {
    const all = this.categories();
    const kind = this.kindFilter();
    // A root stays visible if it or any child matches the kind filter.
    const matches = (c: CategoryRow) => kind === 'all' || c.productKind === kind;
    const roots = all.filter((c) => !c.parentId);
    return roots
      .map((root) => ({
        ...root,
        children: all.filter((c) => c.parentId === root.id && matches(c)),
      }))
      .filter((root) => matches(root) || root.children.length > 0);
  });

  load(): void {
    this.api.categories().subscribe(({ categories }) => this.categories.set(categories));
  }

  add(): void {
    const name = this.newName().trim();
    if (!name) {
      return;
    }
    // Only food gets an automatic shelf-life default; other kinds usually
    // don't expire, so they start with no expiration default.
    const kind = this.newKind();
    const days = kind === 'FOOD' ? 7 : undefined;
    this.api.createCategory({ name, days, productKind: kind }).subscribe({
      next: () => {
        this.toasts.show(
          days != null
            ? `Category “${name}” added · 7 d default`
            : `Category “${name}” added · no expiration default`,
        );
        this.newName.set('');
        this.load();
      },
      error: () => this.toasts.show('That category already exists'),
    });
  }

  kindBadge(category: CategoryRow): string {
    return category.productKind === 'food' ? '' : productKindLabel(category.productKind);
  }

  ruleChip(category: CategoryRow): { label: string; own: boolean } {
    if (category.days != null) {
      return {
        label: category.parentId ? `${category.days} d · own` : `${category.days} d default`,
        own: true,
      };
    }
    if (category.inheritedDays != null) {
      return { label: `${category.inheritedDays} d · inherited`, own: false };
    }
    return { label: 'No expiration', own: false };
  }
}
