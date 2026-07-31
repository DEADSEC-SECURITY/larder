import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import {
  GENERIC_SCOPE_LABEL,
  productKindLabel,
  stockGroupEvidenceCopy,
} from '../../core/format';
import {
  StockGroupCandidate,
  StockGroupDetail,
  StockGroupMember,
  StockGroupSummary,
} from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { StepperComponent } from '../../ui/stepper';
import { TermChipsComponent } from '../../ui/term-chips';
import { UI_LIST } from '../../ui/list';

/**
 * Stock groups settings: manage groups of interchangeable products that
 * satisfy one generic household need. The backend owns all matching — this
 * screen only edits the group's configuration and reviews its suggestions.
 * `?group=<id>` deep-links straight into a group's detail view.
 */
@Component({
  selector: 'app-stock-groups',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, RouterLink, StepperComponent, TermChipsComponent, UI_LIST],
  templateUrl: './stock-groups.html',
  styleUrl: './stock-groups.scss',
})
export class StockGroupsScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly router = inject(Router);
  private readonly state = inject(AppStateService);

  readonly genericLabel = GENERIC_SCOPE_LABEL;

  readonly groups = signal<StockGroupSummary[]>([]);
  readonly detail = signal<StockGroupDetail | null>(null);
  readonly showArchived = signal(false);
  readonly rescanning = signal(false);
  /** Barcode of the candidate/member a request is in flight for. */
  readonly busyBarcode = signal<string | null>(null);
  readonly confirmDeactivate = signal(false);

  // Keep-stocked form (synced per group).
  readonly policyMin = signal(0);
  readonly policyTarget = signal(1);

  // Matching-rules form (synced per group).
  readonly formName = signal('');
  readonly formRequired = signal<string[]>([]);
  readonly formExcluded = signal<string[]>([]);
  readonly formForms = signal<string[]>([]);
  readonly formCategories = signal<string[]>([]);
  readonly formAutoThreshold = signal(0.9);
  readonly formReviewThreshold = signal(0.65);
  private formGroupId: string | null = null;

  readonly newMemberBarcode = signal('');

  /**
   * Categories are edited as bare slugs ("berries") — the taxonomy prefix
   * ("en:", "local:") is plumbing. This map restores the original id on save;
   * unknown slugs default to "en:" since matching uses the English taxonomy.
   */
  private readonly categoryIdByLabel = new Map<string, string>();
  readonly categorySuggestions = signal<string[]>([]);

  private readonly route = inject(ActivatedRoute);
  readonly groupId = toSignal(this.route.queryParamMap.pipe(map((p) => p.get('group'))), {
    initialValue: this.route.snapshot.queryParamMap.get('group'),
  });

  readonly active = computed(() => this.groups().filter((g) => g.active));
  readonly archived = computed(() => this.groups().filter((g) => !g.active));

  readonly pendingCandidates = computed(
    () => this.detail()?.candidates.filter((c) => c.status === 'pending') ?? [],
  );
  readonly rejectedCount = computed(
    () => this.detail()?.candidates.filter((c) => c.status === 'rejected').length ?? 0,
  );

  readonly thresholdError = computed(() =>
    this.formAutoThreshold() < this.formReviewThreshold()
      ? 'Auto-accept confidence must be at least the review confidence.'
      : '',
  );

  constructor() {
    this.api.categories().subscribe(({ categories }) => {
      for (const category of categories) {
        this.categoryIdByLabel.set(categoryLabel(category.id), category.id);
      }
      this.categorySuggestions.set(
        [...this.categoryIdByLabel.keys()].sort((a, b) => a.localeCompare(b)),
      );
    });
    effect(() => {
      this.state.version();
      const id = this.groupId();
      this.loadList();
      if (id) {
        this.loadDetail(id);
      } else {
        this.detail.set(null);
        this.formGroupId = null;
      }
    });
  }

  private loadList(): void {
    this.api.stockGroups(true).subscribe(({ stockGroups }) => this.groups.set(stockGroups));
  }

  private loadDetail(id: string): void {
    this.api.stockGroupDetail(id).subscribe({
      next: (detail) => this.applyDetail(detail),
      error: () => {
        this.toasts.show('Stock group not found');
        this.closeDetail();
      },
    });
  }

  /** Server state wins; form fields resync only when showing a new group. */
  private applyDetail(detail: StockGroupDetail): void {
    this.detail.set(detail);
    if (this.formGroupId !== detail.id) {
      this.formGroupId = detail.id;
      this.confirmDeactivate.set(false);
      this.policyMin.set(detail.policy?.reorderPoint ?? 0);
      this.policyTarget.set(Math.max(detail.policy?.targetStock ?? 1, 1));
      this.formName.set(detail.name);
      this.formRequired.set([...detail.requiredTerms]);
      this.formExcluded.set([...detail.excludedTerms]);
      this.formForms.set([...detail.allowedForms]);
      for (const id of detail.categoryIds) {
        // Group ids may cover taxonomy entries the household list doesn't.
        if (!this.categoryIdByLabel.has(categoryLabel(id))) {
          this.categoryIdByLabel.set(categoryLabel(id), id);
        }
      }
      this.formCategories.set(detail.categoryIds.map(categoryLabel));
      this.formAutoThreshold.set(detail.autoMatchThreshold);
      this.formReviewThreshold.set(detail.reviewThreshold);
    }
  }

  open(group: StockGroupSummary): void {
    this.router.navigate([], { queryParams: { group: group.id } });
  }

  closeDetail(): void {
    this.router.navigate([], { queryParams: {} });
  }

  kindLabel(group: { productKind: StockGroupSummary['productKind'] }): string {
    return productKindLabel(group.productKind);
  }

  groupMeta(group: StockGroupSummary): string {
    const parts = [
      `${group.memberCount} accepted product${group.memberCount === 1 ? '' : 's'}`,
      group.policy
        ? `min ${group.policy.reorderPoint} · restock to ${group.policy.targetStock}`
        : 'no restock policy',
    ];
    return parts.join(' · ');
  }

  rescan(): void {
    this.rescanning.set(true);
    this.api.autoMatchStockGroups().subscribe({
      next: ({ evaluated, matched, review }) => {
        this.rescanning.set(false);
        this.toasts.show(
          `Rescanned ${evaluated} products — ${matched} matched, ${review} for review`,
        );
        this.refresh();
      },
      error: () => this.rescanning.set(false),
    });
  }

  onPolicyMin(value: number): void {
    this.policyMin.set(value);
    if (this.policyTarget() < value) {
      this.policyTarget.set(value);
    }
  }

  savePolicy(): void {
    const detail = this.detail();
    if (!detail) {
      return;
    }
    const min = this.policyMin();
    const target = min === 0 ? 0 : Math.max(this.policyTarget(), min);
    this.api.setStockGroupPolicy(detail.id, min, target).subscribe(() => {
      this.toasts.show(min === 0 ? 'Restock policy removed' : 'Restock policy saved');
      this.refresh();
    });
  }

  memberMeta(member: StockGroupMember): string {
    const source =
      member.evidence?.['seedProduct'] === true
        ? 'original product'
        : member.source === 'user'
        ? 'accepted by you'
        : member.source === 'migration'
          ? 'migrated from prior policy'
          : 'matched automatically';
    const parts = [
      member.brand,
      member.packageSize,
      source,
      member.confidence != null && member.source !== 'user'
        ? `${Math.round(member.confidence * 100)}% match`
        : null,
    ];
    return parts.filter(Boolean).join(' · ');
  }

  candidateEvidence(candidate: StockGroupCandidate): string {
    const lines = stockGroupEvidenceCopy(candidate.evidence);
    lines.push(`${Math.round(candidate.confidence * 100)}% match`);
    return lines.join(' · ');
  }

  decide(candidate: StockGroupCandidate, accepted: boolean): void {
    const detail = this.detail();
    if (!detail || this.busyBarcode()) {
      return;
    }
    // Acceptance can migrate policy and shopping demand transactionally, so
    // wait for the refreshed detail instead of updating membership optimistically.
    this.busyBarcode.set(candidate.barcode);
    this.api
      .decideStockGroupCandidate(detail.id, candidate.barcode, {
        accepted,
        learn: true,
        migratePolicy: true,
      })
      .subscribe({
        next: (refreshed) => {
          this.busyBarcode.set(null);
          this.applyDetail(refreshed);
          this.toasts.show(
            accepted
              ? `${candidate.name ?? candidate.barcode} now counts toward ${detail.name}`
              : 'Suggestion rejected — that product stays separate',
          );
          this.loadList();
          this.state.changed();
        },
        error: () => this.busyBarcode.set(null),
      });
  }

  removeMember(member: StockGroupMember): void {
    const detail = this.detail();
    if (!detail || this.busyBarcode()) {
      return;
    }
    this.busyBarcode.set(member.barcode);
    this.api.removeStockGroupMember(detail.id, member.barcode, true).subscribe({
      next: () => {
        this.busyBarcode.set(null);
        this.toasts.show(`${member.name ?? member.barcode} removed from ${detail.name}`);
        this.refresh();
      },
      error: () => this.busyBarcode.set(null),
    });
  }

  addMember(): void {
    const detail = this.detail();
    const barcode = this.newMemberBarcode().trim();
    if (!detail || !barcode) {
      return;
    }
    this.api
      .addStockGroupMember(detail.id, { barcode, learn: true, migratePolicy: true })
      .subscribe({
        next: () => {
          this.newMemberBarcode.set('');
          this.toasts.show(`Product added to ${detail.name}`);
          this.refresh();
        },
        error: () => this.toasts.show('No product with that barcode'),
      });
  }

  saveRules(): void {
    const detail = this.detail();
    if (!detail || this.thresholdError()) {
      return;
    }
    this.api
      .updateStockGroup(detail.id, {
        name: this.formName().trim() || detail.name,
        requiredTerms: this.formRequired(),
        excludedTerms: this.formExcluded(),
        allowedForms: this.formForms(),
        categoryIds: this.formCategories().map(
          (label) =>
            this.categoryIdByLabel.get(label) ??
            (label.includes(':') ? label : `en:${label}`),
        ),
        autoMatchThreshold: this.formAutoThreshold(),
        reviewThreshold: this.formReviewThreshold(),
      })
      .subscribe(() => {
        this.toasts.show('Matching rules saved — products rescanned');
        this.formGroupId = null;
        this.refresh();
      });
  }

  setActive(active: boolean): void {
    const detail = this.detail();
    if (!detail) {
      return;
    }
    if (!active && !this.confirmDeactivate()) {
      this.confirmDeactivate.set(true);
      setTimeout(
        () => this.confirmDeactivate.set(false),
        this.state.confirmationTimeoutMs(),
      );
      return;
    }
    this.api.updateStockGroup(detail.id, { active }).subscribe(() => {
      this.toasts.show(active ? `${detail.name} reactivated` : `${detail.name} deactivated`);
      this.confirmDeactivate.set(false);
      this.formGroupId = null;
      this.refresh();
    });
  }

  private refresh(): void {
    this.loadList();
    const id = this.groupId();
    if (id) {
      this.loadDetail(id);
    }
    this.state.changed();
  }
}

/** "en:berries" / "local:dairy" → "berries" / "dairy". */
function categoryLabel(id: string): string {
  const colon = id.indexOf(':');
  return colon === -1 ? id : id.slice(colon + 1);
}
