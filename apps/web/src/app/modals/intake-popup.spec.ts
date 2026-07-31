import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ApiService } from '../core/api.service';
import { AppStateService } from '../core/app-state.service';
import { ClockService } from '../core/clock.service';
import { IntakeConfirmPayload } from '../core/models';
import { ToastService } from '../core/toast.service';
import { IntakePopup } from './intake-popup';

describe('IntakePopup price entry', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [IntakePopup],
      providers: [
        {
          provide: ApiService,
          useValue: {
            intakeBatches: () => of({ batches: [] }),
          },
        },
        {
          provide: AppStateService,
          useValue: {
            version: signal(0),
            stores: signal([]),
            activeStorageLocations: signal([]),
            taxLocations: signal([]),
            settings: signal(null),
            intakePopupTimeoutMs: () => 0,
          },
        },
        { provide: ClockService, useValue: { now: () => new Date() } },
        { provide: ToastService, useValue: { show: vi.fn() } },
      ],
    }).compileComponents();
  });

  function payload(component: IntakePopup): IntakeConfirmPayload {
    return (
      component as unknown as {
        buildPayload: () => IntakeConfirmPayload;
      }
    ).buildPayload();
  }

  it('sends a combined receipt amount as totalPrice', () => {
    const fixture = TestBed.createComponent(IntakePopup);
    const component = fixture.componentInstance;

    component.priceMode.set('total');
    component.touchPrice('49.90');

    expect(payload(component)).toEqual({
      totalPrice: 49.9,
      priceIncludesTax: false,
    });
    fixture.destroy();
  });

  it('keeps per-item entry backward compatible', () => {
    const fixture = TestBed.createComponent(IntakePopup);
    const component = fixture.componentInstance;

    component.priceMode.set('unit');
    component.touchPrice('4.99');

    expect(payload(component)).toEqual({
      unitPrice: 4.99,
      priceIncludesTax: false,
    });
    fixture.destroy();
  });
});
