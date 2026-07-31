import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { AppStateService } from '../../core/app-state.service';
import { AuthService, SessionRow } from '../../core/auth.service';
import { HouseholdSettings, RemovalPolicy } from '../../core/models';
import { Density, ThemeChoice, ThemeService } from '../../core/theme.service';
import { ToastService } from '../../core/toast.service';
import { ToggleComponent } from '../../ui/toggle';

const POLICY_DESCRIPTIONS: Record<RemovalPolicy, string> = {
  FEFO: 'Removals consume the soonest-expiring unit',
  FIFO: 'Removals consume the oldest-added unit',
  LIFO: 'Removals consume the newest-added unit',
  LEFO: 'Removals consume the latest-expiring unit',
};

type TimingKey =
  | 'intakePopupTimeoutSeconds'
  | 'intakeScanGroupTimeoutSeconds'
  | 'scannerModeTimeoutSeconds'
  | 'scannerContextTimeoutSeconds'
  | 'scannerOfflineAfterSeconds'
  | 'toastDurationSeconds'
  | 'confirmationTimeoutSeconds'
  | 'liveUpdateDebounceMs'
  | 'fallbackRefreshSeconds';

interface TimingControl {
  key: TimingKey;
  title: string;
  desc: string;
  unit: string;
  scale: number;
  min: number;
  max: number;
  step: number;
  defaultValue: number;
}

