import { expect, test, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

async function openCompanion(page: Page) {
  await page.request.post('/__test/reset');
  await page.goto('/');
  await page.getByLabel('Aktivierungscode').fill('synthetic-local-activation-code-000001');
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  await page.getByRole('button', { name: 'Research Companion öffnen' }).press('Enter');
  return page.locator('atl-agent-companion');
}

test('the Guided UI keeps the local question, preview, and send decision separate across reload', async ({ page }) => {
  const base = '/api/v1/research/agent';
  const original = 'Erholen sich Aktien nach unerwartet schwachen Quartalszahlen?';
  const question = 'Was gilt in dieser Frage als schwache Quartalszahl?';
  const idea = {
    id: 'idea-1', version: 1, title: 'Synthetische Researchfrage', originalText: original,
    status: 'DRAFT', dialogueId: 'dialogue-1', hypothesis: null, uncertainties: [],
    experimentIds: [], experimentDraftIds: [], evidence: [],
  };
  const processing = {
    state: 'AWAITING_DECISION', delivery: 'NOT_SENT', costState: 'NOT_INCURRED', turnVersion: 1,
    receipt: undefined as undefined | { receiptId: string },
  };
  const turn = {
    turnId: 'turn-1', turnVersion: 1, dialogueVersion: 2, processing,
    localQuestions: [{ id: 'question-1', field: 'TRIGGER', question }],
    proposal: undefined as undefined | Record<string, unknown>,
  };
  const interpretedTrigger = 'Quartalszahlen unter den Erwartungen';
  let saved = false;
  let confirmed = false;
  let sessionRevoked = false;
  let previewBlocked = true;
  let followUp = false;
  let turns: Array<typeof turn> = [];
  const posts: Array<{ path: string; body: unknown }> = [];
  await page.route('**/api/v1/research/agent/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.slice(base.length);
    if (request.method() === 'POST') posts.push({ path, body: request.postDataJSON() });
    switch (path) {
      case '/ideas':
        if (sessionRevoked) return route.fulfill({ status: 401, json: { code: 'SESSION_REQUIRED' } });
        if (request.method() === 'POST') {
          saved = true;
          return route.fulfill({ status: 201, json: idea });
        }
        return route.fulfill({ json: { items: saved ? [idea] : [], nextCursor: null } });
      case '/ideas/idea-1':
        return route.fulfill({ json: idea });
      case '/dialogues/dialogue-1':
        return route.fulfill({ json: { id: 'dialogue-1', ideaId: idea.id,
          version: followUp ? 5 : confirmed ? 4 : turn.proposal ? 3 : 1 + turns.length,
          ideaVersion: confirmed ? 2 : 1, turns, nextCursor: null, confirmedAssumptions: [],
          confirmedFields: confirmed ? [{ field: 'TRIGGER', value: interpretedTrigger }] : [],
        } });
      case '/dialogues/dialogue-1/turns':
        if (confirmed) {
          followUp = true;
          const next = {
            ...turn, turnId: 'turn-2', dialogueVersion: 5, proposal: undefined,
            processing: { ...processing, state: 'LOCAL_CLARIFICATION' }, localQuestions: [],
          };
          turns = [...turns, next];
          return route.fulfill({ status: 202, json: next });
        }
        turns = [turn];
        return route.fulfill({ status: 202, json: turn });
      case '/providers':
        return route.fulfill({ json: { items: [{
          binding: { provider: 'SYNTHETIC', profileId: 'profile-1', modelId: 'local-double' },
          credential: { state: 'READY' }, probeState: 'VERIFIED', availability: 'AVAILABLE', structuredOutputSupported: true,
        }] } });
      case '/dialogues/dialogue-1/turns/turn-1/processing-previews':
        if (previewBlocked) return route.fulfill({ status: 422, json: { code: 'MODEL_CONTEXT_UNCLASSIFIED' } });
        return route.fulfill({ status: 201, json: {
          previewId: 'preview-1', turnId: 'turn-1', turnVersion: 1, dialogueVersion: 2,
          provider: 'SYNTHETIC', profileId: 'profile-1', modelId: 'local-double', dataClasses: ['RESEARCH_TEXT'],
          expiresAt: new Date(Date.now() + 120_000).toISOString(), projectionBinding: 'fixture-binding',
          projection: { excerpt: original, confirmedContext: {} },
        } });
      case '/dialogues/dialogue-1/turns/turn-1/processing-decisions':
        processing.state = 'SEND_AUTHORIZED';
        processing.receipt = { receiptId: 'receipt-1' };
        return route.fulfill({ status: 202, json: processing });
      case '/dialogues/dialogue-1/decisions':
        confirmed = true;
        return route.fulfill({ json: { ideaVersion: 2, dialogueVersion: 4 } });
      case '/dialogues/dialogue-1/turns/turn-2/processing-decisions':
        turns[1].processing.state = 'DECLINED';
        return route.fulfill({ json: turns[1].processing });
      default:
        return route.fulfill({ status: 404, json: {} });
    }
  });

  const companion = await openCompanion(page);
  await companion.getByLabel('Titel', { exact: true }).fill(idea.title);
  await companion.getByLabel('Forschungsfrage', { exact: true }).fill(original);
  await companion.getByRole('button', { name: 'Forschungsfrage speichern' }).press('Enter');
  await expect(companion.getByRole('heading', { name: idea.title })).toBeFocused();
  await companion.getByRole('button', { name: 'Gespeicherte Frage lokal klären' }).press('Enter');
  await expect(companion.getByLabel(question, { exact: true })).toBeVisible();
  await companion.getByRole('button', { name: 'Providerstatus laden' }).click();
  await companion.getByRole('button', { name: 'Sendefassung für local-double anzeigen' }).click();
  await expect(companion.getByRole('alert')).toContainText('MODEL_CONTEXT_UNCLASSIFIED');
  await expect(companion.getByRole('button', { name: 'Diese Fassung senden' })).toHaveCount(0);
  await expect(companion.getByRole('button', { name: 'Lokal beantworten' })).toBeVisible();
  previewBlocked = false;
  await companion.getByRole('button', { name: 'Sendefassung für local-double anzeigen' }).click();
  await expect(companion.getByRole('heading', { name: 'Sendefassung' })).toBeFocused();
  await expect(companion.getByRole('region', { name: 'Sendefassung' })).toContainText(original);
  expect(posts.filter(post => post.path.endsWith('processing-decisions'))).toEqual([]);

  await page.reload();
  await expect(companion.getByLabel(question, { exact: true })).toBeVisible();
  await expect(companion.getByRole('button', { name: 'Diese Fassung senden' })).toHaveCount(0);
  await companion.getByRole('button', { name: 'Providerstatus laden' }).click();
  await companion.getByRole('button', { name: 'Sendefassung für local-double anzeigen' }).click();
  await companion.getByRole('button', { name: 'Diese Fassung senden' }).press('Enter');
  await expect(companion.getByRole('status').filter({ hasText: 'SEND_AUTHORIZED' })).toBeVisible();
  await expect(companion.getByRole('button', { name: 'Übermittlung widerrufen' })).toBeEnabled();
  await companion.getByLabel('Frage im Dialog', { exact: true }).fill('Andere Forschungsfrage');
  await expect(companion.getByRole('button', { name: 'Dialogbeitrag speichern' })).toBeDisabled();
  expect(posts.filter(post => post.path.endsWith('processing-decisions')).map(post => post.body)).toEqual([{
    action: 'SEND', expectedTurnVersion: 1, expectedDialogueVersion: 2,
    previewId: 'preview-1', projectionBinding: 'fixture-binding',
  }]);
  expect(posts.some(post => post.path.endsWith('/decisions') || post.path.includes('/approvals'))).toBe(false);

  const untrusted = '<a href="https://example.invalid">Text aus dem Modell</a>';
  turn.proposal = {
    proposalId: 'proposal-1', proposalHash: 'proposal-binding', dialogueVersion: 3, ideaVersion: 1, conflicts: [],
    interpretation: {
      hypothesis: { statement: untrusted, trigger: interpretedTrigger },
      assumptions: [], openQuestions: [], unsupportedAspects: [], requiredDataTypes: [],
    },
  };
  processing.state = 'PROPOSAL_VALIDATED';
  await companion.getByRole('button', { name: 'Dialogstatus aktualisieren' }).click();
  await expect(companion.getByText(untrusted, { exact: true })).toBeVisible();
  await expect(companion.getByRole('link', { name: 'Text aus dem Modell' })).toHaveCount(0);
  await companion.getByRole('checkbox', { name: 'Auslöser · ' + interpretedTrigger }).check();
  await companion.getByRole('button', { name: 'Auswahl als Hypothese bestätigen' }).press('Enter');
  await expect(companion.getByRole('heading', { name: 'Bestätigte Felder' })).toBeVisible();
  expect(posts.filter(post => post.path.endsWith('/decisions')).map(post => post.body)).toEqual([{
    action: 'CONFIRM_HYPOTHESIS', expectedDialogueVersion: 3, expectedIdeaVersion: 1,
    proposalId: 'proposal-1', proposalHash: 'proposal-binding',
    confirmedFields: [{ field: 'TRIGGER', value: interpretedTrigger }], confirmedAssumptionIds: [],
  }]);
  await companion.getByRole('button', { name: 'Advanced anzeigen' }).press('Enter');
  await expect(companion.getByText('dialogue-1 · Version 4')).toBeVisible();
  await page.reload();
  await expect(companion.getByRole('heading', { name: 'Bestätigte Felder' })).toBeVisible();

  const revised = 'Wie unterscheidet sich der Verlauf nach drei Wochen statt nach einer Woche?';
  await companion.getByLabel('Frage im Dialog', { exact: true }).fill(revised);
  await companion.getByRole('button', { name: 'Dialogbeitrag speichern' }).press('Enter');
  await expect(companion.getByRole('button', { name: 'Auswahl als Hypothese bestätigen' })).toHaveCount(0);
  await companion.getByRole('button', { name: 'Übermittlung ablehnen' }).press('Enter');
  await expect(companion.getByRole('status').filter({ hasText: 'DECLINED' })).toBeVisible();
  expect(posts.at(-2)?.body).toEqual({
    kind: 'FREE_TEXT_RESEARCH', text: revised, expectedDialogueVersion: 4, expectedIdeaVersion: 2,
  });
  expect(posts.at(-1)?.body).toEqual({ action: 'DECLINE', expectedTurnVersion: 1, expectedDialogueVersion: 5 });

  sessionRevoked = true;
  await page.request.post('/__test/revoke');
  if (await companion.isVisible()) await companion.getByRole('button', { name: 'Ideen neu laden' }).click();
  await expect(page.getByLabel('Aktivierungscode')).toBeVisible();
  await expect(page.getByText(original, { exact: true })).toHaveCount(0);
});

