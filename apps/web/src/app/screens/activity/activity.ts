import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { ClockService } from '../../core/clock.service';
import { relativeTime } from '../../core/format';
import { ActivityEvent, ActivityKind } from '../../core/models';
import { EmptyComponent } from '../../ui/empty';
import { UI_LIST } from '../../ui/list';

type Filter = 'all' | ActivityKind;

const KIND_META: Record<string, [string, string]> = {
  added: ['Added', 'var(--accent)'],
  removed: ['Removed', 'var(--warn)'],
  wasted: ['Wasted', 'var(--danger)'],
  restored: ['Restored', 'var(--accent)'],
  changed: ['Changed', 'var(--sub)'],
  estimated: ['Estimated', 'var(--sub)'],
  ignored: ['Ignored', 'var(--faint)'],
  failed: ['Failed', 'var(--danger)'],
};

@Component({
  selector: 'app-activity',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EmptyComponent, UI_LIST],
  template: `
    <div class="stack">
      <span class="page-title">Activity</span>
      <div class="hstack wrap" style="gap: 8px">
        @for (f of filters(); track f.key) {
          <button
            type="button"
            class="chip chip-sm"
            [class.active]="filter() === f.key"
            (click)="filter.set(f.key)"
          >
            {{ f.label }} {{ f.count }}
          </button>
        }
      </div>
      <ui-list>
        @for (event of rows(); track $index) {
          <ui-row [hover]="false" [baseline]="true">
            <span class="a-op" [style.color]="meta(event.kind)[1]">{{ meta(event.kind)[0] }}</span>
            <ui-row-main>
              <span style="font-size: 13.5px; font-weight: 600">{{ event.product }}</span>
              @if (event.detail) {
                <span class="sub" style="font-size: 12.5px"> {{ event.detail }}</span>
              }
            </ui-row-main>
            <span class="faint" style="font-size: 11.5px">{{ event.source }}</span>
            <ui-rail [width]="44">
              <span class="a-rel tnum faint">{{ relative(event.at) }}</span>
            </ui-rail>
          </ui-row>
        } @empty {
          <ui-empty message="No events of this type yet." />
        }
      </ui-list>
    </div>
  `,
  styles: `
    .a-op {
      font-weight: 600;
      width: 76px;
      flex: none;
      font-size: 13px;
    }

    .a-rel {
      font-size: 11.5px;
    }
  `,
})
export class ActivityScreen {
  private readonly api = inject(ApiService);
  private readonly clock = inject(ClockService);
  private readonly state = inject(AppStateService);

  readonly events = signal<ActivityEvent[]>([]);
  readonly filter = signal<Filter>('all');

  constructor() {
    effect(() => {
      this.state.version();
      this.api.activity(200).subscribe(({ events }) => this.events.set(events));
    });
  }

  readonly filters = computed(() => {
    const events = this.events();
    const keys: Filter[] = [
      'all',
      'added',
      'removed',
      'wasted',
      'restored',
      'ignored',
      'changed',
      'estimated',
      'failed',
    ];
    return keys.map((key) => ({
      key,
      label: key === 'all' ? 'All' : (KIND_META[key]?.[0] ?? key),
      count: key === 'all' ? events.length : events.filter((e) => e.kind === key).length,
    }));
  });

  readonly rows = computed(() => {
    const events = this.events();
    const filter = this.filter();
    return (filter === 'all' ? events : events.filter((e) => e.kind === filter)).slice(0, 60);
  });

  meta(kind: string): [string, string] {
    return KIND_META[kind] ?? [kind, 'var(--sub)'];
  }

  relative(iso: string): string {
    return relativeTime(iso, this.clock.now());
  }
}
