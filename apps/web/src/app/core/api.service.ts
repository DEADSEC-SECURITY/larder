import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  ActivityEvent,
  BarcodeResolution,
  BrandRow,
  CategoryRow,
  CheckoutResult,
  CommandRow,
  GroceryResponse,
  HouseholdSettings,
  IntakeBatch,
  IntakeConfirmPayload,
  IntegrationRow,
  InventoryResponse,
  OverviewResponse,
  ProductDetail,
  ProductKindUpper,
  ProductRow,
  ProductStockGroupMatches,
  QuickAddPayload,
  RoutingRule,
  RoutingRulesResponse,
  RuleBehavior,
  RuleMatchType,
  ScannerMode,
  ScannersResponse,
  ShoppingScanResult,
  StockGroupDetail,
  StockGroupSummary,
  StorageLocationRow,
  StoreAssignment,
  StoreRow,
  TaxClass,
  TaxClassesResponse,
  TaxLocationRow,
  TaxRateRow,
  UnitRow,
} from './models';

const BASE = '/api/v1/web';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  overview(): Observable<OverviewResponse> {
    return this.http.get<OverviewResponse>(`${BASE}/overview`);
  }

  inventory(): Observable<InventoryResponse> {
    return this.http.get<InventoryResponse>(`${BASE}/inventory`);
  }

  products(): Observable<{ products: ProductRow[] }> {
    return this.http.get<{ products: ProductRow[] }>(`${BASE}/products`);
  }

  productDetail(barcode: string): Observable<ProductDetail> {
    return this.http.get<ProductDetail>(`${BASE}/products/${encodeURIComponent(barcode)}`);
  }

  quickAdd(payload: QuickAddPayload): Observable<{ barcode: string; created: number }> {
    return this.http.post<{ barcode: string; created: number }>(`${BASE}/quick-add`, payload);
  }

  /**
   * Normalize a scanned or typed barcode, registering the product (and queueing
   * enrichment) when the catalogue has never seen it.
   */
  resolveBarcode(barcode: string): Observable<BarcodeResolution> {
    return this.http.post<BarcodeResolution>(`${BASE}/barcodes/resolve`, { barcode });
  }

  updateUnit(
    id: string,
    payload: {
      storeId?: string | null;
      storageLocationId?: string | null;
      expirationDate?: string | null;
      price?: number | null;
      /** Requires price in the same payload; null = explicit no tax. */
      taxRateId?: string | null;
      /** True = price is the final total and the subtotal is backed out. */
      priceIncludesTax?: boolean;
    },
  ): Observable<UnitRow> {
    return this.http.patch<UnitRow>(`${BASE}/units/${id}`, payload);
  }

  removeUnit(id: string): Observable<{ id: string; status: string }> {
    return this.http.post<{ id: string; status: string }>(`${BASE}/units/${id}/remove`, {});
  }

  wasteUnit(id: string): Observable<{ id: string; status: string }> {
    return this.http.post<{ id: string; status: string }>(`${BASE}/units/${id}/waste`, {});
  }

  /** Correct a unit's status — undo a remove/waste or reclassify one as the other. */
  setUnitStatus(
    id: string,
    status: 'in_stock' | 'removed' | 'trashed',
  ): Observable<{ id: string; status: string }> {
    return this.http.post<{ id: string; status: string }>(`${BASE}/units/${id}/status`, {
      status,
    });
  }

  setStockPolicy(barcode: string, reorderPoint: number, targetStock: number): Observable<unknown> {
    return this.http.put(`${BASE}/products/${encodeURIComponent(barcode)}/stock-policy`, {
      reorderPoint,
      targetStock,
    });
  }

  // Stock groups — one generic need satisfied by any accepted member product.
  stockGroups(includeArchived = false): Observable<{ stockGroups: StockGroupSummary[] }> {
    return this.http.get<{ stockGroups: StockGroupSummary[] }>(
      `${BASE}/stock-groups${includeArchived ? '?include_archived=true' : ''}`,
    );
  }

  createStockGroup(payload: {
    seedBarcode: string;
    name?: string;
    reorderPoint?: number;
    targetStock?: number;
  }): Observable<{ created: boolean; stockGroup: StockGroupSummary }> {
    return this.http.post<{ created: boolean; stockGroup: StockGroupSummary }>(
      `${BASE}/stock-groups`,
      payload,
    );
  }

  stockGroupDetail(groupId: string): Observable<StockGroupDetail> {
    return this.http.get<StockGroupDetail>(`${BASE}/stock-groups/${groupId}`);
  }

  updateStockGroup(
    groupId: string,
    payload: {
      name?: string;
      requiredTerms?: string[];
      excludedTerms?: string[];
      allowedForms?: string[];
      categoryIds?: string[];
      autoMatchThreshold?: number;
      reviewThreshold?: number;
      active?: boolean;
    },
  ): Observable<StockGroupSummary> {
    return this.http.patch<StockGroupSummary>(`${BASE}/stock-groups/${groupId}`, payload);
  }

  /** Both values zero removes the policy. */
  setStockGroupPolicy(
    groupId: string,
    reorderPoint: number,
    targetStock: number,
  ): Observable<unknown> {
    return this.http.put(`${BASE}/stock-groups/${groupId}/stock-policy`, {
      reorderPoint,
      targetStock,
    });
  }

  addStockGroupMember(
    groupId: string,
    payload: { barcode: string; learn?: boolean; migratePolicy?: boolean },
  ): Observable<StockGroupSummary> {
    return this.http.post<StockGroupSummary>(`${BASE}/stock-groups/${groupId}/members`, payload);
  }

  removeStockGroupMember(
    groupId: string,
    barcode: string,
    remember = true,
  ): Observable<StockGroupSummary> {
    return this.http.delete<StockGroupSummary>(
      `${BASE}/stock-groups/${groupId}/members/${encodeURIComponent(barcode)}?remember=${remember}`,
    );
  }

  decideStockGroupCandidate(
    groupId: string,
    barcode: string,
    payload: { accepted: boolean; learn?: boolean; migratePolicy?: boolean },
  ): Observable<StockGroupDetail> {
    return this.http.post<StockGroupDetail>(
      `${BASE}/stock-groups/${groupId}/candidates/${encodeURIComponent(barcode)}/decision`,
      payload,
    );
  }

  productStockGroupMatches(barcode: string): Observable<ProductStockGroupMatches> {
    return this.http.get<ProductStockGroupMatches>(
      `${BASE}/products/${encodeURIComponent(barcode)}/stock-group-matches`,
    );
  }

  refreshProductStockGroupMatches(barcode: string): Observable<ProductStockGroupMatches> {
    return this.http.post<ProductStockGroupMatches>(
      `${BASE}/products/${encodeURIComponent(barcode)}/stock-group-matches/refresh`,
      {},
    );
  }

  autoMatchStockGroups(): Observable<{
    evaluated: number;
    matched: number;
    review: number;
    unmatched: number;
  }> {
    return this.http.post<{
      evaluated: number;
      matched: number;
      review: number;
      unmatched: number;
    }>(`${BASE}/stock-groups/auto-match`, {});
  }

  retryEnrichment(barcode: string): Observable<unknown> {
    return this.http.post(`${BASE}/products/${encodeURIComponent(barcode)}/retry-enrichment`, {});
  }

  deleteProduct(
    barcode: string,
  ): Observable<{ barcode: string; deleted: boolean; archived: boolean }> {
    return this.http.delete<{ barcode: string; deleted: boolean; archived: boolean }>(
      `${BASE}/products/${encodeURIComponent(barcode)}`,
    );
  }

  restoreProduct(barcode: string): Observable<unknown> {
    return this.http.post(`${BASE}/products/${encodeURIComponent(barcode)}/restore`, {});
  }

  bulkUnits(payload: {
    itemIds?: string[];
    barcodes?: string[];
    action: 'update' | 'remove' | 'waste';
    setStore?: boolean;
    storeId?: string | null;
    setStorageLocation?: boolean;
    storageLocationId?: string | null;
    expiration?: 'date' | 'estimate';
    expirationDate?: string;
    setPrice?: boolean;
    price?: number | null;
    /** Requires setPrice: true; null = explicit no tax. */
    taxRateId?: string | null;
    /** True = price is the final total and the subtotal is backed out. */
    priceIncludesTax?: boolean;
  }): Observable<{ matched: number; updated: number; skipped: number }> {
    return this.http.post<{ matched: number; updated: number; skipped: number }>(
      `${BASE}/units/bulk`,
      payload,
    );
  }

  // The shopping list lives at /shopping; the legacy /grocery aliases return
  // the same data but new code should not call them.
  shopping(): Observable<GroceryResponse> {
    return this.http.get<GroceryResponse>(`${BASE}/shopping`);
  }

  addShoppingItem(payload: {
    barcode?: string;
    name?: string;
    /** Generic manual demand — cannot be combined with barcode or name. */
    stockGroupId?: string;
    quantity: number;
    productKind?: ProductKindUpper;
  }): Observable<unknown> {
    return this.http.post(`${BASE}/shopping`, payload);
  }

  updateShoppingItem(
    barcode: string,
    payload: {
      checked?: boolean;
      quantity?: number;
      preferredStoreId?: string | null;
      /** Per-unit shelf price; null falls back to the lowest observed price. */
      unitPrice?: number | null;
    },
  ): Observable<unknown> {
    return this.http.patch(`${BASE}/shopping/${encodeURIComponent(barcode)}`, payload);
  }

  /** Removes a row for good — an automatic one stays off the list too. */
  deleteShoppingItem(identifier: string): Observable<unknown> {
    return this.http.delete(`${BASE}/shopping/${encodeURIComponent(identifier)}`);
  }

  /** Ticks one unit off the list for a barcode scanned at the shelf. */
  scanShoppingItem(barcode: string, addIfMissing = true): Observable<ShoppingScanResult> {
    return this.http.post<ShoppingScanResult>(`${BASE}/shopping/scan`, {
      barcode,
      addIfMissing,
    });
  }

  /** Turns everything in the cart into inventory. */
  checkoutShopping(storeId?: string | null): Observable<CheckoutResult> {
    return this.http.post<CheckoutResult>(`${BASE}/shopping/checkout`, {
      storeId: storeId ?? null,
    });
  }

  brands(): Observable<{ brands: BrandRow[] }> {
    return this.http.get<{ brands: BrandRow[] }>(`${BASE}/brands`);
  }

  createBrand(payload: { name: string; aliases?: string[] }): Observable<BrandRow> {
    return this.http.post<BrandRow>(`${BASE}/brands`, payload);
  }

  updateBrand(
    id: string,
    payload: { name?: string; aliases?: string[]; active?: boolean },
  ): Observable<BrandRow> {
    return this.http.patch<BrandRow>(`${BASE}/brands/${id}`, payload);
  }

  setProductBrands(
    barcode: string,
    brandIds: string[],
    primaryBrandId?: string,
  ): Observable<unknown> {
    return this.http.put(`${BASE}/products/${encodeURIComponent(barcode)}/brands`, {
      brandIds,
      primaryBrandId,
    });
  }

  routingRules(): Observable<RoutingRulesResponse> {
    return this.http.get<RoutingRulesResponse>(`${BASE}/store-routing-rules`);
  }

  createRoutingRule(payload: {
    name?: string;
    matchType: RuleMatchType;
    productBarcode?: string;
    brandId?: string;
    categoryId?: string;
    storeId: string;
    behavior: RuleBehavior;
    priority?: number;
    enabled?: boolean;
  }): Observable<RoutingRule> {
    return this.http.post<RoutingRule>(`${BASE}/store-routing-rules`, payload);
  }

  updateRoutingRule(
    id: string,
    payload: {
      name?: string;
      storeId?: string;
      behavior?: RuleBehavior;
      priority?: number;
      enabled?: boolean;
    },
  ): Observable<RoutingRule> {
    return this.http.patch<RoutingRule>(`${BASE}/store-routing-rules/${id}`, payload);
  }

  deleteRoutingRule(id: string): Observable<unknown> {
    return this.http.delete(`${BASE}/store-routing-rules/${id}`);
  }

  productStoreAssignment(
    barcode: string,
  ): Observable<{ barcode: string; assignment: StoreAssignment }> {
    return this.http.get<{ barcode: string; assignment: StoreAssignment }>(
      `${BASE}/products/${encodeURIComponent(barcode)}/store-assignment`,
    );
  }

  clearShoppingChecks(): Observable<unknown> {
    return this.http.post(`${BASE}/shopping/clear-checks`, {});
  }

  categories(): Observable<{ categories: CategoryRow[] }> {
    return this.http.get<{ categories: CategoryRow[] }>(`${BASE}/categories`);
  }

  createCategory(payload: {
    name: string;
    parentId?: string;
    days?: number;
    productKind?: ProductKindUpper;
    defaultStorageLocationId?: string | null;
  }): Observable<unknown> {
    return this.http.post(`${BASE}/categories`, payload);
  }

  updateCategory(
    id: string,
    payload: {
      days?: number | null;
      productKind?: ProductKindUpper;
      defaultStorageLocationId?: string | null;
    },
  ): Observable<unknown> {
    return this.http.patch(`${BASE}/categories/${encodeURIComponent(id)}`, payload);
  }

  storageLocations(): Observable<{ storageLocations: StorageLocationRow[] }> {
    return this.http.get<{ storageLocations: StorageLocationRow[] }>(`${BASE}/storage-locations`);
  }

  createStorageLocation(name: string): Observable<StorageLocationRow> {
    return this.http.post<StorageLocationRow>(`${BASE}/storage-locations`, { name });
  }

  updateStorageLocation(
    id: string,
    payload: { name?: string; active?: boolean },
  ): Observable<StorageLocationRow> {
    return this.http.patch<StorageLocationRow>(`${BASE}/storage-locations/${id}`, payload);
  }

  updateProduct(
    barcode: string,
    payload: {
      name?: string;
      brand?: string | null;
      productKind?: ProductKindUpper;
      description?: string | null;
      manufacturer?: string | null;
      packageSize?: string | null;
      material?: string | null;
      variant?: string | null;
      /** null = back to Uncategorized. */
      categoryId?: string | null;
      defaultStorageLocationId?: string | null;
    },
  ): Observable<unknown> {
    return this.http.patch(`${BASE}/products/${encodeURIComponent(barcode)}`, payload);
  }

  stores(): Observable<{ stores: StoreRow[] }> {
    return this.http.get<{ stores: StoreRow[] }>(`${BASE}/stores`);
  }

  createStore(name: string): Observable<StoreRow> {
    return this.http.post<StoreRow>(`${BASE}/stores`, { name });
  }

  updateStore(id: string, payload: { active?: boolean; name?: string }): Observable<unknown> {
    return this.http.patch(`${BASE}/stores/${id}`, payload);
  }

  ensureControlBarcode(storeId: string): Observable<{ storeId: string; payload: string }> {
    return this.http.post<{ storeId: string; payload: string }>(
      `${BASE}/stores/${storeId}/control-barcode`,
      {},
    );
  }

  /** Scanning the returned CMD:LOC barcode sets the scanner's storage location. */
  ensureLocationControlBarcode(
    locationId: string,
  ): Observable<{ storageLocationId: string; payload: string }> {
    return this.http.post<{ storageLocationId: string; payload: string }>(
      `${BASE}/storage-locations/${locationId}/control-barcode`,
      {},
    );
  }

  commands(): Observable<{ commands: CommandRow[] }> {
    return this.http.get<{ commands: CommandRow[] }>(`${BASE}/commands`);
  }

  queuePrintJob(payload: {
    kind: 'product' | 'control' | 'generic';
    value: string;
    title: string;
    subtitle?: string;
    copies?: number;
  }): Observable<{ id: string; status: string }> {
    return this.http.post<{ id: string; status: string }>(`${BASE}/print-jobs`, payload);
  }

  activity(limit = 100): Observable<{ events: ActivityEvent[] }> {
    return this.http.get<{ events: ActivityEvent[] }>(`${BASE}/activity?limit=${limit}`);
  }

  scanners(): Observable<ScannersResponse> {
    return this.http.get<ScannersResponse>(`${BASE}/scanners`);
  }

  setScannerMode(id: string, mode: ScannerMode | null): Observable<unknown> {
    return this.http.post(`${BASE}/scanners/${id}/mode`, { mode });
  }

  setScannerStore(id: string, storeId: string | null): Observable<unknown> {
    return this.http.post(`${BASE}/scanners/${id}/store`, { storeId });
  }

  intakeBatches(
    status: 'open' | 'confirmed' | 'dismissed' | 'all' = 'open',
  ): Observable<{ batches: IntakeBatch[] }> {
    return this.http.get<{ batches: IntakeBatch[] }>(`${BASE}/intake-batches?status=${status}`);
  }

  intakeBatch(id: string): Observable<IntakeBatch> {
    return this.http.get<IntakeBatch>(`${BASE}/intake-batches/${id}`);
  }

  confirmIntakeBatch(id: string, payload: IntakeConfirmPayload): Observable<IntakeBatch> {
    return this.http.post<IntakeBatch>(`${BASE}/intake-batches/${id}/confirm`, payload);
  }

  dismissIntakeBatch(id: string): Observable<IntakeBatch> {
    return this.http.post<IntakeBatch>(`${BASE}/intake-batches/${id}/dismiss`, {});
  }

  integrations(): Observable<{ integrations: IntegrationRow[] }> {
    return this.http.get<{ integrations: IntegrationRow[] }>(`${BASE}/integrations`);
  }

  setIntegrationKey(provider: string, apiKey: string | null): Observable<IntegrationRow> {
    return this.http.put<IntegrationRow>(`${BASE}/integrations/${provider}`, { apiKey });
  }

  testZiptax(address?: string): Observable<{
    ok: boolean;
    source: string | null;
    address: string;
    normalizedAddress: string | null;
    rate: number;
    percentage: number;
  }> {
    return this.http.post<{
      ok: boolean;
      source: string | null;
      address: string;
      normalizedAddress: string | null;
      rate: number;
      percentage: number;
    }>(`${BASE}/integrations/ziptax/test`, address ? { address } : {});
  }

  taxClasses(): Observable<TaxClassesResponse> {
    return this.http.get<TaxClassesResponse>(`${BASE}/tax-classes`);
  }

  taxLocations(includeInactive = false): Observable<{ locations: TaxLocationRow[] }> {
    return this.http.get<{ locations: TaxLocationRow[] }>(
      `${BASE}/tax-locations${includeInactive ? '?include_inactive=true' : ''}`,
    );
  }

  createTaxLocation(payload: {
    label: string;
    addressLine1?: string;
    addressLine2?: string;
    city: string;
    state: string;
    postalCode: string;
    rates: { taxClass: TaxClass; label?: string; rate: number }[];
  }): Observable<TaxLocationRow> {
    return this.http.post<TaxLocationRow>(`${BASE}/tax-locations`, payload);
  }

  lookupTaxLocation(payload: {
    label: string;
    addressLine1: string;
    addressLine2?: string;
    city: string;
    state: string;
    postalCode: string;
    taxClasses: TaxClass[];
  }): Observable<TaxLocationRow> {
    return this.http.post<TaxLocationRow>(`${BASE}/tax-locations/lookup`, payload);
  }

  updateTaxLocation(
    id: string,
    payload: { label?: string; active?: boolean },
  ): Observable<TaxLocationRow> {
    return this.http.patch<TaxLocationRow>(`${BASE}/tax-locations/${id}`, payload);
  }

  updateTaxRate(
    id: string,
    payload: { label?: string; rate?: number; active?: boolean },
  ): Observable<TaxRateRow> {
    return this.http.patch<TaxRateRow>(`${BASE}/tax-rates/${id}`, payload);
  }

  deleteTaxLocation(id: string): Observable<{ id: string; active: boolean }> {
    return this.http.delete<{ id: string; active: boolean }>(`${BASE}/tax-locations/${id}`);
  }

  settings(): Observable<HouseholdSettings> {
    return this.http.get<HouseholdSettings>(`${BASE}/settings`);
  }

  updateSettings(payload: Partial<HouseholdSettings>): Observable<HouseholdSettings> {
    return this.http.put<HouseholdSettings>(`${BASE}/settings`, payload);
  }

  exportInventoryUrl(): string {
    return `${BASE}/export/inventory.csv`;
  }

  recalculateEstimates(): Observable<{ updated: number }> {
    return this.http.post<{ updated: number }>(`${BASE}/maintenance/recalculate-estimates`, {});
  }

  resetInventory(): Observable<{ removed: number }> {
    return this.http.post<{ removed: number }>(`${BASE}/maintenance/reset-inventory`, {});
  }
}
