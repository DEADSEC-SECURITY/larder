import { describe, expect, it } from 'vitest';

import { shouldAutoRedirect } from './auth.service';

describe('AuthService redirect policy', () => {
  it('automatically starts the first sign-in attempt', () => {
    expect(shouldAutoRedirect('')).toBe(true);
    expect(shouldAutoRedirect('?view=overview')).toBe(true);
  });

  it('stops after a callback failure so an iframe cannot flicker forever', () => {
    expect(shouldAutoRedirect('?authError=expired')).toBe(false);
    expect(shouldAutoRedirect('?view=overview&authError=state')).toBe(false);
  });
});
