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

import {
  VideoInput,
  barcodeVideoConstraints,
  isIOSDevice,
  preferredBarcodeCamera,
} from './barcode-camera';
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

      @if (cameras().length > 1) {
        <label class="camera-picker">
          <span class="field-label">Camera</span>
          <select
            class="select"
            [ngModel]="selectedCameraId()"
            (ngModelChange)="selectCamera($event)"
          >
            @for (camera of cameras(); track camera.deviceId; let index = $index) {
              <option [value]="camera.deviceId">
                {{ camera.label || 'Camera ' + (index + 1) }}
              </option>
            }
          </select>
          <span class="field-hint"
            >Use the main rear or 1× camera if the picture looks blurry.</span
          >
        </label>
      }

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

    .camera-picker {
      display: grid;
      gap: 6px;
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
  readonly cameras = signal<VideoInput[]>([]);
  readonly selectedCameraId = signal('');

  private readonly videoRef = viewChild<ElementRef<HTMLVideoElement>>('video');
  private stream: MediaStream | null = null;
  private zxingControls: { stop(): void } | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private finished = false;
  private startVersion = 0;

  private static readonly CAMERA_STORAGE_KEY = 'larder.barcode-camera-id';

  constructor() {
    effect(() => {
      if (this.videoRef()) {
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

  async start(requestedDeviceId: string | null = this.savedCameraId()): Promise<void> {
    const video = this.videoRef()?.nativeElement;
    if (!video || this.finished) {
      return;
    }
    const version = ++this.startVersion;
    this.stopCapture();
    this.status.set('starting');
    this.message.set('Starting the camera…');

    // getUserMedia is gated on a secure context; over plain http the API is
    // simply absent, which otherwise surfaces as a confusing TypeError.
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this.fail('The camera needs an HTTPS connection. Type the number instead.');
      return;
    }

    let stream: MediaStream;
    try {
      stream = await this.openCamera(requestedDeviceId);
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

    if (version !== this.startVersion) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    const cameras = await this.listCameras();
    if (version !== this.startVersion) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    this.cameras.set(cameras);
    const currentDeviceId = stream.getVideoTracks()[0]?.getSettings().deviceId ?? '';
    const saved = requestedDeviceId
      ? cameras.find((camera) => camera.deviceId === requestedDeviceId)
      : undefined;
    const preferred =
      saved ??
      preferredBarcodeCamera(
        cameras,
        currentDeviceId,
        isIOSDevice(navigator.userAgent, navigator.platform, navigator.maxTouchPoints),
      );

    if (preferred && preferred.deviceId !== currentDeviceId) {
      stream.getTracks().forEach((track) => track.stop());
      try {
        stream = await this.openCamera(preferred.deviceId);
      } catch {
        // A device can disappear between enumeration and selection. Reopen the
        // browser's best rear-facing fallback rather than failing the scanner.
        stream = await this.openCamera(null);
      }
    }

    if (version !== this.startVersion) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    this.stream = stream;
    const activeDeviceId = stream.getVideoTracks()[0]?.getSettings().deviceId ?? '';
    this.selectedCameraId.set(activeDeviceId);
    if (activeDeviceId) {
      this.saveCameraId(activeDeviceId);
    }
    await this.optimizeFocus(stream.getVideoTracks()[0]);
    if (version !== this.startVersion) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    video.srcObject = stream;
    try {
      await video.play();
    } catch {
      // Autoplay rejection still leaves usable frames on most browsers.
    }
    if (version !== this.startVersion) {
      return;
    }
    this.status.set('scanning');
    this.message.set('');
    this.detectTorch();

    const native = (window as unknown as { BarcodeDetector?: BarcodeDetectorConstructor })
      .BarcodeDetector;
    if (native) {
      this.scanWithNativeDetector(native, video);
    } else {
      await this.scanWithZxing(video, version);
    }
  }

  selectCamera(deviceId: string): void {
    if (!deviceId || deviceId === this.selectedCameraId()) {
      return;
    }
    this.selectedCameraId.set(deviceId);
    this.saveCameraId(deviceId);
    void this.start(deviceId);
  }

  private async openCamera(deviceId: string | null): Promise<MediaStream> {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: barcodeVideoConstraints(deviceId),
      });
    } catch (error) {
      const name = (error as DOMException)?.name;
      if (deviceId && (name === 'NotFoundError' || name === 'OverconstrainedError')) {
        this.saveCameraId(null);
        return navigator.mediaDevices.getUserMedia({
          audio: false,
          video: barcodeVideoConstraints(),
        });
      }
      throw error;
    }
  }

  private async listCameras(): Promise<VideoInput[]> {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      return devices
        .filter((device) => device.kind === 'videoinput')
        .map(({ deviceId, label }) => ({ deviceId, label }));
    } catch {
      return [];
    }
  }

  private async optimizeFocus(track: MediaStreamTrack | undefined): Promise<void> {
    if (!track?.getCapabilities || !track.applyConstraints) {
      return;
    }
    const capabilities = track.getCapabilities() as MediaTrackCapabilities & {
      focusMode?: string[];
    };
    if (!capabilities.focusMode?.includes('continuous')) {
      return;
    }
    try {
      await track.applyConstraints({
        advanced: [{ focusMode: 'continuous' }],
      } as unknown as MediaTrackConstraints);
    } catch {
      // The high-resolution stream remains useful when focus control is absent.
    }
  }

  private savedCameraId(): string | null {
    try {
      return localStorage.getItem(BarcodeScannerComponent.CAMERA_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  private saveCameraId(deviceId: string | null): void {
    try {
      if (deviceId) {
        localStorage.setItem(BarcodeScannerComponent.CAMERA_STORAGE_KEY, deviceId);
      } else {
        localStorage.removeItem(BarcodeScannerComponent.CAMERA_STORAGE_KEY);
      }
    } catch {
      // Private browsing can disable storage; selection still works this time.
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
  private async scanWithZxing(video: HTMLVideoElement, version: number): Promise<void> {
    try {
      const [{ BrowserMultiFormatOneDReader }, { BarcodeFormat, DecodeHintType }] =
        await Promise.all([import('@zxing/browser'), import('@zxing/library')]);
      if (this.finished || version !== this.startVersion) {
        return;
      }
      const hints = new Map([
        [
          DecodeHintType.POSSIBLE_FORMATS,
          [
            BarcodeFormat.EAN_13,
            BarcodeFormat.EAN_8,
            BarcodeFormat.UPC_A,
            BarcodeFormat.UPC_E,
            BarcodeFormat.ITF,
            BarcodeFormat.CODE_128,
          ],
        ],
      ]);
      const reader = new BrowserMultiFormatOneDReader(hints, {
        delayBetweenScanAttempts: 80,
      });
      const controls = await reader.decodeFromVideoElement(video, (result) => {
        if (result) {
          this.emit(result.getText());
        }
      });
      if (version !== this.startVersion) {
        controls.stop();
        return;
      }
      this.zxingControls = controls;
    } catch {
      if (version === this.startVersion) {
        this.fail('Scanning is not supported on this browser. Type the number instead.');
      }
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
    this.startVersion += 1;
    this.stopCapture();
  }

  private stopCapture(): void {
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
