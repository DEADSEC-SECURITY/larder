import { Injectable, signal } from '@angular/core';

const MOBILE_QUERY = '(max-width: 760px)';

@Injectable({ providedIn: 'root' })
export class LayoutService {
  private readonly query = window.matchMedia(MOBILE_QUERY);
  readonly isMobile = signal(this.query.matches);

  constructor() {
    this.query.addEventListener('change', (event) => this.isMobile.set(event.matches));
  }
}
