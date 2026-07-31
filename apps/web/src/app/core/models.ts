// API contracts — mirror src/larder/webapi responses.

export type IdStatus = 'ok' | 'pending' | 'failed';
/** Lowercase in responses; requests send the uppercase form. */
export type ProductKind =
  'food' | 'household' | 'personal_care' | 'medical' | 'pet' | 'office' | 'other';
export type ProductKindUpper =
  'FOOD' | 'HOUSEHOLD' | 'PERSONAL_CARE' | 'MEDICAL' | 'PET' | 'OFFICE' | 'OTHER';
export type ExpirationKind = 'confirmed' | 'estimated' | 'unknown';
export type UnitStatus = 'in_stock' | 'removed' | 'trashed';
export type RemovalPolicy = 'FEFO' | 'FIFO' | 'LIFO' | 'LEFO';
export type ScannerMode = 'ADD' | 'LIST' | 'REMOVE' | 'TRASH';
export type ActivityKind =
  'added' | 'removed' | 'wasted' | 'restored' | 'changed' | 'estimated' | 'ignored' | 'failed';

export interface CategoryRef {
  id: string;
  name: string;
  parentName: string | null;
}

export interface BrandRef {
  id: string;
  name: string;
}

export interface BrandRow {
  id: string;
  name: string;
  normalizedName: string;
  aliases: string[];
  active: boolean;
  productCount: number;
  routingRuleId: string | null;
}

export type AssignmentSource =
  'manual' | 'product_rule' | 'brand_rule' | 'category_rule' | 'price_history' | 'none';
export type RuleBehavior = 'prefer' | 'require';
export type RuleMatchType = 'product' | 'brand' | 'category';

export interface StoreAssignment {
  storeId: string | null;
  storeName: string | null;
  storeActive: boolean | null;
  source: AssignmentSource;
  reason: string | null;
  behavior: RuleBehavior | null;
  ruleId: string | null;
  warning: string | null;
}

