import {
  ChangeDetectionStrategy,
  Component,
  effect,
  inject,
  signal,
} from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { RoutingRule, RuleMatchType } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { RuleEditModal } from '../../modals/rule-edit';
import { ToggleComponent } from '../../ui/toggle';
import { UI_LIST } from '../../ui/list';

@Component({
  selector: 'app-routing',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RuleEditModal, ToggleComponent, UI_LIST],
  templateUrl: './routing.html',
  styleUrl: './routing.scss',
})
export class RoutingScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  private readonly router = inject(Router);
  readonly state = inject(AppStateService);

  readonly rules = signal<RoutingRule[]>([]);
  readonly editing = signal<RoutingRule | null>(null);
  readonly creating = signal(false);
  readonly presetMatchType = signal<RuleMatchType | null>(null);
  readonly presetMatcherId = signal<string | null>(null);
  readonly confirmDeleteId = signal<string | null>(null);

  private pendingRuleId: string | null = null;

  constructor() {
    const params = inject(ActivatedRoute).snapshot.queryParamMap;
    this.pendingRuleId = params.get('rule');
    if (params.get('create')) {
      this.presetMatchType.set(params.get('create') as RuleMatchType);
      this.presetMatcherId.set(params.get('brandId'));
      this.creating.set(true);
    }
    effect(() => {
      this.state.version();
      this.load();
    });
  }

  load(): void {
    this.api.routingRules().subscribe(({ rules }) => {
      this.rules.set(rules);
      if (this.pendingRuleId) {
        const target = rules.find((rule) => rule.id === this.pendingRuleId);
        this.pendingRuleId = null;
        if (target) {
          this.editing.set(target);
        }
      }
    });
  }

  matcherLabel(rule: RoutingRule): string {
    const kind = rule.matchType[0].toUpperCase() + rule.matchType.slice(1);
    return `${kind} · ${rule.match.label}`;
  }

  toggleEnabled(rule: RoutingRule, enabled: boolean): void {
    this.api.updateRoutingRule(rule.id, { enabled }).subscribe(() => {
      this.toasts.show(enabled ? 'Rule enabled' : 'Rule disabled');
      this.load();
      this.state.changed();
    });
  }

  remove(rule: RoutingRule): void {
    if (this.confirmDeleteId() !== rule.id) {
      this.confirmDeleteId.set(rule.id);
      setTimeout(
        () => this.confirmDeleteId.set(null),
        this.state.confirmationTimeoutMs(),
      );
      return;
    }
    this.api.deleteRoutingRule(rule.id).subscribe(() => {
      this.toasts.show('Rule deleted');
      this.confirmDeleteId.set(null);
      this.load();
      this.state.changed();
    });
  }

  openConflicting(): void {
    // A 409 means a rule already exists for the chosen matcher — reload and
    // let the user find it at the top of the list.
    this.creating.set(false);
    this.load();
    this.toasts.show('Showing existing rules');
  }

  closeCreate(): void {
    this.creating.set(false);
    this.presetMatchType.set(null);
    this.presetMatcherId.set(null);
    this.router.navigate([], { queryParams: {} });
  }

  refresh(): void {
    this.load();
    this.state.changed();
  }
}
