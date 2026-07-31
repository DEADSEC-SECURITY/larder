import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

/**
 * Chip-list editor for small term lists (brand aliases, matching terms,
 * category ids). Enter or comma commits the draft; blur commits too, so text
 * left in the input is never silently dropped by a Save click. Backspace in
 * an empty input removes the last chip; Ctrl/Cmd+Z undoes chip edits (and
 * Ctrl/Cmd+Shift+Z or Ctrl+Y redoes them).
 *
 * Pass `suggestions` to offer completions: the closest matches drop down as
 * you type, Enter takes the highlighted one (arrows move it), and Escape —
 * or just typing past every match — falls back to the literal text.
 */
@Component({
  selector: 'ui-term-chips',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  template: `
    <div
      class="chip-wrap"
      (keydown.control.z)="undo($event)"
      (keydown.meta.z)="undo($event)"
      (keydown.control.shift.z)="redo($event)"
      (keydown.meta.shift.z)="redo($event)"
      (keydown.control.y)="redo($event)"
    >
      <div class="chip-box" (click)="termInput.focus()">
        @for (term of terms(); track term) {
          <span class="term-chip">
            {{ term }}
            <button
              type="button"
              class="term-x"
              (click)="remove(term)"
              [attr.aria-label]="'Remove ' + term"
            >
              ✕
            </button>
          </span>
        }
        <input
          #termInput
          class="term-input"
          [placeholder]="terms().length ? '' : placeholder()"
          [ngModel]="draft()"
          (ngModelChange)="onDraft($event)"
          (keydown.enter)="commit($event)"
          (keydown.,)="commit($event)"
          (keydown.backspace)="backspace()"
          (keydown.arrowdown)="move(1, $event)"
          (keydown.arrowup)="move(-1, $event)"
          (keydown.escape)="dismiss($event)"
          (blur)="commit()"
        />
      </div>
      @if (matches().length && !dismissed()) {
        <div class="suggest">
          @for (match of matches(); track match; let i = $index) {
            <button
              type="button"
              class="suggest-item"
              [class.hl]="i === highlighted()"
              (mousedown)="pick(match, $event)"
            >
              {{ match }}
            </button>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .chip-wrap {
      position: relative;
    }

    .chip-box {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
      background: var(--bg);
      border: 1px solid var(--line);
      border-radius: var(--r-input);
      padding: 8px;
      min-height: var(--h-input);
      cursor: text;
    }

    .term-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: var(--chip);
      color: var(--ink);
      border-radius: 99px;
      padding: 5px 6px 5px 11px;
      font-size: 12.5px;
      font-weight: 600;
    }

    .term-x {
      border: none;
      background: none;
      color: var(--faint);
      cursor: pointer;
      font-size: 11px;
      padding: 1px 4px;

      &:hover {
        color: var(--danger);
      }
    }

    .term-input {
      flex: 1;
      min-width: 120px;
      border: none;
      background: transparent;
      font-family: inherit;
      font-size: 13.5px;
      color: var(--ink);
      outline: none;
      padding: 5px;

      &::placeholder {
        color: var(--faint);
      }
    }

    .suggest {
      position: absolute;
      top: calc(100% + 4px);
      left: 0;
      right: 0;
      z-index: 30;
      display: flex;
      flex-direction: column;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 4px;
      box-shadow: var(--shadow-modal);
      max-height: 220px;
      overflow: auto;
    }

    .suggest-item {
      border: none;
      background: none;
      font: inherit;
      font-size: 13px;
      color: var(--ink);
      text-align: left;
      padding: 7px 10px;
      border-radius: 8px;
      cursor: pointer;

      &.hl,
      &:hover {
        background: var(--chip);
      }
    }
  `,
})
export class TermChipsComponent {
  readonly value = input.required<string[]>();
  readonly placeholder = input('Type and press Enter…');
  /** Known completions; matches drop down as the user types. */
  readonly suggestions = input<string[]>([]);
  readonly valueChange = output<string[]>();

