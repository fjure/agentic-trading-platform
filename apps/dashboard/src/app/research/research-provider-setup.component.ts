import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { timeout } from 'rxjs';
import { AgentProviderSetupService } from '../api/generated/control-api/api/agentProviderSetup.service';
import { AgentCredentialStatusV1 } from '../api/generated/control-api/model/agentCredentialStatusV1';
import { AgentProviderId } from '../api/generated/control-api/model/agentProviderId';

type Action = 'save' | 'rotate' | 'revoke';
type SetupDialog = { provider: AgentProviderId; action: Action; status: AgentCredentialStatusV1 };
type SetupOutcome = 'updated' | 'uncertain' | 'sessionLost';
const LABELS: Record<Action, string> = {
  save: 'Schlüssel speichern',
  rotate: 'Schlüssel ersetzen',
  revoke: 'Schlüssel lokal widerrufen',
};
const PROVIDERS = { OPENAI: 'OpenAI', ANTHROPIC: 'Anthropic' };

@Component({
  selector: 'atl-research-credential-dialog',
  imports: [MatButtonModule, MatDialogModule, MatFormFieldModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: ['mat-form-field { display: block; width: 100%; } code { overflow-wrap: anywhere; }'],
  template: `
    <h2 mat-dialog-title>{{ title }}</h2>
    <form #setupForm (submit)="$event.preventDefault(); submit(setupForm)" autocomplete="off">
      <mat-dialog-content>
        <p>Einmalcode im privaten Terminal des Setup-Dienstes erzeugen.</p>
        <p><code>python -m services.setup.research_app activate {{ data.provider }} {{ data.action }}</code></p>
        @if (data.action === 'revoke') {
          <p>Der lokale Widerruf sperrt diese Generation. Den Schlüssel beim Provider separat widerrufen.</p>
        } @else {
          <p>Die neue Generation bleibt bis zur gesonderten Freigabe für Research gesperrt.</p>
          <mat-form-field appearance="outline">
            <mat-label>Provider-Schlüssel</mat-label>
            <input matInput name="credential" type="password" required maxlength="2048"
              autocomplete="off" spellcheck="false" autocapitalize="none" [disabled]="pending()" />
          </mat-form-field>
        }
        <mat-form-field appearance="outline">
          <mat-label>Einmalcode für Setup</mat-label>
          <input matInput name="grant" type="password" required pattern="[A-Za-z0-9_-]{43}"
            maxlength="43" autocomplete="off" spellcheck="false" autocapitalize="none" [disabled]="pending()" />
        </mat-form-field>
        @if (pending()) { <p role="status">Setup-Anfrage läuft.</p> }
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" mat-dialog-close [disabled]="pending()">Abbrechen</button>
        <button mat-flat-button type="submit" [disabled]="pending()">{{ label }}</button>
      </mat-dialog-actions>
    </form>
  `,
})
export class ResearchCredentialDialog {
  readonly data = inject<SetupDialog>(MAT_DIALOG_DATA);
  private readonly dialog = inject<MatDialogRef<ResearchCredentialDialog, SetupOutcome>>(MatDialogRef);
  private readonly api = inject(AgentProviderSetupService);
  private readonly destroyRef = inject(DestroyRef);

  readonly pending = signal(false);
  readonly label = LABELS[this.data.action];
  readonly title = `${PROVIDERS[this.data.provider]} · ${this.label}`;

  submit(form: HTMLFormElement): void {
    const { action, provider, status } = this.data;
    if (this.pending() || !form.reportValidity()) return;
    const version = status.credentialVersion;
    const generation = status.credentialGeneration;
    if (action !== 'save' && generation === undefined) return;

    const grant = (form.elements.namedItem('grant') as HTMLInputElement).value;
    const credential = (form.elements.namedItem('credential') as HTMLInputElement | null)?.value ?? '';
    form.reset();
    this.pending.set(true);
    this.dialog.disableClose = true;
    const request = action === 'save'
      ? this.api.saveResearchSetupCredential(grant, provider, { expectedCredentialVersion: version, credential })
      : action === 'rotate'
        ? this.api.rotateResearchSetupCredential(grant, provider, {
          expectedCredentialVersion: version, expectedCredentialGeneration: generation!, credential,
        })
        : this.api.revokeResearchSetupCredential(grant, provider, {
          expectedCredentialVersion: version, credentialGeneration: generation!,
        });
    request.pipe(timeout(15_000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => this.dialog.close('updated'),
      error: (error: unknown) =>
        this.dialog.close(error instanceof HttpErrorResponse && error.status === 401 ? 'sessionLost' : 'uncertain'),
    });
  }
}

@Component({
  selector: 'atl-research-provider-setup',
  imports: [MatButtonModule, MatCardModule, MatDialogModule, MatFormFieldModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [`
    :host { display: block; margin-block: 1rem; }
    mat-form-field { min-width: 13rem; }
    .actions { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
    h2 { margin-top: 0; }
  `],
  template: `
    <mat-card appearance="outlined">
      <mat-card-content>
        <section aria-labelledby="research-provider-setup-title">
          <h2 id="research-provider-setup-title">Research-Schlüssel</h2>
          <div class="actions">
            <mat-form-field appearance="outline">
              <mat-label>Research-Provider</mat-label>
              <select matNativeControl #providerSelect [value]="provider()"
                (change)="selectProvider(providerSelect.value)" [disabled]="loading() || dialogOpen()">
                <option value="OPENAI">OpenAI</option>
                <option value="ANTHROPIC">Anthropic</option>
              </select>
            </mat-form-field>
            <button mat-stroked-button type="button" [disabled]="loading() || dialogOpen()" (click)="refresh()">
              Schlüsselstatus abrufen
            </button>
          </div>
          @if (loading()) { <p role="status">Schlüsselstatus wird geladen.</p> }
          @if (message()) { <p role="alert">{{ message() }}</p> }
          @if (status(); as current) {
            <p role="status">{{ labels[current.state] }} · Version {{ current.credentialVersion }}
              @if (current.credentialGeneration !== undefined) { · Generation {{ current.credentialGeneration }} }
            </p>
            @if (current.state === 'REVOKED') {
              <p>Providerwiderruf {{ current.providerRevocationState === 'CONFIRMED' ? 'bestätigt' : 'unbestätigt' }}.</p>
            }
            <div class="actions">
              @if (current.state === 'NOT_CONFIGURED') {
                <button mat-stroked-button type="button" [disabled]="dialogOpen()" (click)="open('save')">
                  Schlüssel speichern
                </button>
              } @else if (current.credentialGeneration !== undefined) {
                <button mat-stroked-button type="button" [disabled]="dialogOpen()" (click)="open('rotate')">
                  Schlüssel ersetzen
                </button>
                @if (current.state !== 'REVOKED') {
                  <button mat-stroked-button type="button" [disabled]="dialogOpen()" (click)="open('revoke')">
                    Schlüssel lokal widerrufen
                  </button>
                }
              }
            </div>
          }
        </section>
      </mat-card-content>
    </mat-card>
  `,
})
export class ResearchProviderSetupComponent {
  private readonly api = inject(AgentProviderSetupService);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);
  readonly sessionLost = output<void>();

  readonly provider = signal<AgentProviderId>('OPENAI');
  readonly status = signal<AgentCredentialStatusV1 | null>(null);
  readonly loading = signal(false);
  readonly dialogOpen = signal(false);
  readonly message = signal('');
  readonly labels: Record<AgentCredentialStatusV1.StateEnum, string> = {
    NOT_CONFIGURED: 'Schlüssel fehlt', STORED: 'Gespeichert, gesperrt',
    VERIFIED: 'Verbindung geprüft, Freigabe ausstehend', ACTIVE: 'Freigegeben',
    REVOKED: 'Lokal widerrufen',
  };

  selectProvider(value: string): void {
    if ((value !== 'OPENAI' && value !== 'ANTHROPIC') || this.loading() || this.dialogOpen()) return;
    this.provider.set(value);
    this.status.set(null);
    this.message.set('');
  }

  refresh(): void {
    if (this.loading() || this.dialogOpen()) return;
    this.loadStatus('');
  }

  open(action: Action): void {
    const current = this.status();
    if (!current || this.loading() || this.dialogOpen()) return;
    this.dialogOpen.set(true);
    const ref = this.dialog.open<ResearchCredentialDialog, SetupDialog, SetupOutcome>(ResearchCredentialDialog, {
      data: { provider: this.provider(), action, status: current },
    });
    const unregister = this.destroyRef.onDestroy(() => ref.close());
    ref.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe(outcome => {
      unregister();
      this.dialogOpen.set(false);
      if (outcome === 'sessionLost') {
        this.status.set(null);
        this.sessionLost.emit();
      } else if (outcome) {
        this.loadStatus(outcome === 'uncertain'
          ? 'Setup-Anfrage ungeklärt. Status prüfen und für eine neue Aktion einen neuen Einmalcode verwenden.'
          : '');
      }
    });
  }

  private loadStatus(message: string): void {
    this.loading.set(true);
    this.status.set(null);
    this.message.set(message);
    this.api.getResearchSetupCredential(this.provider()).pipe(
      timeout(15_000), takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: status => {
        this.status.set(status);
        this.loading.set(false);
      },
      error: (error: unknown) => {
        this.loading.set(false);
        if (error instanceof HttpErrorResponse && error.status === 401) this.sessionLost.emit();
        else this.message.set('Schlüsselstatus nicht verfügbar. Lokalen Setup-Dienst prüfen.');
      },
    });
  }
}
