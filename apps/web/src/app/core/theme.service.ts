import { Injectable, signal } from '@angular/core';

export type ThemeChoice = 'auto' | 'light' | 'dark';
export type Density = 'comfortable' | 'compact';

const THEME_KEY = 'larder.theme';
const DENSITY_KEY = 'larder.density';

// Matches --card in styles/_tokens.scss. The installed app tints its status bar
// with this, so it has to track the *resolved* theme, not the OS preference.
const TOPBAR_COLOR: Record<'light' | 'dark', string> = {
  light: '#ffffff',
  dark: '#211e18',
};

@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly theme = signal<ThemeChoice>(
    (localStorage.getItem(THEME_KEY) as ThemeChoice) || 'auto',
  );
  readonly density = signal<Density>(
    (localStorage.getItem(DENSITY_KEY) as Density) || 'comfortable',
  );

  private readonly systemDark = window.matchMedia('(prefers-color-scheme: dark)');

  constructor() {
    this.systemDark.addEventListener('change', () => this.apply());
    this.apply();
  }

  setTheme(theme: ThemeChoice): void {
    this.theme.set(theme);
    localStorage.setItem(THEME_KEY, theme);
    this.apply();
  }

  setDensity(density: Density): void {
    this.density.set(density);
    localStorage.setItem(DENSITY_KEY, density);
    this.apply();
  }

  private apply(): void {
    const dark =
      this.theme() === 'dark' || (this.theme() === 'auto' && this.systemDark.matches);
    const root = document.documentElement;
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    root.setAttribute('data-density', this.density());
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', TOPBAR_COLOR[dark ? 'dark' : 'light']);
  }
}
