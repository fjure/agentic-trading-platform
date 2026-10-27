import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import type { ReadinessTrustDimensions, ReadinessTrustResult } from '../api/generated/control-api/model/models';

@Component({
  selector: 'atl-readiness-trust',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="table-scroll"><table aria-label="Unabhängige Vertrauensdimensionen">
      <thead><tr><th>Prüfung</th><th>Status</th><th>Methode / Regel</th><th>Grund / Referenzen</th></tr></thead>
      <tbody>@for (item of rows(); track item.label) {
        <tr><th scope="row">{{ item.label }}</th><td>{{ item.result.status }}</td>
          <td>{{ item.result.method }} / {{ item.result.ruleVersion }}</td><td>{{ item.result.reason }}
          @for (reference of item.result.references; track $index) {
            <p>{{ reference.kind }} · {{ reference.id }} · {{ reference.revision ?? 'keine Revision' }}</p>
          }</td></tr>
      }</tbody>
    </table></div>
  `,
  styles: `:host{display:block}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.85rem}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #c4c7ce;padding:.6rem;overflow-wrap:anywhere}`,
})
export class ReadinessTrustComponent {
  readonly trust = input.required<ReadinessTrustDimensions>();
  rows(): { label: string; result: ReadinessTrustResult }[] {
    const value = this.trust();
    return [
      { label: 'Integrität', result: value.integrity },
      { label: 'Quellenauthentizität', result: value.sourceAuthenticity },
      { label: 'Zeitliche Authentizität', result: value.temporalAuthenticity },
      { label: 'Vollständigkeit', result: value.completeness },
      { label: 'Fachliche Qualität', result: value.domainQuality },
    ];
  }
}
