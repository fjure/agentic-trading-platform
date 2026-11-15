import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { AgentResearchService } from '../api/generated/control-api/api/agentResearch.service';
import type {
  AgentProviderCapabilityV1, AgentProviderProbeRequestV1, AgentProviderSmokePreviewV1,
} from '../api/generated/control-api/model/models';

@Component({
  selector: 'atl-research-provider-smoke',
  imports: [DatePipe, MatButtonModule, MatCardModule],
  templateUrl: './research-provider-smoke.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ResearchProviderSmokeComponent {
  private readonly api = inject(AgentResearchService);
  private readonly destroy = inject(DestroyRef);
  private expiryTimer?: ReturnType<typeof setTimeout>;
  readonly providers = signal<AgentProviderCapabilityV1[]>([]);
  readonly loading = signal(false);
  readonly message = signal('');
  readonly preview = signal<{
    provider: AgentProviderCapabilityV1['binding']['provider'];
    binding: AgentProviderProbeRequestV1;
    quote: AgentProviderSmokePreviewV1;
  } | null>(null);

  constructor() {
    this.destroy.onDestroy(() => clearTimeout(this.expiryTimer));
  }

  refresh(message = ''): void {
    this.clearPreview();
    this.providers.set([]);
    this.message.set(message);
    this.loading.set(true);
    this.api.listAgentProviders(undefined, 50, 'body', false, { transferCache: false })
      .pipe(takeUntilDestroyed(this.destroy)).subscribe({
        next: page => { this.providers.set(page.items); this.loading.set(false); },
        error: () => this.fail('Providerstatus konnte nicht geladen werden.'),
      });
  }

  canPreview(profile: AgentProviderCapabilityV1): boolean {
    return profile.credential.state === 'STORED' &&
      profile.credential.credentialGeneration !== undefined &&
      profile.availability !== 'REVOKED' && profile.availability !== 'DISABLED' &&
      !['QUEUED', 'IN_FLIGHT', 'OUTCOME_UNKNOWN', 'VERIFIED'].includes(profile.probeState);
  }

  prepare(profile: AgentProviderCapabilityV1): void {
    const generation = profile.credential.credentialGeneration;
    if (this.loading() || !this.canPreview(profile) || generation === undefined) return;
    this.clearPreview();
    this.message.set('');
    this.loading.set(true);
    const { provider, profileId, profileGeneration, modelGeneration } = profile.binding;
    const binding = { profileId, profileGeneration, modelGeneration, credentialGeneration: generation };
    this.api.prepareAgentProviderSmoke(provider, crypto.randomUUID(), binding, 'body', false,
      { transferCache: false }).pipe(takeUntilDestroyed(this.destroy)).subscribe({
        next: quote => {
          this.loading.set(false);
          const remaining = Date.parse(quote.expiresAt) - Date.now();
          if (!Number.isFinite(remaining) || remaining <= 0) {
            this.fail('Die Testvorschau ist abgelaufen.');
            return;
          }
          this.preview.set({ provider, binding, quote });
          this.expiryTimer = setTimeout(() => {
            this.clearPreview();
            this.message.set('Die Testvorschau ist abgelaufen.');
          }, remaining);
        },
        error: () => this.fail('Testvorschau gesperrt. Prüfe den aktuellen Providerstatus.'),
      });
  }

  confirm(): void {
    const pending = this.preview();
    if (!pending || this.loading() || pending.quote.blockedReason ||
        !(Date.parse(pending.quote.expiresAt) > Date.now())) return;
    const { grantId, commitment } = pending.quote;
    this.clearPreview();
    this.loading.set(true);
    this.api.probeAgentProvider(pending.provider, grantId, { ...pending.binding, grantId, commitment },
      'body', false, { transferCache: false }).pipe(takeUntilDestroyed(this.destroy)).subscribe({
        next: receipt => this.refresh(`Teststatus: ${receipt.status}`),
        error: () => this.fail(
          'Die Bestätigung ist fehlgeschlagen oder der Ausgang ist unklar. Lade den Status neu.',
        ),
      });
  }

  clearPreview(): void {
    clearTimeout(this.expiryTimer);
    this.preview.set(null);
  }

  private fail(message: string): void {
    this.loading.set(false);
    this.message.set(message);
  }
}
