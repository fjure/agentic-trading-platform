import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { AppComponent } from './app/app';
import { BASE_PATH } from './app/api/generated/control-api/variables';

bootstrapApplication(AppComponent, {
  providers: [provideHttpClient(), { provide: BASE_PATH, useValue: '' }],
}).catch(() => {
  document.body.textContent = 'Die Oberfläche konnte nicht gestartet werden.';
});
