import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';

import { AuthService } from './auth.service';

// /auth/me always answers 200 and /auth/logout clears state itself; bouncing on
// either would loop.
const NEVER_REDIRECT = ['/api/v1/web/auth/me', '/api/v1/web/auth/logout'];

/**
 * A year-long session still ends eventually — when it does, every in-flight
 * request 401s at once. AuthService.signIn() is latched so that produces a
 * single redirect to Authentik rather than a pile-up.
 */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  return next(request).pipe(
    catchError((error: unknown) => {
      if (
        error instanceof HttpErrorResponse &&
        error.status === 401 &&
        !NEVER_REDIRECT.some((path) => request.url.startsWith(path))
      ) {
        auth.onUnauthorized();
      }
      return throwError(() => error);
    }),
  );
};
