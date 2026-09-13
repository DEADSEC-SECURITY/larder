import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { AuthService } from '../../core/auth.service';
import { SettingsLayout } from './settings-layout';

let editorLoaded = false;

@Component({ template: 'Settings editor' })
class SettingsEditor {
  constructor() {
    editorLoaded = true;
  }
}

describe('SettingsLayout access', () => {
  it('keeps settings editors inactive until sign-in and offers an explicit login', async () => {
    editorLoaded = false;
    const auth = { canChangeSettings: signal(false), signIn: vi.fn() };
    TestBed.configureTestingModule({
      providers: [
        { provide: AuthService, useValue: auth },
        provideRouter([
          {
            path: 'settings',
            component: SettingsLayout,
            children: [{ path: '', component: SettingsEditor }],
          },
        ]),
      ],
    });
    const harness = await RouterTestingHarness.create('/settings');
    expect(harness.routeNativeElement?.textContent).toContain('Sign in to change settings');
    expect(editorLoaded).toBe(false);
    expect(auth.signIn).not.toHaveBeenCalled();
    harness.routeNativeElement?.querySelector('button')?.click();
    expect(auth.signIn).toHaveBeenCalledOnce();
    auth.canChangeSettings.set(true);
    harness.detectChanges();
    expect(editorLoaded).toBe(true);
    expect(harness.routeNativeElement?.textContent).toContain('Settings editor');
  });
});
