import { inject } from '@angular/core';
import { Router, Routes } from '@angular/router';
const supervisor = () => import('./supervisor/supervisor.component').then(m => m.SupervisorComponent);
const prototype = () => import('./experience/prototype.component').then(m => m.PrototypeComponent);
export const legacyKeys = ['researchIdea', 'researchApproval', 'researchReport', 'researchReportVersion', 'quantExperiment', 'quantBatch', 'quantReport', 'backtestAssessment', 'backtestRun', 'backtestReport', 'readinessAssessment'] as const;
export function legacyLinkProblem(params: URLSearchParams, fragment: string): string | null {
  const selected = legacyKeys.filter(key => params.has(key));
  if (selected.some(key => params.getAll(key).length !== 1 || !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$/.test(params.get(key) ?? ''))) return 'Ungültige oder mehrfach gebundene Objektkennung. Bitte den ursprünglichen Link prüfen.';
  if (params.has('researchReportVersion') && (!params.has('researchReport') || !/^[1-9]\d{0,8}$/.test(params.get('researchReportVersion') ?? ''))) return 'Die Berichtversion benötigt einen eindeutigen Bericht und eine positive Versionsnummer.';
  if (params.has('researchReport') && !params.has('researchReportVersion')) return 'Der Researchbericht benötigt seine ursprüngliche Versionsbindung.';
  if (params.has('quantReport') && !params.has('quantBatch')) return 'Der Quantbericht benötigt seine ursprüngliche Batchbindung.';
  // Companion.quantLink and selectPaper preserve research provenance. These combinations
  // are supported by the existing read components, so retain every identity unchanged.
  const researchOrQuant = selected.some(k => k.startsWith('research') || k.startsWith('quant'));
  if ((params.has('readinessAssessment') && selected.length > 1) || (researchOrQuant && selected.some(k => k.startsWith('backtest')))) return 'Dieser Link enthält widersprüchliche Objektauswahlen. Bitte den ursprünglichen Link prüfen.';
  return null;
}
export const routes: Routes = [
  { path: '', pathMatch: 'full', loadComponent: supervisor, title: 'Daten und Betrieb · Agentic Trading Lab', canActivate: [route => {
    if (legacyKeys.some(key => route.queryParamMap.has(key)) || route.fragment === 'alpaca-paper') {
      return inject(Router).createUrlTree(['/operations'], { queryParams: route.queryParams, fragment: route.fragment ?? undefined });
    }
    return true;
  }] },
  { path: 'operations', loadComponent: supervisor, title: 'Daten und Betrieb · Agentic Trading Lab' },
  ...[
    ['onboarding', 'onboarding', 'Willkommen'], ['home', 'home', 'Mein Überblick'],
    ['portfolio/insights', 'insights', 'Portfolio verstehen'], ['portfolio', 'portfolio', 'Mein Portfolio'],
    ['strategies/:strategyId', 'strategy', 'Strategie verstehen'], ['strategies', 'strategies', 'Strategien entdecken'],
    ['tasks/:taskId', 'task', 'Auftrag verstehen'], ['tasks', 'tasks', 'AI-Aufgaben'],
    ['paper/orders/new', 'order', 'Paperanforderung vorbereiten'], ['activity', 'activity', 'Aktivität'],
    ['**', 'missing', 'Seite nicht verfügbar'],
  ].map(([path, screen, title]) => ({ path, loadComponent: prototype, data: { screen, title }, title: `${title} · Prototyp` })),
];
