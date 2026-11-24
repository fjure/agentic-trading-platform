import { expect, test, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

const screens = [
  ['/prototype/onboarding', 'Willkommen'], ['/home', 'Mein Überblick'], ['/portfolio', 'Mein Portfolio'],
  ['/portfolio/insights', 'Portfolio verstehen'], ['/strategies', 'Strategien entdecken'],
  ['/strategies/trend-long-flat?version=1', 'Strategie verstehen'], ['/tasks', 'AI-Aufgaben'],
  ['/tasks/local-analysis', 'Auftrag verstehen'], ['/paper/orders/new', 'Paperanforderung vorbereiten'], ['/activity', 'Aktivität'],
];
function recordApi(page: Page) {
  const calls: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) calls.push(`${request.method()} ${request.url()}`); });
  return calls;
}
async function click(page: Page, name: string) { await page.getByRole('link', { name, exact: true }).click(); }

test('all ten prototype screens deep link and reload without API, persistence, or object substitution', async ({ page }) => {
  const calls = recordApi(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const [path, title] of screens) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
    await expect(page.getByText('Klickbarer Prototyp v1 · synthetische Daten', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  }
  expect(calls).toEqual([]);
  expect(errors).toEqual([]);
  for (const path of ['/strategies/local-analysis', '/tasks/trend-long-flat', '/strategies/trend-long-flat?version=2', '/portfolio?context=PAPER', '/portfolio?demoRunId=foreign', '/paper/orders/new?strategy=foreign&version=1']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'Objekt, Version oder Quellkontext nicht verfügbar' })).toBeVisible();
    await expect(page.getByText('1 000.00 USD', { exact: true })).toHaveCount(0);
  }
});

