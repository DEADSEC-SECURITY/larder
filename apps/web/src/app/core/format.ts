// Shared formatting helpers — expiry labeling mirrors the design's expInfo().

import { ExpirationKind, ProductKind, ProductKindUpper, StockGroupEvidence } from './models';

/** User-facing name for the stock-group scope; "stock group" itself only
 *  appears on management/settings screens. */
export const GENERIC_SCOPE_LABEL = 'Any compatible brand or size';

/** Translate match evidence into concise copy — confidence is supporting
 *  information, not the primary label. */
export function stockGroupEvidenceCopy(evidence: StockGroupEvidence | null): string[] {
  if (!evidence) {
    return [];
  }
  const lines: string[] = [];
  if (evidence.seedProduct) {
    lines.push('Original product');
  }
  if (evidence.manual) {
    lines.push('Added manually');
  }
  if (evidence.formConflict && evidence.productForms?.length) {
    lines.push(`Different form: ${evidence.productForms.join(', ')}`);
  }
  if (evidence.requiredCoverage === 1) {
    lines.push('Same product name');
  } else if (evidence.requiredCoverage != null && evidence.requiredCoverage < 1) {
    lines.push('Name only partly matches');
  }
  if (evidence.categoryOverlap?.length) {
    const category = evidence.categoryOverlap[0].split(':').pop()?.replace(/-/g, ' ');
    lines.push(`Same ${category} category`);
  }
  if (evidence.termPrecision != null && evidence.termPrecision < 1 && !evidence.formConflict) {
    lines.push('Extra words in the name');
  }
  return lines;
}

/** Response value → request enum + display label, in menu order. */
export const PRODUCT_KINDS: { kind: ProductKind; upper: ProductKindUpper; label: string }[] = [
  { kind: 'food', upper: 'FOOD', label: 'Food & Beverage' },
  { kind: 'household', upper: 'HOUSEHOLD', label: 'Household' },
  { kind: 'personal_care', upper: 'PERSONAL_CARE', label: 'Personal Care' },
  { kind: 'medical', upper: 'MEDICAL', label: 'Health & Medical' },
  { kind: 'pet', upper: 'PET', label: 'Pet Supplies' },
  { kind: 'office', upper: 'OFFICE', label: 'Office' },
  { kind: 'other', upper: 'OTHER', label: 'Other' },
];

export function productKindLabel(kind: ProductKind | null | undefined): string {
  return PRODUCT_KINDS.find((k) => k.kind === kind)?.label ?? 'Other';
}

export function productKindUpper(kind: ProductKind): ProductKindUpper {
  return (PRODUCT_KINDS.find((k) => k.kind === kind)?.upper ?? 'OTHER') as ProductKindUpper;
}

export type Tone = 'danger' | 'warn' | 'ok' | 'chip' | 'faint' | 'none';

export interface ExpiryInfo {
  label: string;
  tone: Tone;
  days: number | null;
}

export function money(value: number | null | undefined, currency = 'USD'): string {
  if (value == null) {
    return '—';
  }
  const symbol = currency === 'EUR' ? '€' : currency === 'GBP' ? '£' : '$';
  return `${symbol}${value.toFixed(2)}`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) {
    return '—';
  }
  return new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

export function shortDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return '—';
  }
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function relativeTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) {
    return '—';
  }
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) {
    return 'now';
  }
  if (minutes < 60) {
    return `${minutes}m`;
  }
  if (minutes < 1440) {
    return `${Math.round(minutes / 60)}h`;
  }
  return `${Math.round(minutes / 1440)}d`;
}

export function expiryInfo(
  date: string | null | undefined,
  daysLeft: number | null | undefined,
  windowDays: number,
): ExpiryInfo {
  if (!date || daysLeft == null) {
    return { label: 'No date', tone: 'none', days: null };
  }
  if (daysLeft < 0) {
    return { label: `Expired ${-daysLeft}d`, tone: 'danger', days: daysLeft };
  }
  if (daysLeft === 0) {
    return { label: 'Today', tone: 'warn', days: daysLeft };
  }
  if (daysLeft === 1) {
    return { label: 'Tomorrow', tone: 'warn', days: daysLeft };
  }
  if (daysLeft <= windowDays) {
    return { label: `${daysLeft} days`, tone: 'chip', days: daysLeft };
  }
  return { label: shortDate(date), tone: 'faint', days: daysLeft };
}

export function daysBetween(iso: string, today = new Date()): number {
  const target = new Date(`${iso.slice(0, 10)}T12:00:00`);
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 12);
  return Math.round((target.getTime() - base.getTime()) / 86400000);
}

export function pillClass(tone: Tone): string {
  return `pill-${tone}`;
}

export function kindLabel(kind: ExpirationKind): string {
  return kind === 'confirmed' ? 'confirmed' : kind === 'estimated' ? 'estimated' : 'no date';
}

export function formatBarcodeDigits(barcode: string): string {
  return barcode.replace(/(\d{4})(?=\d)/g, '$1 ');
}

export function assignmentSourceLabel(source: string, reason?: string | null): string {
  switch (source) {
    case 'manual':
      return 'Selected manually';
    case 'product_rule':
      return 'Product rule';
    case 'brand_rule':
      return reason?.startsWith('Brand rule: ')
        ? `${reason.slice('Brand rule: '.length)} rule`
        : 'Brand rule';
    case 'category_rule':
      return 'Category rule';
    case 'price_history':
      return 'Lowest observed price';
    default:
      return '';
  }
}

export function countdown(expiresAt: string | null, now: Date): string {
  if (!expiresAt) {
    return '—';
  }
  const seconds = Math.max(
    0,
    Math.floor((new Date(expiresAt).getTime() - now.getTime()) / 1000),
  );
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}
