import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

// Settings hub — side nav (desktop) / chip row (mobile) with each catalog
// management screen as a child route.
@Component({
  selector: 'app-settings-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="settings-shell">
      <nav class="settings-nav">
        <div class="settings-nav-title display">Settings</div>
        @for (section of sections; track section.label) {
          @if (section.label) {
            <div class="settings-nav-group">{{ section.label }}</div>
          }
          @for (item of section.items; track item.path) {
            <a
              [routerLink]="item.path"
              routerLinkActive="active"
              [routerLinkActiveOptions]="{ exact: item.exact }"
              >{{ item.label }}</a
            >
          }
        }
      </nav>
      <div class="settings-content">
        <router-outlet />
      </div>
    </div>
  `,
  styles: `
    .settings-shell {
      display: grid;
      grid-template-columns: 200px minmax(0, 1fr);
      gap: 28px;
      align-items: start;
    }

    .settings-nav {
      position: sticky;
      top: 88px;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .settings-nav-title {
      font-size: 24px;
      padding: 0 12px 10px;
    }

    .settings-nav-group {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--faint);
      padding: 14px 12px 4px;

      /* Rule extending from the label to the nav edge. */
      &::after {
        content: '';
        flex: 1;
        height: 1px;
        background: var(--line);
      }
    }

    .settings-nav a {
      padding: 8px 12px;
      border-radius: 10px;
      font-size: 13.5px;
      font-weight: 500;
      color: var(--sub);
      white-space: nowrap;

      &:hover {
        opacity: 0.75;
      }

      &.active {
        background: var(--chip);
        color: var(--ink);
        font-weight: 600;
        opacity: 1;
      }
    }

    @media (max-width: 760px) {
      .settings-shell {
        display: block;
      }

      .settings-nav {
        position: static;
        flex-direction: row;
        flex-wrap: wrap;
        align-items: center;
        gap: 4px;
        margin-bottom: 16px;
      }

      .settings-nav-title {
        width: 100%;
        padding: 0 2px 8px;
      }

      /* The chip row reads fine unlabeled — group headings are desktop-only. */
      .settings-nav-group {
        display: none;
      }

      .settings-nav a {
        background: var(--chip);
        opacity: 0.75;

        &.active {
          background: var(--ink);
          color: var(--card);
        }
      }
    }
  `,
})
export class SettingsLayout {
  readonly sections = [
    {
      label: null,
      items: [
        { path: '/settings', label: 'Preferences', exact: true },
        { path: '/settings/integrations', label: 'Integrations', exact: false },
      ],
    },
    {
      label: 'Catalog',
      items: [
        { path: '/settings/categories', label: 'Categories & rules', exact: false },
        { path: '/settings/brands', label: 'Brands', exact: false },
        { path: '/settings/stock-groups', label: 'Stock groups', exact: false },
        { path: '/settings/storage', label: 'Storage locations', exact: false },
      ],
    },
    {
      label: 'Shopping',
      items: [
        { path: '/settings/stores', label: 'Stores', exact: false },
        { path: '/settings/rules', label: 'Shopping rules', exact: false },
        { path: '/settings/taxes', label: 'Tax locations', exact: false },
      ],
    },
    {
      label: 'Scanner',
      items: [{ path: '/settings/commands', label: 'Commands', exact: false }],
    },
  ];
}