@Component({
  selector: 'app-settings',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ToggleComponent],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsScreen {
  private readonly api = inject(ApiService);
  private readonly toasts = inject(ToastService);
  readonly theme = inject(ThemeService);
  readonly state = inject(AppStateService);
  readonly auth = inject(AuthService);

  readonly themeOptions: ThemeChoice[] = ['auto', 'light', 'dark'];
  readonly densityOptions: Density[] = ['comfortable', 'compact'];
  readonly policies: RemovalPolicy[] = ['FEFO', 'FIFO', 'LIFO', 'LEFO'];

  readonly confirmReset = signal(false);
  /** null until the user asks — no point listing sessions nobody looked at. */
  readonly sessions = signal<SessionRow[] | null>(null);

  readonly settings = computed(() => this.state.settings());
  readonly policyDescription = computed(() => {
    const policy = this.settings()?.removalPolicy ?? 'FEFO';
    return POLICY_DESCRIPTIONS[policy];
  });

  readonly inventoryToggles: {
    key: keyof HouseholdSettings;
    title: string;
    desc: string;
  }[] = [
    {
      key: 'autoGrocery',
      title: 'Auto-build shopping list',
      desc: 'From restock policies and stock levels',
    },
    {
      key: 'requireStoreOnManualAdd',
      title: 'Require store on manual add',
      desc: 'Quick add won’t save without a store',
    },
    {
      key: 'badgeExpiringNav',
      title: 'Badge expiring items in nav',
      desc: 'Show a count on Inventory',
    },
  ];

  readonly scannerToggles: {
    key: keyof HouseholdSettings;
    title: string;
    desc: string;
  }[] = [
    {
      key: 'retryFailedLookups',
      title: 'Retry failed lookups',
      desc: 'Unknown barcodes are retried hourly',
    },
    {
      key: 'audibleScanConfirmation',
      title: 'Audible scan confirmation',
      desc: 'Beep on accepted scans',
    },
  ];

  readonly workflowTimings: TimingControl[] = [
    {
      key: 'intakePopupTimeoutSeconds',
      title: 'Intake popup inactivity',
      desc: 'Auto-save and close after no scans or edits; 0 keeps it open',
      unit: 'sec',
      scale: 1,
      min: 0,
      max: 3600,
      step: 1,
      defaultValue: 30,
    },
    {
      key: 'intakeScanGroupTimeoutSeconds',
      title: 'Repeat-scan grouping',
      desc: 'Same barcode within this gap increments the current intake quantity',
      unit: 'sec',
      scale: 1,
      min: 1,
      max: 3600,
      step: 1,
      defaultValue: 30,
    },
    {
      key: 'scannerModeTimeoutSeconds',
      title: 'Scanner operation mode',
      desc: 'How long Add, Remove, Trash, or Add to List remains active',
      unit: 'sec',
      scale: 1,
      min: 5,
      max: 3600,
      step: 1,
      defaultValue: 30,
    },
    {
      key: 'scannerContextTimeoutSeconds',
      title: 'Store and location context',
      desc: 'How long a scanned store or storage location remains selected',
      unit: 'min',
      scale: 60,
      min: 0.5,
      max: 1440,
      step: 0.5,
      defaultValue: 1800,
    },
    {
      key: 'scannerOfflineAfterSeconds',
      title: 'Scanner offline threshold',
      desc: 'No scan activity for this long marks a scanner offline',
      unit: 'hr',
      scale: 3600,
      min: 1,
      max: 168,
      step: 1,
      defaultValue: 172800,
    },
  ];

  readonly interfaceTimings: TimingControl[] = [
    {
      key: 'toastDurationSeconds',
      title: 'Toast messages',
      desc: 'How long success and status messages remain visible',
      unit: 'sec',
      scale: 1,
      min: 1,
      max: 60,
      step: 1,
      defaultValue: 3,
    },
    {
      key: 'confirmationTimeoutSeconds',
      title: 'Second-tap confirmations',
      desc: 'Time allowed for the confirming tap on destructive actions',
      unit: 'sec',
      scale: 1,
      min: 1,
      max: 60,
      step: 1,
      defaultValue: 3,
    },
    {
      key: 'liveUpdateDebounceMs',
      title: 'Live-update debounce',
      desc: 'Combines bursts of scanner and server events before refreshing',
      unit: 'ms',
      scale: 1,
      min: 50,
      max: 5000,
      step: 50,
      defaultValue: 300,
    },
    {
      key: 'fallbackRefreshSeconds',
      title: 'Fallback refresh',
      desc: 'Polling interval used while live events are quiet or disconnected',
      unit: 'sec',
      scale: 1,
      min: 5,
      max: 3600,
      step: 5,
      defaultValue: 60,
    },
  ];

  onPolicy(policy: RemovalPolicy): void {
    this.api.updateSettings({ removalPolicy: policy }).subscribe((settings) => {
      this.state.setSettings(settings);
      this.toasts.show(`Removal policy · ${policy}`);
    });
  }

  onWindowDays(value: number): void {
    const days = Math.min(30, Math.max(1, Math.round(value) || 4));
    this.api.updateSettings({ expiringWindowDays: days }).subscribe((settings) => {
      this.state.setSettings(settings);
      this.state.refreshBadges();
      this.toasts.show(`Expiring window · ${days} days`);
    });
  }

  onToggle(key: keyof HouseholdSettings, value: boolean): void {
    this.api.updateSettings({ [key]: value }).subscribe((settings) => {
      this.state.setSettings(settings);
      this.state.refreshBadges();
    });
  }

  toggleValue(key: keyof HouseholdSettings): boolean {
    return Boolean(this.settings()?.[key]);
  }

  timingValue(control: TimingControl): number {
    const current = this.settings()?.[control.key];
    const persisted = typeof current === 'number' ? current : control.defaultValue;
    return persisted / control.scale;
  }

  onTiming(control: TimingControl, rawValue: string | number): void {
    const parsed = Number(rawValue);
    const fallback = control.defaultValue / control.scale;
    const displayValue = Number.isFinite(parsed) ? parsed : fallback;
    const clamped = Math.min(control.max, Math.max(control.min, displayValue));
    const persisted = Math.round(clamped * control.scale);
    this.api.updateSettings({ [control.key]: persisted }).subscribe((settings) => {
      this.state.setSettings(settings);
      this.state.refreshBadges();
      this.toasts.show(`${control.title} · ${clamped} ${control.unit}`);
    });
  }

  resetTimings(controls: TimingControl[]): void {
    const payload: Partial<HouseholdSettings> = {};
    for (const control of controls) {
      payload[control.key] = control.defaultValue;
    }
    this.api.updateSettings(payload).subscribe((settings) => {
      this.state.setSettings(settings);
      this.state.refreshBadges();
      this.toasts.show('Timing restored to defaults');
    });
  }

  exportCsv(): void {
    window.open(this.api.exportInventoryUrl(), '_blank');
    this.toasts.show('Inventory exported');
  }

  recalculate(): void {
    this.api.recalculateEstimates().subscribe(({ updated }) => {
      this.toasts.show(`Recalculated ${updated} estimates`);
      this.state.changed();
    });
  }

  reset(): void {
    if (!this.confirmReset()) {
      this.confirmReset.set(true);
      setTimeout(() => this.confirmReset.set(false), this.state.confirmationTimeoutMs());
      return;
    }
    this.api.resetInventory().subscribe(({ removed }) => {
      this.confirmReset.set(false);
      this.toasts.show(`Inventory reset — ${removed} units cleared`);
      this.state.changed();
    });
  }

  loadSessions(): void {
    this.auth.sessions().subscribe((rows) => this.sessions.set(rows));
  }

  /** User-agent strings are unreadable; a platform hint is all that's useful. */
  describeAgent(agent: string | null): string {
    if (!agent) {
      return 'Unknown device';
    }
    for (const [needle, label] of [
      ['iPhone', 'iPhone'],
      ['iPad', 'iPad'],
      ['Android', 'Android'],
      ['Macintosh', 'Mac'],
      ['Windows', 'Windows'],
      ['Linux', 'Linux'],
    ] as const) {
      if (agent.includes(needle)) {
        return label;
      }
    }
    return 'Other device';
  }

  shortDate(value: string | null): string {
    if (!value) {
      return 'never';
    }
    return new Date(value).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }
}
