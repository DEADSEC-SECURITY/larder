import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./screens/overview/overview').then((m) => m.OverviewScreen),
    title: 'Larder',
  },
  {
    path: 'inventory',
    loadComponent: () => import('./screens/inventory/inventory').then((m) => m.InventoryScreen),
    title: 'Inventory · Larder',
  },
  {
    path: 'inventory/:barcode',
    loadComponent: () =>
      import('./screens/product-detail/product-detail').then((m) => m.ProductDetailScreen),
    title: 'Product · Larder',
  },
  {
    path: 'shopping',
    loadComponent: () => import('./screens/shopping/shopping').then((m) => m.ShoppingScreen),
    title: 'Shopping · Larder',
  },
  {
    path: 'products',
    loadComponent: () => import('./screens/products/products').then((m) => m.ProductsScreen),
    title: 'Products · Larder',
  },
  {
    path: 'activity',
    loadComponent: () => import('./screens/activity/activity').then((m) => m.ActivityScreen),
    title: 'Activity · Larder',
  },
  {
    path: 'settings',
    loadComponent: () =>
      import('./screens/settings/settings-layout').then((m) => m.SettingsLayout),
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./screens/settings/settings').then((m) => m.SettingsScreen),
        title: 'Settings · Larder',
      },
      {
        path: 'commands',
        loadComponent: () =>
          import('./screens/commands/commands').then((m) => m.CommandsScreen),
        title: 'Commands · Larder',
      },
      {
        path: 'stores',
        loadComponent: () => import('./screens/stores/stores').then((m) => m.StoresScreen),
        title: 'Stores · Larder',
      },
      {
        path: 'storage',
        loadComponent: () =>
          import('./screens/storage/storage').then((m) => m.StorageLocationsScreen),
        title: 'Storage locations · Larder',
      },
      {
        path: 'integrations',
        loadComponent: () =>
          import('./screens/integrations/integrations').then((m) => m.IntegrationsScreen),
        title: 'Integrations · Larder',
      },
      {
        path: 'taxes',
        loadComponent: () => import('./screens/taxes/taxes').then((m) => m.TaxesScreen),
        title: 'Tax locations · Larder',
      },
      {
        path: 'stock-groups',
        loadComponent: () =>
          import('./screens/stock-groups/stock-groups').then((m) => m.StockGroupsScreen),
        title: 'Stock groups · Larder',
      },
      {
        path: 'rules',
        loadComponent: () =>
          import('./screens/routing/routing').then((m) => m.RoutingScreen),
        title: 'Shopping rules · Larder',
      },
      {
        path: 'brands',
        loadComponent: () => import('./screens/brands/brands').then((m) => m.BrandsScreen),
        title: 'Brands · Larder',
      },
      {
        path: 'categories',
        loadComponent: () =>
          import('./screens/categories/categories').then((m) => m.CategoriesScreen),
        title: 'Categories & rules · Larder',
      },
    ],
  },
  // Legacy top-level paths (relative redirects keep query params, e.g.
  // /routing?rule=<id> deep links).
  { path: 'grocery', redirectTo: 'shopping' },
  { path: 'stores', redirectTo: 'settings/stores' },
  { path: 'routing', redirectTo: 'settings/rules' },
  { path: 'brands', redirectTo: 'settings/brands' },
  { path: 'categories', redirectTo: 'settings/categories' },
  { path: '**', redirectTo: '' },
];