test('Quant decisions bind the displayed version and reports retain exact server metrics', async ({ page }) => {
  const question = 'Synthetische Quartalsanalyse';
  const approval = {
    approvalId: 'approval-1', approvalVersion: 7, bindingHash: 'immutable-approval', state: 'PENDING',
    question, hypothesis: 'Synthetische Annahme', assumptions: [], definition: {}, evidence: [],
    binding: {
      expiresAt: '2099-01-01T00:00:00Z', researchMode: 'STRICT_PIT', definitionVersion: 3,
      experimentId: 'experiment-1', resourceBudget: {}, activePolicies: [],
    },
  };
  const summary = { title: 'Synthetischer Bericht', reportId: 'report-1', reportVersion: 2, batchId: 'batch-1' };
  const unavailable = { status: 'UNAVAILABLE', metrics: [], limitations: ['Keine Beobachtungen'] };
  const exact = '0.012345678901234567';
  const report = {
    summary, experimentId: 'experiment-1', researchQuestion: question, hypothesis: approval.hypothesis,
    phases: {
      inSample: { status: 'AVAILABLE', metrics: [{ claimId: 'claim-1', label: 'Rendite', value: exact }], limitations: [] },
      validation: unavailable, walkForwardOos: unavailable, holdout: unavailable,
    },
    facts: [], interpretations: [], unknowns: [], assumptions: [], furtherResearch: [], limitations: [], currentRightsWarnings: [],
  };
  const decisions: unknown[] = [];
  await page.route('**/api/v1/research/agent/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/research/agent', '');
    switch (path) {
      case '/ideas': return route.fulfill({ json: { items: [], nextCursor: null } });
      case '/approvals': return route.fulfill({ json: { items: [approval], nextCursor: null } });
      case '/approvals/approval-1': return route.fulfill({ json: approval });
      case '/approvals/approval-1/decision':
        decisions.push(route.request().postDataJSON());
        approval.state = 'REJECTED';
        approval.approvalVersion++;
        return route.fulfill({ json: approval });
      case '/reports': return route.fulfill({ json: { items: [summary], nextCursor: null } });
      case '/reports/report-1': return route.fulfill({ json: report });
      default: return route.fulfill({ status: 404, json: {} });
    }
  });
  const companion = await openCompanion(page);
  await companion.getByRole('button', { name: 'Freigaben laden' }).click();
  await companion.getByRole('button', { name: question }).click();
  await expect(companion.getByText('immutable-approval', { exact: true })).toBeVisible();
  expect(decisions).toEqual([]);
  await companion.getByText('Quant-Entscheidung treffen').press('Enter');
  await companion.getByRole('button', { name: 'Quant-Lauf ablehnen' }).press('Enter');
  await expect(companion.getByText(/Freigabeversion 8.*REJECTED/)).toBeVisible();
  expect(decisions).toEqual([{ action: 'REJECT', expectedApprovalVersion: 7, bindingHash: 'immutable-approval' }]);
  await companion.getByRole('button', { name: 'Berichte laden' }).click();
  await companion.getByRole('button', { name: summary.title }).click();
  await expect(companion.getByText(exact, { exact: true })).toBeVisible();
  await expect(companion.getByRole('heading', { name: 'Holdout' })).toBeVisible();
  await expect(companion.getByRole('link', { name: 'Quant-Evidenz öffnen' })).toHaveAttribute('href', /quantBatch=batch-1/);
  await page.reload();
  await expect(companion.getByText(exact, { exact: true })).toBeVisible();
  await expect(companion.getByText(/Freigabeversion 8.*REJECTED/)).toBeVisible();
});

