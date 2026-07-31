import { Injectable, inject, signal } from '@angular/core';

import { AppStateService } from './app-state.service';

export interface ToastAction {
  label: string;
  run: () => void;
}

export interface Toast {
  id: number;
  message: string;
  actions?: ToastAction[];
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  private readonly state = inject(AppStateService);
  readonly toasts = signal<Toast[]>([]);
  private nextId = 1;

  show(message: string, actions?: ToastAction[]): void {
    const toast = { id: this.nextId++, message, actions };
    this.toasts.update((list) => [...list, toast]);
    setTimeout(
      () => this.dismiss(toast.id),
      // Toasts offering an action linger longer so it can actually be tapped.
      this.state.toastDurationMs() * (actions?.length ? 3 : 1),
    );
  }

  dismiss(id: number): void {
    this.toasts.update((list) => list.filter((t) => t.id !== id));
  }

  act(toast: Toast, action: ToastAction): void {
    this.dismiss(toast.id);
    action.run();
  }
}
