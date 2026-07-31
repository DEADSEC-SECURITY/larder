import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  effect,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ModalComponent } from './modal';

/** GTIN family plus Code 128, which some store labels use. */
const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'itf', 'code_128'];

// Shipping in Chrome/Edge/Android WebView but still missing from lib.dom, and
// absent entirely on iOS Safari — hence the ZXing fallback below.
interface DetectedBarcode {
  rawValue: string;
  format: string;
}
interface BarcodeDetectorLike {
  detect(source: CanvasImageSource): Promise<DetectedBarcode[]>;
}
interface BarcodeDetectorConstructor {
  new (options?: { formats?: string[] }): BarcodeDetectorLike;
  getSupportedFormats?(): Promise<string[]>;
}

@Component({
  selector: 'ui-barcode-scanner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ModalComponent],
  template: `
    <ui-modal [title]="title()" [width]="460" (closed)="closed.emit()">
      <div class="viewport" [class.live]="status() === 'scanning'">
        <video #video playsinline muted autoplay></video>
        @if (status() === 'scanning') {
          <div class="reticle"></div>
        }
        @if (status() !== 'scanning') {
          <div class="overlay">{{ message() }}</div>
        }
      </div>

      <div class="hstack" style="gap: 8px">
        @if (torchAvailable()) {
          <button type="button" class="btn btn-outline btn-sm" (click)="toggleTorch()">
            {{ torchOn() ? 'Light off' : 'Light on' }}
          </button>
        }
        @if (status() === 'error') {
          <button type="button" class="btn btn-outline btn-sm" (click)="start()">Try again</button>
        }
      </div>

      <div>
        <div class="field-label">Or type the number under the barcode</div>
        <form class="hstack" style="gap: 8px" (ngSubmit)="submitManual()">
          <input
            class="input input-on-bg tnum"
            style="flex: 1"
            inputmode="numeric"
            autocomplete="off"
            placeholder="e.g. 0 12345 67890 5"
            [ngModel]="manual()"
            (ngModelChange)="manual.set($event)"
            name="barcode"
          />
          <button type="submit" class="btn btn-primary" [disabled]="!manualValid()">Use</button>
        </form>
        <div class="field-hint">8, 12, 13, or 14 digits. Spaces are ignored.</div>
      </div>
    </ui-modal>
  `,
  styles: `
    .viewport {
      position: relative;
      width: 100%;
      aspect-ratio: 4 / 3;
      border-radius: var(--r-input);
      overflow: hidden;
      background: #16130f;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    video {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: none;
    }

    .viewport.live video {
      display: block;
    }

    .reticle {
      position: absolute;
      left: 8%;
      right: 8%;
      top: 50%;
      height: 26%;
      transform: translateY(-50%);
      border: 2px solid rgba(255, 255, 255, 0.9);
      border-radius: 10px;
      box-shadow: 0 0 0 100vmax rgba(0, 0, 0, 0.32);
    }

    .overlay {
      color: rgba(255, 255, 255, 0.86);
      font-size: 13px;
      text-align: center;
      padding: 0 24px;
      line-height: 1.5;
    }
  `,
})
export class BarcodeScannerComponent implements OnDestroy {
  readonly title = input('Scan a barcode');
  readonly closed = output<void>();
  readonly detected = output<string>();

  readonly status = signal<'starting' | 'scanning' | 'error'>('starting');
  readonly message = signal('Starting the camera…');
  readonly manual = signal('');
  readonly torchOn = signal(false);
  readonly torchAvailable = signal(false);

  private readonly videoRef = viewChild<ElementRef<HTMLVideoElement>>('video');
  private stream: MediaStream | null = null;
  private zxingControls: { stop(): void } | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private finished = false;

  constructor() {
    effect(() => {
      if (this.videoRef() && this.status() === 'starting') {
        void this.start();
      }
    });
  }

  ngOnDestroy(): void {
    this.teardown();
  }

