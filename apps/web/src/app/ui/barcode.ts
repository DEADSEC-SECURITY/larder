import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  effect,
  input,
  viewChild,
} from '@angular/core';
import JsBarcode from 'jsbarcode';

import { formatBarcodeDigits } from '../core/format';

/**
 * Renders a real scannable barcode when the value is encodable (EAN-13/UPC or
 * CODE128 for control payloads); falls back to a decorative strip for manual ids.
 */
@Component({
  selector: 'ui-barcode',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="holder">
      @if (encodable()) {
        <svg #svg></svg>
      } @else {
        <div class="decorative" [style.height.px]="height()"></div>
      }
      <div class="digits tnum">{{ caption() }}</div>
    </div>
  `,
  styles: `
    .holder {
      text-align: center;
    }

    svg {
      max-width: 100%;
    }

    .decorative {
      width: 150px;
      margin: 0 auto;
      border-radius: 2px;
      background: repeating-linear-gradient(
        90deg,
        var(--ink) 0 2px,
        transparent 2px 4px,
        var(--ink) 4px 7px,
        transparent 7px 10px,
        var(--ink) 10px 11px,
        transparent 11px 14px
      );
    }

    .digits {
      font-size: 11.5px;
      color: var(--sub);
      letter-spacing: 0.18em;
      margin-top: 5px;
    }
  `,
})
export class BarcodeComponent {
  readonly value = input.required<string>();
  readonly height = input(44);

  private readonly svg = viewChild<ElementRef<SVGSVGElement>>('svg');

  constructor() {
    effect(() => {
      const element = this.svg()?.nativeElement;
      if (!element || !this.encodable()) {
        return;
      }
      const value = this.value();
      const ink = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim();
      try {
        JsBarcode(element, value, {
          format: /^\d{13}$/.test(value) ? 'EAN13' : /^\d{12}$/.test(value) ? 'UPC' : 'CODE128',
          displayValue: false,
          height: this.height(),
          width: 1.6,
          margin: 0,
          background: 'transparent',
          lineColor: ink || '#2a2620',
        });
      } catch {
        // Bad check digit etc. — leave the empty svg; the caption still shows the value.
      }
    });
  }

  encodable(): boolean {
    return !this.value().startsWith('MANUAL-');
  }

  caption(): string {
    const value = this.value();
    return /^\d+$/.test(value) ? formatBarcodeDigits(value) : value;
  }
}
