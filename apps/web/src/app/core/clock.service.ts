import { Injectable, signal } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class ClockService {
  /** Ticks every second — drives the header clock and countdowns. */
  readonly now = signal(new Date());

  constructor() {
    setInterval(() => this.now.set(new Date()), 1000);
  }
}
