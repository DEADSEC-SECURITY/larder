import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { AppStateService } from './app-state.service';
import { AuthService } from './auth.service';
import { LiveService } from './live.service';

describe('LiveService session watchdog', () => {
  afterEach(() => vi.unstubAllGlobals());

  for (const internalAccess of [true, false]) {
    it(`only redirects a signed-out public user (internalAccess=${internalAccess})`, () => {
      let source: { onerror?: () => void };
      vi.stubGlobal(
        'EventSource',
        class {
          constructor() {
            source = this;
          }
          onerror?: () => void;
        },
      );
      const auth = {
        refresh: () => of({ enabled: true, authenticated: false, internalAccess }),
        onUnauthorized: vi.fn(),
      };
      TestBed.configureTestingModule({
        providers: [
          { provide: AppStateService, useValue: {} },
          { provide: AuthService, useValue: auth },
        ],
      });
      TestBed.inject(LiveService).start();
      source!.onerror!();
      expect(auth.onUnauthorized).toHaveBeenCalledTimes(internalAccess ? 0 : 1);
    });
  }
});