test('local clarification uses the active turn and preserves an Advanced experiment binding', async ({ page }) => {
  const title = 'Fünf Handelstage';
  const original = 'Was geschieht in der Woche nach drei negativen Handelstagen?';
  const idea = {
    id: 'idea-2', version: 1, title, originalText: original, dialogueId: 'dialogue-2',
    status: 'DRAFT', hypothesis: null, uncertainties: [], evidence: [], experimentIds: [], experimentDraftIds: [],
  };
  const expertExperiment = { definitionId: 'experiment-2', definitionVersion: 4, definitionHash: 'exact-definition' };
  const initialQuestion = 'Wie viele Handelstage umfasst die nächste Woche?';
  const processing = { state: 'LOCAL_CLARIFICATION', delivery: 'NOT_SENT', costState: 'NOT_INCURRED', turnVersion: 1 };
  const captured: unknown[] = [];
  let version = 1;
  let bound = false;
  const dialogue = () => ({
    id: idea.dialogueId, ideaId: idea.id, ideaVersion: 1, version, nextCursor: null,
    confirmedFields: [], confirmedAssumptions: [], expertExperiment: bound ? expertExperiment : undefined,
    turns: version < 3 ? [] : [{
      turnId: 'turn-2', dialogueVersion: version, processing,
      localQuestions: version === 3 ? [{ id: 'question-2', field: 'MEASUREMENT_HORIZON', question: initialQuestion }] : [],
    }],
  });
  await page.route('**/api/v1/research/experiments/experiment-2', route => route.fulfill({
    json: { id: 'experiment-2', definitionVersion: 4, definitionHash: 'exact-definition' },
  }));
  await page.route('**/api/v1/research/agent/**', async route => {
    const request = route.request();
    if (request.method() === 'POST') captured.push(request.postDataJSON());
    const path = new URL(request.url()).pathname.replace('/api/v1/research/agent', '');
    switch (path) {
      case '/ideas': return route.fulfill({ json: { items: [idea], nextCursor: null } });
      case '/ideas/idea-2': return route.fulfill({ json: idea });
      case '/dialogues/dialogue-2': return route.fulfill({ json: dialogue() });
      case '/dialogues':
        bound = true;
        version++;
        return route.fulfill({ status: 201, json: dialogue() });
      case '/dialogues/dialogue-2/turns':
        version++;
        return route.fulfill({ status: 202, json: dialogue().turns[0] });
      default: return route.fulfill({ status: 404, json: {} });
    }
  });
  const companion = await openCompanion(page);
  await companion.getByRole('button', { name: title, exact: true }).press('Enter');
  await companion.getByRole('button', { name: 'Advanced anzeigen' }).click();
  await companion.getByLabel('Gespeicherte Experiment-ID').fill('experiment-2');
  await companion.getByRole('button', { name: 'Expertenvorgaben übernehmen' }).press('Enter');
  await expect(companion.getByRole('link', { name: 'Experiment öffnen' })).toHaveAttribute('href', /quantExperiment=experiment-2/);
  await companion.getByRole('button', { name: 'Advanced ausblenden' }).click();
  await companion.getByLabel('Frage im Dialog', { exact: true }).fill(original);
  const saveTurn = companion.getByRole('button', { name: 'Dialogbeitrag speichern' });
  await expect(saveTurn).toBeEnabled();
  await saveTurn.press('Enter');
  await companion.getByLabel(initialQuestion).fill('Fünf Handelstage');
  await companion.getByRole('button', { name: 'Lokal beantworten' }).press('Enter');
  await expect(companion.getByLabel(initialQuestion)).toHaveCount(0);
  await companion.getByRole('button', { name: 'Advanced anzeigen' }).click();
  await expect(companion.getByRole('link', { name: 'Experiment öffnen' })).toHaveAttribute('href', /quantExperiment=experiment-2/);
  expect(captured).toEqual([
    { ideaId: idea.id, expectedIdeaVersion: 1, expertExperiment },
    { kind: 'FREE_TEXT_RESEARCH', text: original, expectedDialogueVersion: 2, expectedIdeaVersion: 1 },
    { kind: 'LOCAL_STRUCTURED_ANSWER', questionId: 'question-2', field: 'MEASUREMENT_HORIZON',
      value: 'Fünf Handelstage', expectedDialogueVersion: 3, expectedIdeaVersion: 1 },
  ]);
});
