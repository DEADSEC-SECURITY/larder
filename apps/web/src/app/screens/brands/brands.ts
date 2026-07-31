import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { BrandRow } from '../../core/models';
import { BrandEditModal } from '../../modals/brand-edit';
import { EmptyComponent } from '../../ui/empty';
import { UI_LIST } from '../../ui/list';

@Component({
  selector: 'app-brands',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, BrandEditModal, EmptyComponent, UI_LIST],
  templateUrl: './brands.html',
  styleUrl: './brands.scss',
})
export class BrandsScreen {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  readonly state = inject(AppStateService);

  readonly brands = signal<BrandRow[]>([]);
  readonly search = signal('');
  readonly editing = signal<BrandRow | null>(null);
  readonly creating = signal(false);

  readonly rows = computed(() => {
    const query = this.search().toLowerCase().trim();
    if (!query) {
      return this.brands();
    }
    return this.brands().filter(
      (brand) =>
        brand.name.toLowerCase().includes(query) ||
        brand.aliases.some((alias) => alias.toLowerCase().includes(query)),
    );
  });

  constructor() {
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api.brands().subscribe(({ brands }) => this.brands.set(brands));
  }

  openRule(brand: BrandRow): void {
    if (brand.routingRuleId) {
      this.router.navigate(['/settings/rules'], {
        queryParams: { rule: brand.routingRuleId },
      });
    } else {
      this.router.navigate(['/settings/rules'], {
        queryParams: { create: 'brand', brandId: brand.id },
      });
    }
  }
}
