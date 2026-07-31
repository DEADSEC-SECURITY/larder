import { ChangeDetectionStrategy, Component, input } from '@angular/core';

// Shared editorial list primitives — every flat table in the app is built from
// these so spacing, hover behavior, and column alignment stay consistent:
//
//   <ui-list>                            card container
//     <ui-row (click)="…">               flex row with hover tint + chip lift
//       <ui-row-main>                    flexible main cell
//         <div class="row-title">…</div>   (global classes from _base.scss)
//         <div class="row-meta">…</div>
//       </ui-row-main>
//       <ui-rail [width]="112">…</ui-rail> fixed right-aligned column (≥ tablet)
//     </ui-row>
//   </ui-list>

@Component({
  selector: 'ui-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<ng-content />',
  styles: `
    :host {
      display: block;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      padding: 6px 10px;
    }
  `,
})
export class UiListComponent {}

@Component({
  selector: 'ui-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<ng-content />',
  host: {
    '[class.row-hover]': 'hover()',
    '[class.selected]': 'selected()',
    '[class.baseline]': 'baseline()',
  },
  styles: `
    :host {
      display: flex;
      align-items: center;
      gap: 14px;
      padding: calc(var(--rpad) + 2px) 10px;
      flex-wrap: wrap;
    }

    :host(.baseline) {
      align-items: baseline;
    }

    :host(.selected) {
      background: var(--accent-bg);
      border-radius: var(--r-row);
    }
  `,
})
export class UiRowComponent {
  /** Row reacts to hover (tint + pointer). Turn off for non-clickable rows. */
  readonly hover = input(true);
  /** Multi-select highlight (accent tint). */
  readonly selected = input(false);
  /** Align cells on the text baseline instead of center. */
  readonly baseline = input(false);
}

@Component({
  selector: 'ui-row-main',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<ng-content />',
  styles: `
    :host {
      flex: 1;
      min-width: 160px;
    }
  `,
})
export class UiRowMainComponent {}

@Component({
  selector: 'ui-rail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<ng-content />',
  host: { '[style.width.px]': 'width()' },
  styles: `
    :host {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      text-align: right;
      flex: none;
    }

    // Fixed widths only make sense once rows are wide enough to align; on
    // mobile the rail collapses to its content.
    @media (max-width: 760px) {
      :host {
        width: auto !important;
      }
    }
  `,
})
export class UiRailComponent {
  /** Fixed column width (px) ≥ tablet so rails align vertically across rows. */
  readonly width = input<number | null>(null);
}

export const UI_LIST = [UiListComponent, UiRowComponent, UiRowMainComponent, UiRailComponent];