test('J1 and J2 explicitly choose demo and confirm local analysis, retaining stable synthetic source on reload', async ({ page }) => {
  const calls = recordApi(page);
  await page.goto('/prototype/onboarding');
  await page.getByRole('button', { name: 'Weiter zu freiwilligen Angaben' }).click();
  await page.getByRole('button', { name: 'Präferenzen überspringen' }).click();
  await click(page, 'Diese Demo wählen und zum Überblick');
  await expect(page).toHaveURL(/demoRunId=prototype-demo-01/);
  await click(page, 'Bestand ansehen');
  await click(page, 'Portfolio untersuchen');
  await expect(page.getByText(/Für eine zuverlässige Gewichtung fehlen Bewertungsdaten/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulierten Analysebefund öffnen' })).toBeDisabled();
  await page.getByRole('checkbox', { name: /Diese Demoquelle/ }).check();
  await click(page, 'Simulierten Analysebefund öffnen');
  await expect(page.getByText('PORTFOLIO_ANALYSIS / analysis-prototype-01', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('DEMO / prototype-demo-01 / snapshot-v1 / ledger-v1', { exact: true })).toBeVisible();
  await click(page, 'Analyse im Verlauf ansehen');
  await click(page, 'Analysequelle öffnen');
  expect(calls).toEqual([]);
});

test('J3/J5 full evidence-eligibility-plan-preview-risk-confirm-status-activity flow has no API effect', async ({ page }) => {
  const calls = recordApi(page);
  await page.goto('/strategies');
  await click(page, 'Strategie verstehen');
  await click(page, 'Historische Evidenz ansehen');
  await expect(page.getByText(/2025-01-01T00:00:00Z/)).toBeVisible();
  await click(page, 'Aktuelle Paper-Eignung getrennt prüfen');
  await page.getByLabel('Sicherheitsfall simulieren').selectOption('stale');
  await expect(page.getByText('Gesperrt: keine Bestätigung und keine automatische Entsperrung.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Separaten menschlichen Paperplan vorbereiten' })).toHaveCount(0);
  await page.getByLabel('Sicherheitsfall simulieren').selectOption('ready');
  await click(page, 'Separaten menschlichen Paperplan vorbereiten');
  await page.getByLabel('Menge (exakter Decimalwert)').fill('2');
  await page.getByRole('button', { name: 'Plan lokal prüfen (Simulation)' }).click();
  await expect(page.getByText(/Andere Eingaben werden nicht ersetzt/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Gebundene Beispielvorschau öffnen' })).toHaveCount(0);
  await page.getByLabel('Menge (exakter Decimalwert)').fill('1');
  await page.getByRole('button', { name: 'Plan lokal prüfen (Simulation)' }).click();
  await click(page, 'Gebundene Beispielvorschau öffnen');
  await click(page, 'Lesende Risk-Vorprüfung ansehen');
  await expect(page).toHaveURL(/strategy=trend-long-flat/);
  await page.getByLabel('Risk- und Recoveryfall').selectOption('unknown');
  await expect(page.getByRole('link', { name: 'Zur menschlichen Bestätigung' })).toHaveCount(0);
  await page.getByLabel('Risk- und Recoveryfall').selectOption('ready');
  await click(page, 'Zur menschlichen Bestätigung');
  await expect(page.getByText(/Ihre Bestätigung würde eine Anforderung an die Paper-Infrastruktur übermitteln/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Diese Paperanforderung simuliert bestätigen' })).toBeDisabled();
  await page.getByRole('checkbox', { name: /Ich bestätige genau/ }).check();
  await page.getByRole('button', { name: 'Diese Paperanforderung simuliert bestätigen' }).click();
  await click(page, 'Angenommenen Beispielvorgang ansehen');
  await expect(page.getByRole('heading', { name: 'Bestehender Vorgang · PENDING' })).toBeVisible();
  await click(page, 'Vorgang in Aktivität ansehen');
  await click(page, 'Bestehenden Vorgang öffnen');
  await expect(page.getByRole('heading', { name: 'Bestehender Vorgang · UNKNOWN' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Bestehenden Vorgang erneut prüfen' }).click();
  await expect(page.getByText(/Denselben synthetischen Vorgang/)).toBeVisible();
  expect(calls).toEqual([]);
});

test('J4 preserves research semantics and requires separate approximation acceptance without persisting free text', async ({ page }) => {
  const calls = recordApi(page);
  await page.goto('/tasks/tech-recovery');
  const question = page.getByLabel('Originalfrage (nur flüchtiger Prototypentwurf)');
  await question.fill('Synthetische geänderte Frage');
  await page.getByRole('checkbox', { name: 'Offene Fragen und Original im Beispiel bestätigen' }).check();
  await expect(page.getByRole('heading', { name: 'Testbarkeit: INSUFFICIENT_DATA / REQUIRES_NEW_STRATEGY' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ersatzhypothese als separaten Entwurf ansehen' })).toHaveCount(0);
  await page.getByRole('checkbox', { name: 'Diese andere Frage bewusst als neuen Entwurf annehmen' }).check();
  await question.fill('Eine zweite synthetische Frage');
  await expect(page.getByRole('checkbox', { name: 'Offene Fragen und Original im Beispiel bestätigen' })).not.toBeChecked();
  await expect(page.getByRole('link', { name: 'Ersatzhypothese als separaten Entwurf ansehen' })).toHaveCount(0);
  await page.getByRole('checkbox', { name: 'Offene Fragen und Original im Beispiel bestätigen' }).check();
  await expect(page.getByRole('checkbox', { name: 'Diese andere Frage bewusst als neuen Entwurf annehmen' })).not.toBeChecked();
  await page.getByRole('checkbox', { name: 'Diese andere Frage bewusst als neuen Entwurf annehmen' }).check();
  await click(page, 'Ersatzhypothese als separaten Entwurf ansehen');
  await expect(page.getByText(/Keine Bestätigung für einzelne Techaktien/)).toBeVisible();
  await page.goBack();
  await page.reload();
  await expect(question).toHaveValue('Erholen sich Techaktien nach starken Kursrückgängen binnen einer Woche?');
  expect(page.url()).not.toContain('Synthetische');
  expect(calls).toEqual([]);
});

test('all ten screens expose recovery states; new paper approval remains blocked offline', async ({ page }) => {
  for (const [path] of screens) {
    for (const [state, title] of [['loading','Beispiel wird geladen'], ['empty','Keine Einträge im Beispiel'], ['offline','Verbindung unterbrochen'], ['stale','Daten veraltet'], ['conflict','Version hat sich geändert'], ['unauthorized','Sitzung abgelaufen'], ['unavailable','Quelle nicht verfügbar']]) {
      await page.goto(path + (path.includes('?') ? '&' : '?') + 'view=' + state);
      await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Bestätigtes Beispiel erneut lesen' })).toBeVisible();
    }
  }
  await page.goto('/paper/orders/new?phase=confirm&view=offline');
  await expect(page.getByRole('button', { name: 'Diese Paperanforderung simuliert bestätigen' })).toHaveCount(0);
});

test('J6 all nine failure scenarios name a safe next step and keep same UNKNOWN operation', async ({ page }) => {
  const calls = recordApi(page);
  await page.goto('/activity');
  await page.getByText('J6 · Alle Pflichtfälle sicher wiederaufnehmen', { exact: true }).click();
  for (const value of ['provider','budget','stale','rights','unsupported','grant','paused','session','unknown']) {
    await page.getByLabel('Fehlerfall').selectOption(value);
    await expect(page.locator('details').filter({ has: page.getByLabel('Fehlerfall') }).getByRole('status')).not.toBeEmpty();
  }
  await click(page, 'Denselben ungeklärten Vorgang lesen');
  await expect(page).toHaveURL(/phase=status&status=UNKNOWN/);
  await page.reload();
  await expect(page.getByText('prototype-operation-01 / prototype-key-01', { exact: true })).toBeVisible();
  expect(calls).toEqual([]);
});

test('mobile nav Escape restores focus; routes focus heading; primary screens do not overflow at320/360/768/1440', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto('/home');
  const menu = page.getByRole('button', { name: 'Menü', exact: true });
  await menu.click();
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(menu).toBeFocused();
  await menu.click();
  await page.getByRole('navigation', { name: 'Hauptnavigation', exact: true }).getByRole('link', { name: 'Strategien', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Strategien entdecken', exact: true })).toBeFocused();
  for (const width of [320,360,768,1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [path] of screens) {
      await page.goto(path);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      expect(overflow, `${path} at ${width}px`).toBe(false);
    }
  }
});

test('legacy root adapters preserve all supported identity bindings and never post', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') writes.push(request.url()); });
  for (const suffix of ['#alpaca-paper', '?researchIdea=idea-1', '?researchApproval=approval-1', '?researchReport=report-1&researchReportVersion=2', '?quantExperiment=experiment-1', '?quantExperiment=experiment-1&quantBatch=batch-1&quantReport=report-1', '?researchIdea=idea-1&quantExperiment=experiment-1', '?researchIdea=idea-1#alpaca-paper', '?backtestRun=run-1', '?backtestReport=report-1', '?readinessAssessment=assessment-1']) {
    await page.goto('/' + suffix);
    await expect(page).toHaveURL('/operations' + suffix);
    await page.reload();
    await expect(page).toHaveURL('/operations' + suffix);
    await expect(page.getByRole('heading', { name: 'Link kann nicht sicher geöffnet werden' })).toHaveCount(0);
  }
  for (const suffix of ['?researchIdea=idea-1&researchIdea=idea-2', '?researchReport=report-1&researchReportVersion=-1', '?quantReport=report-1', '?researchIdea=idea-1&backtestRun=run-1', '?researchIdea=https://foreign.invalid']) {
    await page.goto('/' + suffix);
    await expect(page.getByRole('heading', { name: 'Link kann nicht sicher geöffnet werden' })).toBeVisible();
  }
  expect(writes).toEqual([]);
});