  manualValid(): boolean {
    return /^\d{8}$|^\d{12,14}$/.test(this.manual().replace(/\D/g, ''));
  }

  submitManual(): void {
    const digits = this.manual().replace(/\D/g, '');
    if (this.manualValid()) {
      this.emit(digits);
    }
  }

  async start(): Promise<void> {
    const video = this.videoRef()?.nativeElement;
    if (!video || this.finished) {
      return;
    }
    this.teardown();
    this.status.set('starting');
    this.message.set('Starting the camera…');

    // getUserMedia is gated on a secure context; over plain http the API is
    // simply absent, which otherwise surfaces as a confusing TypeError.
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this.fail('The camera needs an HTTPS connection. Type the number instead.');
      return;
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
      });
    } catch (error) {
      const name = (error as DOMException)?.name;
      this.fail(
        name === 'NotAllowedError'
          ? 'Camera access was blocked. Allow it in your browser settings, or type the number.'
          : name === 'NotFoundError'
            ? 'No camera was found on this device.'
            : 'The camera could not be started. Type the number instead.',
      );
      return;
    }

    video.srcObject = this.stream;
    try {
      await video.play();
    } catch {
      // Autoplay rejection still leaves usable frames on most browsers.
    }
    this.status.set('scanning');
    this.message.set('');
    this.detectTorch();

    const native = (window as unknown as { BarcodeDetector?: BarcodeDetectorConstructor })
      .BarcodeDetector;
    if (native) {
      this.scanWithNativeDetector(native, video);
    } else {
      await this.scanWithZxing(video);
    }
  }

  private scanWithNativeDetector(ctor: BarcodeDetectorConstructor, video: HTMLVideoElement): void {
    const detector = new ctor({ formats: FORMATS });
    let busy = false;
    this.timer = setInterval(async () => {
      if (busy || this.finished || video.readyState < 2) {
        return;
      }
      busy = true;
      try {
        const found = await detector.detect(video);
        if (found.length) {
          this.emit(found[0].rawValue);
        }
      } catch {
        // Transient decode failures are normal between frames.
      } finally {
        busy = false;
      }
    }, 180);
  }

  /** iOS Safari has no BarcodeDetector; pull in ZXing only when needed. */
  private async scanWithZxing(video: HTMLVideoElement): Promise<void> {
    try {
      const { BrowserMultiFormatReader } = await import('@zxing/browser');
      if (this.finished) {
        return;
      }
      const reader = new BrowserMultiFormatReader();
      this.zxingControls = await reader.decodeFromVideoElement(video, (result) => {
        if (result) {
          this.emit(result.getText());
        }
      });
    } catch {
      this.fail('Scanning is not supported on this browser. Type the number instead.');
    }
  }

  private detectTorch(): void {
    const track = this.stream?.getVideoTracks()[0];
    const capabilities = track?.getCapabilities?.() as { torch?: boolean } | undefined;
    this.torchAvailable.set(Boolean(capabilities?.torch));
  }

  async toggleTorch(): Promise<void> {
    const track = this.stream?.getVideoTracks()[0];
    if (!track) {
      return;
    }
    const next = !this.torchOn();
    try {
      // `torch` is a non-standard constraint, so it is absent from lib.dom.
      await track.applyConstraints({
        advanced: [{ torch: next }],
      } as unknown as MediaTrackConstraints);
      this.torchOn.set(next);
    } catch {
      this.torchAvailable.set(false);
    }
  }

  private emit(code: string): void {
    if (this.finished) {
      return;
    }
    this.finished = true;
    // A short buzz is the only confirmation you get holding a phone at a shelf.
    navigator.vibrate?.(60);
    this.teardown();
    this.detected.emit(code);
  }

  private fail(text: string): void {
    this.status.set('error');
    this.message.set(text);
    this.teardown();
  }

  private teardown(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.zxingControls?.stop();
    this.zxingControls = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.torchOn.set(false);
  }
}
