import { barcodeVideoConstraints, isIOSDevice, preferredBarcodeCamera } from './barcode-camera';

describe('barcode camera selection', () => {
  const camera = (deviceId: string, label: string) => ({ deviceId, label });

  it('prefers the standard rear camera over front, ultra-wide, and telephoto lenses', () => {
    const selected = preferredBarcodeCamera(
      [
        camera('front', 'Front Camera'),
        camera('ultra', 'Back Ultra Wide Camera'),
        camera('main', 'Back Camera'),
        camera('tele', 'Back Telephoto Camera'),
      ],
      'ultra',
      false,
    );

    expect(selected?.deviceId).toBe('main');
  });

  it('uses the standard iOS rear-camera position when labels are localized', () => {
    const selected = preferredBarcodeCamera(
      [camera('front', '前面カメラ'), camera('main', '背面カメラ'), camera('ultra', '超広角')],
      'ultra',
      true,
    );

    expect(selected?.deviceId).toBe('main');
  });

  it('keeps the current camera when labels do not identify a lens off iOS', () => {
    const selected = preferredBarcodeCamera(
      [camera('one', 'Camera 1'), camera('current', 'Camera 2')],
      'current',
      false,
    );

    expect(selected?.deviceId).toBe('current');
  });

  it('requests a 1080p rear stream or an exact selected device', () => {
    expect(barcodeVideoConstraints()).toMatchObject({
      facingMode: { ideal: 'environment' },
      width: { ideal: 1920 },
      height: { ideal: 1080 },
    });
    expect(barcodeVideoConstraints('main')).toMatchObject({
      deviceId: { exact: 'main' },
      width: { ideal: 1920 },
      height: { ideal: 1080 },
    });
  });

  it('recognizes iPhones and touch-based iPads identifying as Macs', () => {
    expect(isIOSDevice('Mozilla/5.0 (iPhone)', 'iPhone', 5)).toBe(true);
    expect(isIOSDevice('Mozilla/5.0 (Macintosh)', 'MacIntel', 5)).toBe(true);
    expect(isIOSDevice('Mozilla/5.0 (Macintosh)', 'MacIntel', 0)).toBe(false);
  });
});
