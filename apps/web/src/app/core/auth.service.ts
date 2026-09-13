import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, of } from 'rxjs';
import { catchError, map, tap } from 'rxjs/operators';

const BASE = '/api/v1/web/auth';

export function shouldAutoRedirect(search: string): boolean {
  // A callback error must stop on the usable retry toast. Automatically
  // starting OIDC again here creates an especially violent loop in iframes.
  return !new URLSearchParams(search).has('authError');
}

export interface AuthUser {
  sub: string;
  name: string | null;
  email: string | null;
  username: string | null;
  groups: string[];
}

export interface AuthState {
  /** False when the deployment runs without Authentik — nothing is gated. */
  enabled: boolean;
  authenticated: boolean;
  /** Everyday operations are open on the configured private origin. */
  internalAccess?: boolean;
  user: AuthUser | null;
  loginUrl: string | null;
}

export interface SessionRow {
  id: string;
  current: boolean;
  createdAt: string;
  lastSeenAt: string | null;
  expiresAt: string;
  userAgent: string | null;
}

/** Human-readable reasons the backend can bounce back on ?authError=. */
const ERROR_COPY: Record<string, string> = {
  provider: 'Authentik refused the sign-in.',
  expired: 'That sign-in took too long — try again.',
  state: 'Sign-in could not be verified. Try again.',
  issuer: 'Sign-in came back from an unexpected provider.',
  audience: 'Sign-in was issued for a different application.',
  nonce: 'Sign-in could not be verified. Try again.',
  exchange: 'Could not reach Authentik to finish signing in.',
  claims: 'Authentik did not return an account id.',
  forbidden: 'Your account is not allowed into Larder.',
};

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  readonly state = signal<AuthState | null>(null);
  readonly user = computed(() => this.state()?.user ?? null);
  /** True only once the backend has confirmed sign-in is switched on. */
  readonly required = computed(() => this.state()?.enabled === true);
  /** Optimistic while unknown, so the shell renders instead of flashing a gate. */
  readonly signedIn = computed(() => this.state()?.authenticated !== false);
  /** Fail closed for settings until the server has confirmed access. */
  readonly canChangeSettings = computed(() => this.state()?.authenticated === true);

  private redirecting = false;
  private readonly autoRedirect = shouldAutoRedirect(window.location.search);

  refresh(): Observable<AuthState | null> {
    return this.http.get<AuthState>(`${BASE}/me`).pipe(
      tap((state) => this.state.set(state)),
      // A failure here must not block the app; the interceptor handles 401s.
      catchError(() => of(null)),
    );
  }

  /** Full-page navigation — the OIDC handshake cannot happen over XHR. */
  signIn(next?: string): void {
    if (this.redirecting) {
      return;
    }
    this.redirecting = true;
    const target = next ?? window.location.pathname + window.location.search;
    window.location.assign(`${BASE}/login?next=${encodeURIComponent(target)}`);
  }

  signOut(everywhere = false): void {
    this.http
      .post<{ endSessionUrl: string | null }>(`${BASE}/logout?everywhere=${everywhere}`, {})
      .pipe(catchError(() => of({ endSessionUrl: null })))
      .subscribe((result) => {
        this.state.set(null);
        // Ending the Authentik session too, so the next sign-in really prompts.
        window.location.assign(result.endSessionUrl ?? '/');
      });
  }

  sessions(): Observable<SessionRow[]> {
    return this.http.get<{ sessions: SessionRow[] }>(`${BASE}/sessions`).pipe(
      map((result) => result.sessions),
      catchError(() => of<SessionRow[]>([])),
    );
  }

  /** Called by the interceptor and the SSE watchdog on a 401. */
  onUnauthorized(): void {
    this.state.update((current) =>
      current ? { ...current, authenticated: false, user: null } : current,
    );
    if (this.autoRedirect) {
      this.signIn();
    }
  }

  static describeError(code: string | null): string | null {
    if (!code) {
      return null;
    }
    return ERROR_COPY[code] ?? 'Sign-in failed. Try again.';
  }
}