export interface RoutingRule {
  id: string;
  name: string;
  matchType: RuleMatchType;
  match: { id: string | null; label: string; productCount: number };
  store: { id: string; code: string; name: string; active: boolean } | null;
  behavior: RuleBehavior;
  priority: number;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface RoutingRulesResponse {
  rules: RoutingRule[];
  precedence: string[];
}

export interface RuleRef {
  categoryId: string;
  categoryName: string;
  days: number;
}

export interface PolicyRef {
  reorderPoint: number;
  targetStock: number;
  /** Present when the policy lives on a stock group rather than the barcode. */
  scope?: 'stock_group';
  stockGroupId?: string;
}

/**
 * Stock groups: one generic household need ("Strawberries") satisfied by any
 * accepted member product. The backend owns all matching — the UI never
 * implements its own product-equivalence logic.
 */
export type StockGroupMemberSource = 'user' | 'automatic' | 'migration';

/** Embedded on products that are accepted members of a group. */
export interface ProductStockGroup {
  id: string;
  name: string;
  active: boolean;
  source: StockGroupMemberSource | null;
  confidence: number | null;
  scopeLabel: string;
  memberCount: number;
  /** Combined in-stock count across all members — the product root's inStock
   *  stays the exact barcode's own count. */
  inStock: number;
  policy: PolicyRef | null;
}

export interface StockGroupSummary {
  id: string;
  name: string;
  canonicalKey: string;
  productKind: ProductKind;
  representativeProductBarcode: string | null;
  requiredTerms: string[];
  excludedTerms: string[];
  allowedForms: string[];
  categoryIds: string[];
  ignoreBrand: boolean;
  ignorePackageSize: boolean;
  autoMatchThreshold: number;
  reviewThreshold: number;
  active: boolean;
  memberCount: number;
  inStock: number;
  pendingCandidateCount: number;
  policy: PolicyRef | null;
}

/** Match evidence — score components; translate to concise copy in the UI. */
export interface StockGroupEvidence {
  requiredCoverage?: number;
  termPrecision?: number;
  categoryOverlap?: string[];
  productForms?: string[];
  formConflict?: boolean;
  seedProduct?: boolean;
  manual?: boolean;
  [key: string]: unknown;
}

export interface StockGroupMember {
  barcode: string;
  name: string | null;
  brand: string | null;
  imageUrl: string | null;
  packageSize: string | null;
  productKind: ProductKind;
  source: StockGroupMemberSource;
  confidence: number | null;
  evidence: StockGroupEvidence | null;
}

export interface StockGroupCandidate {
  barcode: string;
  name: string | null;
  brand: string | null;
  imageUrl: string | null;
  packageSize: string | null;
  productKind: ProductKind;
  confidence: number;
  /** rejected = history, not an actionable review card. */
  status: 'pending' | 'rejected';
  evidence: StockGroupEvidence | null;
}

export interface StockGroupDetail extends StockGroupSummary {
  members: StockGroupMember[];
  candidates: StockGroupCandidate[];
}

/** Persisted match state for a product that is not yet an accepted member. */
export interface ProductStockGroupMatches {
  status: 'matched' | 'review' | 'none';
  stockGroup?: StockGroupSummary | null;
  suggestions: {
    stockGroupId: string;
    stockGroupName: string;
    confidence: number;
    evidence: StockGroupEvidence | null;
  }[];
  /** Only on the refresh endpoint's response. */
  evaluation?: {
    status: 'matched' | 'review' | 'no_match' | 'no_profile';
    stockGroupId: string | null;
    confidence: number | null;
  };
}

export interface SoonestExpiration {
  date: string;
  kind: ExpirationKind;
  daysLeft: number | null;
}

/** Response from POST /barcodes/resolve — a scanned or typed barcode. */
export interface BarcodeResolution {
  /** Canonical GTIN; a 12-digit UPC-A comes back zero-padded to 13. */
  barcode: string;
  /** Exactly what was scanned or typed, before normalization. */
  raw: string;
  barcodeFormat: string | null;
  /** True when this call registered the product and queued enrichment. */
  created: boolean;
  name: string | null;
  brand: string | null;
  idStatus: IdStatus;
  archived: boolean;
  inStock: number;
}

export interface ProductRow {
  barcode: string;
  name: string | null;
  productKind: ProductKind;
  description: string | null;
  manufacturer: string | null;
  packageSize: string | null;
  material: string | null;
  variant: string | null;
  identificationSource: string | null;
  defaultStorageLocationId: string | null;
  brand: string | null;
  brandRef: BrandRef | null;
  brands: BrandRef[];
  archived: boolean;
  idStatus: IdStatus;
  category: CategoryRef | null;
  rule: RuleRef | null;
  policy: PolicyRef | null;
  /** Non-null when this barcode is an accepted member of a stock group. */
  stockGroup: ProductStockGroup | null;
  inStock: number;
  soonestExpiration: SoonestExpiration | null;
  hasUnknownDate: boolean;
  lowStock: boolean;
  needsInfo: boolean;
  lowestPrice: number | null;
  lastAddedAt: string | null;
}

export interface InventoryResponse {
  windowDays: number;
  totalUnits: number;
  products: ProductRow[];
}

export type TaxClass =
  'GENERAL' | 'GROCERY' | 'CANDY' | 'SUPPLEMENT' | 'SOFT_DRINK' | 'PREPARED_FOOD';

export interface TaxClassInfo {
  id: TaxClass;
  label: string;
  taxabilityCode: string | null;
  providerPlan: 'all' | 'pro';
}

export interface TaxClassesResponse {
  provider: { name: string; configured: boolean; productRatesRequirePaidPlan: boolean };
  classes: TaxClassInfo[];
}

export interface TaxRateRow {
  id: string;
  taxClass: TaxClass;
  label: string;
  /** Decimal fraction — 0.08875 means 8.875%. */
  rate: number;
  percentage: number;
  taxabilityCode: string | null;
  source: string;
  fetchedAt: string | null;
  active: boolean;
}

export interface TaxLocationRow {
  id: string;
  label: string;
  addressLine1: string | null;
  addressLine2: string | null;
  city: string;
  state: string;
  postalCode: string;
  countryCode: string;
  normalizedAddress: string | null;
  provider: string;
  active: boolean;
  rates: TaxRateRow[];
}

/** Snapshot of the tax applied when a price was saved. Null fields = tax was never recorded (not 0%). */
export interface PriceBreakdown {
  subtotal: number | null;
  taxRate: number | null;
  taxAmount: number | null;
  total: number;
  taxRateId: string | null;
  taxLocationId: string | null;
  taxClass: TaxClass | null;
  taxSource: string | null;
}

export interface UnitRow {
  id: string;
  status: UnitStatus;
  dateAdded: string;
  removedAt: string | null;
  expirationDate: string | null;
  expirationKind: ExpirationKind;
  storeId: string | null;
  storeName: string | null;
  storageLocationId: string | null;
  storageLocationName: string | null;
  /** Final amount paid (tax included when recorded). */
  price: number | null;
  priceSubtotal: number | null;
  taxRate: number | null;
  taxAmount: number | null;
  taxRateId: string | null;
  taxLocationId: string | null;
  taxClass: TaxClass | null;
  taxSource: string | null;
  priceBreakdown: PriceBreakdown | null;
  currency: string | null;
}

export interface PriceStats {
  lowest: number;
  average: number;
  highest: number;
  byStore: { storeId: string; storeName: string; price: number }[];
}

export interface HistoryRow {
  storeId: string;
  storeName: string;
  observedAt: string;
  price: number;
  priceSubtotal: number | null;
  taxRate: number | null;
  taxAmount: number | null;
  currency: string;
}

export interface NutritionValues {
  energyKcal: number | null;
  fatG: number | null;
  saturatedFatG: number | null;
  carbohydratesG: number | null;
  sugarsG: number | null;
  fiberG: number | null;
  proteinG: number | null;
  saltG: number | null;
  sodiumG: number | null;
}

export interface Nutrition extends NutritionValues {
  basisAmount: 100;
  basisUnit: 'g' | 'ml';
  servingSize: string | null;
  servingAmount: number | null;
  servingUnit: 'g' | 'ml' | null;
  perServing: NutritionValues | null;
}

export interface ProviderRecord {
  provider: string;
  productType: string | null;
  disposition: string;
  httpStatus: number | null;
  error: string | null;
  fetchedAt: string;
}

export interface ProductDetail extends ProductRow {
  barcodeFormat: string;
  imageUrl: string | null;
  quantityText: string | null;
  offProductUrl: string | null;
  enrichmentStatus: string;
  enrichmentError: string | null;
  units: UnitRow[];
  history: HistoryRow[];
  priceStats: PriceStats | null;
  nutrition: Nutrition | null;
  /** Provider provenance — debug info, not primary product copy. */
  providerMetadata: Record<string, unknown> | null;
  /** Per-field origin; a field sourced USER is protected from provider enrichment. */
  fieldSources: Record<string, string> | null;
  providers: ProviderRecord[];
}

export interface GroceryItem {
  /** Branch UI on itemType, not the `group:` barcode prefix. */
  itemType: 'product' | 'stock_group';
  /** For stock-group rows this is the pseudo-barcode `group:<id>`; existing
   *  check/quantity/store actions accept it unchanged. */
  barcode: string;
  stockGroupId: string | null;
  name: string;
  productKind: ProductKind;
  brand: string | null;
  scopeLabel?: string | null;
  quantity: number;
  checked: boolean;
  /** Units already in the cart; each scan adds one, so a ×3 row needs three. */
  checkedQuantity: number;
  /** Member barcodes actually scanned — checkout stocks these, not a guess. */
  scannedBarcodes: string[];
  reason: string;
  memberBarcodes?: string[];
  memberCount?: number;
  inStock?: number;
  storeId: string | null;
  storeName: string | null;
  unitPrice: number | null;
  priceKnown: boolean;
  /** 'entered' = typed at the shelf, 'observed' = lowest price on record. */
  priceSource: 'entered' | 'observed' | null;
  storeAssignment: StoreAssignment;
}

export interface ShoppingScanResult {
  matched: boolean;
  /** True when the scan put a previously unlisted product on the list. */
  added: boolean;
  itemType?: 'product' | 'stock_group';
  /** Row key: a barcode, or `group:<id>` for a generic row. */
  identifier?: string;
  barcode: string;
  stockGroupId?: string | null;
  name: string | null;
  quantity?: number;
  checkedQuantity?: number;
  checked?: boolean;
}

export interface CheckoutResult {
  added: number;
  items: { barcode: string; name: string; created: number }[];
  /** Rows that could not be stocked, with the reason why. */
  skipped: { identifier: string; name: string; reason: string }[];
}

export interface GroceryResponse {
  items: GroceryItem[];
  estimatedUncheckedTotal: number;
  checkedTotal: number;
  currency: string;
}

export interface CategoryRow {
  id: string;
  name: string;
  parentId: string | null;
  parentName: string | null;
  productKind: ProductKind;
  defaultStorageLocationId: string | null;
  days: number | null;
  inheritedDays: number | null;
  productCount: number;
}

export interface StorageLocationRow {
  id: string;
  code: string;
  name: string;
  active: boolean;
  inStockUnits: number;
}

export interface StoreRow {
  id: string;
  code: string;
  name: string;
  active: boolean;
  productCount: number;
  recordCount: number;
  lastObservedAt: string | null;
  priceCoveragePct: number;
  controlPayload: string | null;
}

export interface ActivityEvent {
  at: string;
  kind: ActivityKind;
  product: string;
  barcode: string | null;
  detail: string | null;
  source: string;
}

export interface ScannerFeedRow {
  at: string;
  tag: string;
  ok: boolean;
  label: string;
  mode: string | null;
}

export interface CommandRow {
  payload: string;
  commandType:
    'SET_MODE' | 'CLEAR_MODE' | 'SET_STORE' | 'CLEAR_STORE' | 'SET_LOCATION' | 'CLEAR_LOCATION';
  mode: string | null;
  storeId: string | null;
  storeName: string | null;
  storeActive: boolean | null;
  storageLocationId: string | null;
  storageLocationName: string | null;
  storageLocationActive: boolean | null;
  enabled: boolean;
  description: string;
}

export interface ScannerRow {
  id: string;
  code: string;
  name: string;
  enabled: boolean;
  modeTimeoutSeconds: number;
  storeTimeoutSeconds: number;
  mode: ScannerMode | null;
  modeExpiresAt: string | null;
  storeId: string | null;
  storeName: string | null;
  storeExpiresAt: string | null;
  lastSeenAt: string | null;
  online: boolean;
  feed: ScannerFeedRow[];
}

export interface ScannersResponse {
  scanners: ScannerRow[];
  serverTime: string;
}

export type IntakeBatchStatus = 'open' | 'confirmed' | 'dismissed';

export interface IntakeBatch {
  id: string;
  status: IntakeBatchStatus;
  scannerId: string;
  scannerCode: string | null;
  scannerName: string | null;
  barcode: string;
  productName: string | null;
  brand: string | null;
  imageUrl: string | null;
  enrichmentStatus: string | null;
  quantity: number;
  editableQuantity: number;
  storeId: string | null;
  storeName: string | null;
  storageLocationId: string | null;
  storageLocationName: string | null;
  unitPrice: number | null;
  unitPriceSubtotal: number | null;
  taxRate: number | null;
  taxAmount: number | null;
  taxRateId: string | null;
  taxLocationId: string | null;
  taxClass: TaxClass | null;
  taxSource: string | null;
  priceBreakdown: PriceBreakdown | null;
  currency: string | null;
  expirationDate: string | null;
  expirationKind: ExpirationKind;
  createdAt: string;
  updatedAt: string;
  lastScanAt: string;
  finalizedAt: string | null;
}

export interface IntakeConfirmPayload {
  /** Omit a field to retain its scan-time default; send null to clear it. */
  storeId?: string | null;
  /** Price for one item; mutually exclusive with totalPrice. */
  unitPrice?: number | null;
  /** Combined price for every editable item; the API splits it per item. */
  totalPrice?: number | null;
  expirationDate?: string | null;
  /** Requires unitPrice or totalPrice in the same payload; null = explicit no tax. */
  taxRateId?: string | null;
  /** True = the entered price includes tax and its subtotal is backed out. */
  priceIncludesTax?: boolean;
  storageLocationId?: string | null;
}

export interface IntegrationRow {
  provider: string;
  label: string;
  configured: boolean;
  /** Where the effective key lives: saved from the UI or server environment. */
  source: 'db' | 'env' | null;
  keyMask: string | null;
  baseUrl?: string;
}

export interface HouseholdSettings {
  removalPolicy: RemovalPolicy;
  defaultTaxRateId: string | null;
  expiringWindowDays: number;
  autoGrocery: boolean;
  requireStoreOnManualAdd: boolean;
  badgeExpiringNav: boolean;
  retryFailedLookups: boolean;
  audibleScanConfirmation: boolean;
  /** Zero disables intake-popup auto-save. */
  intakePopupTimeoutSeconds: number;
  /** Maximum gap for repeated scans to join one intake batch. */
  intakeScanGroupTimeoutSeconds: number;
  scannerModeTimeoutSeconds: number;
  /** Shared by store and storage-location scanner context. */
  scannerContextTimeoutSeconds: number;
  scannerOfflineAfterSeconds: number;
  toastDurationSeconds: number;
  confirmationTimeoutSeconds: number;
  liveUpdateDebounceMs: number;
  fallbackRefreshSeconds: number;
}

export interface OverviewResponse {
  windowDays: number;
  metrics: {
    unitsInStock: number;
    stockedProducts: number;
    expiringUnits: number;
    expiredUnits: number;
    groceryCount: number;
    groceryEstimate: number;
    attentionCount: number;
    awaitingIdentification: number;
  };
  expiring: {
    barcode: string;
    name: string;
    brand: string | null;
    storeName: string | null;
    inStock: number;
    date: string;
    kind: ExpirationKind;
    daysLeft: number | null;
  }[];
  attention: {
    pendingIdentification: number;
    failedLookups: number;
    missingPrices: number;
    missingRules: number;
    offlineScanners: string[];
  };
  groceryPreview: {
    items: {
      name: string;
      quantity: number;
      unitPrice: number | null;
      storeName?: string | null;
      storeAssignmentSource?: string;
    }[];
    moreCount: number;
    totalCount: number;
    estimate: number;
  };
  currency: string;
}

export interface QuickAddPayload {
  barcode?: string;
  name?: string;
  brand?: string;
  productKind?: ProductKindUpper;
  description?: string;
  manufacturer?: string;
  packageSize?: string;
  material?: string;
  variant?: string;
  /** Omitted = product/category default location applies. */
  storageLocationId?: string;
  quantity: number;
  storeId?: string;
  newStoreName?: string;
  priceTotal?: number;
  /** Requires priceTotal; null = explicit no tax, omitted = household default. */
  taxRateId?: string | null;
  /** True = priceTotal already includes tax. */
  priceIncludesTax?: boolean;
  addedOn?: string;
  expirationDate?: string;
}
