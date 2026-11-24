import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter, withInMemoryScrolling } from '@angular/router';
import { AppComponent } from './app/app';
import { routes } from './app/app.routes';
import { BASE_PATH } from './app/api/generated/control-api/variables';
bootstrapApplication(AppComponent, {
  providers: [provideHttpClient(), provideRouter(routes, withInMemoryScrolling({ scrollPositionRestoration: 'enabled' })), { provide: BASE_PATH, useValue: '' }],
}).catch(() => { document.body.textContent = 'Die Oberfläche konnte nicht gestartet werden.'; });
