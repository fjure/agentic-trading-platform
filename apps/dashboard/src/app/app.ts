import { ChangeDetectionStrategy, Component, inject, signal, DestroyRef } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet, NavigationEnd } from '@angular/router';
import { BreakpointObserver } from '@angular/cdk/layout';
import { MatSidenav, MatSidenavModule } from '@angular/material/sidenav';
import { MatButtonModule } from '@angular/material/button';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { legacyLinkProblem } from './app.routes';
@Component({
  selector: 'atl-root', imports: [RouterOutlet, RouterLink, RouterLinkActive, MatSidenavModule, MatButtonModule],
  templateUrl: './app.html', styleUrl: './app.css', changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  readonly router = inject(Router);
  readonly narrow = signal(false);
  readonly linkProblem = signal<string | null>(null);
  readonly navigation = [['/home', 'Mein Überblick'], ['/portfolio', 'Portfolio'], ['/strategies', 'Strategien'], ['/tasks', 'AI-Aufgaben'], ['/activity', 'Aktivität'], ['/operations', 'Daten und Betrieb']];
  private previousPath = '';
  async closeNavigation(drawer: MatSidenav): Promise<void> {
    if (!this.narrow()) return;
    await drawer.close();
    (document.querySelector<HTMLElement>('main h1') ?? document.querySelector<HTMLElement>('main h2') ?? document.querySelector<HTMLElement>('main'))?.focus();
  }
  constructor() {
    const destroy = inject(DestroyRef);
    inject(BreakpointObserver).observe('(max-width: 1023px)').pipe(takeUntilDestroyed(destroy)).subscribe(value => this.narrow.set(value.matches));
    const validate = () => this.linkProblem.set(legacyLinkProblem(new URL(location.href).searchParams, location.hash.slice(1)));
    validate();
    this.router.events.pipe(takeUntilDestroyed(destroy)).subscribe(event => {
      if (!(event instanceof NavigationEnd)) return;
      validate();
      if (event.urlAfterRedirects.split('?')[0] !== this.previousPath) {
        this.previousPath = event.urlAfterRedirects.split('?')[0];
        setTimeout(() => (document.querySelector<HTMLElement>('main h1') ?? document.querySelector<HTMLElement>('main h2') ?? document.querySelector<HTMLElement>('main'))?.focus(), 0);
      }
    });
  }
}
