export interface VideoInput {
  deviceId: string;
  label: string;
}

/**
 * Pick a normal rear 1x camera when a browser exposes a phone's physical and
 * virtual lenses separately. The current stream remains the safe fallback on
 * platforms whose localized labels do not reveal which lens is which.
 */
export function preferredBarcodeCamera(
  cameras: readonly VideoInput[],
  currentDeviceId: string,
  isiOS: boolean,
): VideoInput | undefined {
  if (!cameras.length) {
    return undefined;
  }

  const ranked = cameras
    .map((camera) => ({ camera, score: cameraScore(camera.label) }))
    .sort((a, b) => b.score - a.score);

  if (ranked[0].score > 0) {
    return ranked[0].camera;
  }

  // WebKit exposes localized labels. On iOS its stable workaround for the
  // multi-lens regression is the second enumerated video input: the standard
  // rear camera follows the front camera.
  if (isiOS && cameras.length > 1) {
    return cameras[1];
  }

  return cameras.find((camera) => camera.deviceId === currentDeviceId) ?? cameras[0];
}

export function isIOSDevice(userAgent: string, platform: string, maxTouchPoints: number): boolean {
  return /iPad|iPhone|iPod/i.test(userAgent) || (platform === 'MacIntel' && maxTouchPoints > 1);
}

export function barcodeVideoConstraints(deviceId?: string | null): MediaTrackConstraints {
  return {
    ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }),
    width: { ideal: 1920 },
    height: { ideal: 1080 },
    frameRate: { ideal: 30 },
  };
}

function cameraScore(label: string): number {
  const normalized = label.toLocaleLowerCase();
  let score = 0;

  if (/\b(back|rear|environment)\b/.test(normalized)) {
    score += 200;
  }
  if (/\b(front|user|facetime)\b/.test(normalized)) {
    score -= 1000;
  }
  if (/ultra[\s-]?wide|\b0[.,]5\s*x?\b/.test(normalized)) {
    score -= 250;
  }
  if (/telephoto|periscope/.test(normalized)) {
    score -= 180;
  }
  if (/\b(triple|dual)\b/.test(normalized)) {
    score -= 100;
  }
  if (/\b(back|rear) camera\b|wide[\s-]?angle|\bmain camera\b/.test(normalized)) {
    score += 160;
  }

  return score;
}