  readonly draft = signal('');
  /** Escape hides the dropdown for the current draft so Enter takes the raw text. */
  readonly dismissed = signal(false);
  readonly highlighted = signal(0);

  /**
   * Local working copy. Rapid event sequences (blur-commit immediately
   * followed by a ✕ click) must compose on this, not on `value()` — the input
   * binding only refreshes on the next change-detection cycle, so the second
   * event would see a stale list and silently drop the first edit. Synced
   * from the parent only when the content genuinely differs (a new group,
   * a server refresh) — our own emissions echo back unchanged and are
   * ignored, which is also what keeps the undo history alive across edits.
   */
  readonly terms = signal<string[]>([]);

  private undoStack: string[][] = [];
  private redoStack: string[][] = [];

  constructor() {
    effect(() => {
      const incoming = this.value();
      untracked(() => {
        if (!sameList(incoming, this.terms())) {
          this.terms.set([...incoming]);
          this.undoStack = [];
          this.redoStack = [];
        }
      });
    });
  }

  readonly matches = computed(() => {
    const query = this.draft().trim().toLowerCase();
    if (!query) {
      return [];
    }
    const taken = new Set(this.terms().map((t) => t.toLowerCase()));
    const pool = this.suggestions().filter((s) => !taken.has(s.toLowerCase()));
    const starts = pool.filter((s) => s.toLowerCase().startsWith(query));
    const contains = pool.filter(
      (s) => !s.toLowerCase().startsWith(query) && s.toLowerCase().includes(query),
    );
    return [...starts, ...contains].slice(0, 6);
  });

  onDraft(value: string): void {
    this.draft.set(value);
    this.dismissed.set(false);
    this.highlighted.set(0);
  }

  private update(next: string[]): void {
    this.undoStack.push(this.terms());
    this.redoStack = [];
    this.terms.set(next);
    this.valueChange.emit(next);
  }

  private add(term: string): void {
    this.draft.set('');
    this.highlighted.set(0);
    if (!term || this.terms().some((t) => t.toLowerCase() === term.toLowerCase())) {
      return;
    }
    this.update([...this.terms(), term]);
  }

  commit(event?: Event): void {
    event?.preventDefault();
    const raw = this.draft().replace(/,/g, '').trim();
    if (!raw) {
      return;
    }
    const options = this.matches();
    const suggested =
      !this.dismissed() && options.length ? options[this.highlighted()] : null;
    this.add(suggested ?? raw);
  }

  pick(term: string, event: Event): void {
    // mousedown, so the input never blurs (blur would commit the raw draft first).
    event.preventDefault();
    this.add(term);
  }

  move(delta: number, event: Event): void {
    const count = this.matches().length;
    if (!count || this.dismissed()) {
      return;
    }
    event.preventDefault();
    this.highlighted.set((this.highlighted() + delta + count) % count);
  }

  dismiss(event: Event): void {
    if (this.matches().length && !this.dismissed()) {
      event.preventDefault();
      this.dismissed.set(true);
    }
  }

  remove(term: string): void {
    this.update(this.terms().filter((t) => t !== term));
  }

  backspace(): void {
    if (!this.draft() && this.terms().length) {
      this.update(this.terms().slice(0, -1));
    }
  }

  undo(event: Event): void {
    // With text in the input, leave Ctrl+Z to the browser's native text undo.
    if (this.draft()) {
      return;
    }
    const previous = this.undoStack.pop();
    if (!previous) {
      return;
    }
    event.preventDefault();
    this.redoStack.push(this.terms());
    this.terms.set(previous);
    this.valueChange.emit(previous);
  }

  redo(event: Event): void {
    if (this.draft()) {
      return;
    }
    const next = this.redoStack.pop();
    if (!next) {
      return;
    }
    event.preventDefault();
    this.undoStack.push(this.terms());
    this.terms.set(next);
    this.valueChange.emit(next);
  }
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((term, i) => term === b[i]);
}
