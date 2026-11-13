import { expect, test, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type {
  AgentDialogueV1,
  AgentIdeaVersionV1,
  AgentQuantApprovalPageV1,
  AgentReportPageV1,
  AgentRunV1,
  ProcessingStatusV1,
} from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

const activation = process.env.APF_BROWSER_ACTIVATION;
const expert = process.env.APF_BROWSER_QUANT_EXPERT;
const agent = '/api/v1/research/agent';
const title = 'Synthetische Quartalsanalyse';
const original = 'Erholen sich Technologieaktien nach enttäuschenden Quartalszahlen?';
const trigger = 'Quartalszahlen unter den Erwartungen';

test.skip(!activation || !expert || !process.env.RESEARCH_BROWSER_CONTAINER,
  'Requires the isolated APF browser owner with the real Control API, Relay and PostgreSQL.');

function posted(page: Page, path: string) {
  return page.waitForResponse(response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname === path,
    { timeout: 20_000 });
}

async function read<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(path);
  expect(response.status()).toBe(200);
  return await response.json() as T;
}

async function selectedIdea(page: Page): Promise<AgentIdeaVersionV1> {
  const id = new URL(page.url()).searchParams.get('researchIdea');
  expect(id).toBeTruthy();
  return read(page, `${agent}/ideas/${id}`);
}

test('the real browser confirms a persisted hypothesis after explicit consent to the exact send preview', async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto('/');
  await page.getByLabel('Aktivierungscode', { exact: true }).fill(activation!);
  await page.getByRole('button', { name: 'Sitzung aktivieren', exact: true }).click();
  await page.getByRole('button', { name: 'Research Companion öffnen', exact: true }).click();
  const companion = page.locator('atl-agent-companion');
  await expect(companion.getByRole('button', { name: 'Does the market recover after a decline?' }))
    .toBeVisible();

  await companion.getByLabel('Titel', { exact: true }).fill(title);
  await companion.getByLabel('Forschungsfrage', { exact: true }).fill(original);
  const savedResponse = posted(page, agent + '/ideas');
  await companion.getByRole('button', { name: 'Forschungsfrage speichern', exact: true }).click();
  const saved = await savedResponse;
  expect(saved.status()).toBe(201);
  await expect(companion.getByRole('heading', { name: title, exact: true })).toBeFocused();
  const idea = await selectedIdea(page);
  expect(idea.originalText).toBe(original);

  const dialoguePath = `${agent}/dialogues/${idea.dialogueId}`;
  const submittedResponse = posted(page, dialoguePath + '/turns');
  await companion.getByRole('button', { name: 'Gespeicherte Frage lokal klären', exact: true }).click();
  const submitted = await submittedResponse;
  expect(submitted.status()).toBe(202);
  const turn = (await read<AgentDialogueV1>(page, dialoguePath)).turns.at(-1)!;
  const processingPath = `${dialoguePath}/turns/${turn.turnId}/processing`;
  await expect(companion.getByRole('button', { name: 'Lokal beantworten', exact: true }).first()).toBeVisible();
  await expect(companion.getByRole('button', { name: 'Diese Fassung senden', exact: true })).toHaveCount(0);

  const processingStatus = async (): Promise<ProcessingStatusV1['state'] | undefined> => {
    const response = await page.request.get(processingPath);
    if (!response.ok()) return undefined;
    const status = await response.json() as ProcessingStatusV1;
    return status.state;
  };
  expect(await processingStatus()).toBe('LOCAL_CLARIFICATION');
  expect(turn.runId).toBeTruthy();
  await expect.poll(async () => (await read<AgentRunV1>(page, `${agent}/runs/${turn.runId}`)).state)
    .toBe('WAITING_FOR_PROCESSING_DECISION');
  await companion.getByRole('button', { name: 'Providerstatus laden', exact: true }).click();
  const previewResponse = posted(page, processingPath + '-previews');
  await companion.getByRole('button', { name: 'Sendefassung für offline-model anzeigen', exact: true }).click();
  const prepared = await previewResponse;
  expect(prepared.status()).toBe(201);
  await expect(companion.getByRole('heading', { name: 'Sendefassung', exact: true })).toBeFocused();
  const preview = companion.getByRole('region', { name: 'Sendefassung', exact: true });
  await expect(preview.getByText(original, { exact: true })).toBeVisible();
  await expect(preview.getByText('{}', { exact: true })).toBeVisible();
  await expect(preview).toContainText('offline-profile');
  const awaiting = await read<ProcessingStatusV1>(page, processingPath);
  expect(awaiting.state).toBe('AWAITING_DECISION');

  const sentResponse = posted(page, processingPath + '-decisions');
  await companion.getByRole('button', { name: 'Diese Fassung senden', exact: true }).click();
  const sent = await sentResponse;
  expect(sent.status()).toBe(202);
  const consent = sent.request().postDataJSON();
  expect(consent).toEqual({
    action: 'SEND',
    expectedTurnVersion: awaiting.turnVersion,
    expectedDialogueVersion: awaiting.dialogueVersion,
    previewId: expect.any(String),
    projectionBinding: expect.any(String),
  });
  const key = sent.request().headers()['idempotency-key'];
  expect(key).toBeTruthy();
  const replay = await page.request.post(processingPath + '-decisions', {
    headers: { Origin: new URL(page.url()).origin, 'Idempotency-Key': key },
    data: consent,
  });
  expect(replay.status()).toBe(202);
  const accepted = await replay.json() as ProcessingStatusV1;
  expect(accepted.state).toBe('SEND_AUTHORIZED');
  await expect.poll(processingStatus).toBe('PROPOSAL_VALIDATED');
  expect((await read<ProcessingStatusV1>(page, processingPath)).receipt?.receiptId).toBe(accepted.receipt?.receiptId);
  await companion.getByRole('button', { name: 'Dialogstatus aktualisieren', exact: true }).click();
  const triggerField = companion.getByRole('checkbox', { name: 'Auslöser · ' + trigger, exact: true });
  await expect(triggerField).toBeVisible();
  await expect(companion.getByRole('heading', { name: 'Bestätigte Felder', exact: true })).toHaveCount(0);
  await triggerField.check();
  const confirmedResponse = posted(page, dialoguePath + '/decisions');
  await companion.getByRole('button', { name: 'Auswahl als Hypothese bestätigen', exact: true }).click();
  const confirmed = await confirmedResponse;
  expect(confirmed.status()).toBe(200);
  await expect(companion.getByRole('heading', { name: 'Bestätigte Felder', exact: true })).toBeVisible();
  await page.reload();
  await expect(companion.getByRole('heading', { name: 'Bestätigte Felder', exact: true })).toBeVisible();
  await expect(companion.getByText('TRIGGER · ' + trigger, { exact: true })).toBeVisible();
  await expect(companion.getByRole('region', { name: 'Sendefassung', exact: true })).toHaveCount(0);
  const persisted = await read<AgentIdeaVersionV1>(page, `${agent}/ideas/${idea.id}`);
  expect(persisted.version).toBe(idea.version + 1);
  expect(persisted.hypothesis?.trigger).toBe(trigger);
  expect(persisted.testability).toBeNull();
  const staleConfirmation = await page.request.post(dialoguePath + '/decisions', {
    headers: { Origin: new URL(page.url()).origin, 'Idempotency-Key': 'apf-browser-stale-hypothesis' },
    data: confirmed.request().postDataJSON(),
  });
  expect(staleConfirmation.status()).toBe(409);
  expect((await selectedIdea(page)).version).toBe(persisted.version);

  const variants = [
    ['Können sich Technologiewerte nach enttäuschenden Quartalsberichten erholen?', trigger],
    ['Was folgt auf drei negative Handelstage?', 'Drei negative Handelstage'],
  ];
  for (const [index, [excerpt, expectedTrigger]] of variants.entries()) {
    const variantTitle = `Synthetische Sprachvariante ${index + 1}`;
    await companion.getByLabel('Titel', { exact: true }).fill(variantTitle);
    await companion.getByLabel('Forschungsfrage', { exact: true }).fill(excerpt);
    await companion.getByRole('button', { name: 'Forschungsfrage speichern', exact: true }).click();
    await expect(companion.getByRole('heading', { name: variantTitle, exact: true })).toBeFocused();
    const variant = await selectedIdea(page);
    const path = `${agent}/dialogues/${variant.dialogueId}`;
    const submission = posted(page, path + '/turns');
    await companion.getByRole('button', { name: 'Gespeicherte Frage lokal klären', exact: true }).click();
    expect((await submission).status()).toBe(202);
    const newest = (await read<AgentDialogueV1>(page, path)).turns.at(-1)!;
    await expect.poll(async () => (await read<AgentRunV1>(page, `${agent}/runs/${newest.runId}`)).state)
      .toBe('WAITING_FOR_PROCESSING_DECISION');
    await companion.getByRole('button', { name: 'Providerstatus laden', exact: true }).click();
    await companion.getByRole('button', { name: 'Sendefassung für offline-model anzeigen', exact: true }).click();
    await expect(companion.getByRole('region', { name: 'Sendefassung' })).toContainText(excerpt);
    await companion.getByRole('button', { name: 'Diese Fassung senden', exact: true }).click();
    await expect.poll(async () => (await read<AgentDialogueV1>(page, path)).turns.at(-1)
      ?.proposal?.interpretation.hypothesis.trigger).toBe(expectedTrigger);
    if (index === 1) {
      const beforeChange = await read<AgentDialogueV1>(page, path);
      await companion.getByRole('button', { name: 'Dialogstatus aktualisieren', exact: true }).click();
      await companion.getByRole('checkbox', { name: /Messhorizont.*offen/ }).check();
      await companion.getByLabel('Gewünschte Änderung', { exact: true }).fill('Fünf Handelstage');
      const requested = posted(page, path + '/decisions');
      await companion.getByRole('button', { name: 'Änderung anfordern', exact: true }).click();
      expect((await requested).status()).toBe(200);
      const changed = await read<AgentDialogueV1>(page, path);
      expect(changed.version).toBe(beforeChange.version + 1);
      await expect(companion.getByRole('button', { name: 'Auswahl als Hypothese bestätigen' })).toHaveCount(0);
    }
  }

  // The local path adds no requests to the three authorized sends.
  await companion.getByLabel('Titel', { exact: true }).fill('Lokale Anschlussfrage');
  await companion.getByLabel('Forschungsfrage', { exact: true }).fill('Wie lang ist die nächste Handelswoche?');
  const localSavedResponse = posted(page, agent + '/ideas');
  await companion.getByRole('button', { name: 'Forschungsfrage speichern', exact: true }).click();
  const localSaved = await localSavedResponse;
  expect(localSaved.status()).toBe(201);
  await expect(companion.getByRole('heading', { name: 'Lokale Anschlussfrage', exact: true })).toBeFocused();
  const localIdea = await selectedIdea(page);
  const localDialogue = `${agent}/dialogues/${localIdea.dialogueId}`;
  const localPath = localDialogue + '/turns';
  const cleanDialogue = await read<AgentDialogueV1>(page, localDialogue);
  const syntheticContact = 'synthetic@example.invalid';
  const unclassified = await page.request.post(localPath, {
    headers: { Origin: new URL(page.url()).origin, 'Idempotency-Key': 'apf-browser-unclassified' },
    data: { kind: 'FREE_TEXT_RESEARCH', text: syntheticContact,
      expectedDialogueVersion: cleanDialogue.version, expectedIdeaVersion: cleanDialogue.ideaVersion },
  });
  expect(unclassified.status()).toBe(422);
  const rejection = await unclassified.text();
  expect(rejection).toContain('MODEL_CONTEXT_UNCLASSIFIED');
  expect(rejection).not.toContain(syntheticContact);
  expect(await read<AgentDialogueV1>(page, localDialogue)).toEqual(cleanDialogue);
  const localSubmittedResponse = posted(page, localPath);
  await companion.getByRole('button', { name: 'Gespeicherte Frage lokal klären', exact: true }).click();
  const localSubmitted = await localSubmittedResponse;
  expect(localSubmitted.status()).toBe(202);
  const localTurn = (await read<AgentDialogueV1>(page, localDialogue)).turns.at(-1)!;
  const localProcessing = `${localPath}/${localTurn.turnId}/processing`;
  const declineResponse = posted(page, localProcessing + '-decisions');
  await companion.getByRole('button', { name: 'Übermittlung ablehnen', exact: true }).click();
  const decline = await declineResponse;
  expect(decline.status()).toBe(200);
  expect((await read<ProcessingStatusV1>(page, localProcessing)).delivery).toBe('NOT_SENT');
  await expect(companion.getByRole('status').filter({ hasText: 'DECLINED' })).toBeVisible();
  const localQuestion = localTurn.localQuestions.find(question => question.field === 'MEASUREMENT_HORIZON');
  expect(localQuestion).toBeDefined();
  const answer = companion.locator('form').filter({ has: page.getByLabel(localQuestion!.question, { exact: true }) });
  await answer.getByLabel(localQuestion!.question, { exact: true }).fill('Fünf Handelstage');
  const answeredResponse = posted(page, localPath);
  await answer.getByRole('button', { name: 'Lokal beantworten', exact: true }).click();
  const answered = await answeredResponse;
  expect(answered.status()).toBe(202);
  const localProposal = (await read<AgentDialogueV1>(page, localDialogue)).turns.at(-1)?.proposal;
  expect(localProposal?.interpretation.hypothesis.measurementHorizon)
    .toBe('Fünf Handelstage');

  const quantTitle = 'Synthetische EMA-Forschungsfrage';
  const quantQuestion = 'Wie schneidet der EMA-Kreuzungspunkt gegen Buy-and-Hold ab?';
  await companion.getByLabel('Titel', { exact: true }).fill(quantTitle);
  await companion.getByLabel('Forschungsfrage', { exact: true }).fill(quantQuestion);
  await companion.getByRole('button', { name: 'Forschungsfrage speichern', exact: true }).click();
  await expect(companion.getByRole('heading', { name: quantTitle, exact: true })).toBeFocused();
  const quantIdea = await selectedIdea(page);
  const quantDialogue = `${agent}/dialogues/${quantIdea.dialogueId}`;
  await companion.getByRole('button', { name: 'Advanced anzeigen', exact: true }).click();
  await companion.getByLabel('Gespeicherte Experiment-ID').fill(expert!);
  await companion.getByRole('button', { name: 'Expertenvorgaben übernehmen' }).click();
  await expect(companion.getByRole('link', { name: 'Experiment öffnen' })).toBeVisible();
  const quantSubmitted = posted(page, quantDialogue + '/turns');
  await companion.getByRole('button', { name: 'Gespeicherte Frage lokal klären' }).click();
  expect((await quantSubmitted).status()).toBe(202);
  const inputs = {
    MARKET: 'SPY', TRIGGER: 'EMA crossover', SIGNAL: 'EMA(10) above EMA(30)',
    OBSERVATION_WINDOW: '2026-01-05/2026-02-24', TARGET: 'net return',
    MEASUREMENT_HORIZON: 'entire evaluation window', BENCHMARK: 'buy-hold-v1',
    EVALUATION_CRITERION: 'return greater than benchmark',
  };
  for (const [field, value] of Object.entries(inputs)) {
    const current = await read<AgentDialogueV1>(page, quantDialogue);
    const question = current.turns.at(-1)?.localQuestions.find(item => item.field === field);
    expect(question).toBeDefined();
    const form = companion.locator('form').filter({
      has: page.getByLabel(question!.question, { exact: true }),
    }).filter({
      has: page.getByRole('button', { name: 'Lokal beantworten', exact: true }).and(page.locator(':enabled')),
    });
    await expect(form).toHaveCount(1);
    await form.getByLabel(question!.question, { exact: true }).fill(value);
    const answer = posted(page, quantDialogue + '/turns');
    await form.getByRole('button', { name: 'Lokal beantworten', exact: true }).click();
    expect((await answer).status()).toBe(202);
  }
  const confirm = companion.getByRole('button', { name: 'Auswahl als Hypothese bestätigen' });
  await expect(confirm).toBeEnabled();
  const fields = companion.locator('form').filter({
    has: page.getByRole('button', { name: 'Auswahl als Hypothese bestätigen' }),
  }).getByRole('checkbox');
  await expect(fields).toHaveCount(8);
  for (const field of await fields.all()) await field.check();
  await confirm.click();
  await companion.getByRole('button', { name: 'Quant-Entwurf prüfen lassen' }).click();
  const findApproval = async () => (await read<AgentQuantApprovalPageV1>(page, agent + '/approvals'))
    .items.find(item => item.binding.ideaId === quantIdea.id);
  await expect.poll(async () => (await findApproval())?.state, { timeout: 60_000 }).toBe('PENDING');
  const approval = (await findApproval())!;
  const approvalPath = `${agent}/approvals/${approval.approvalId}/decision`;
  const origin = new URL(page.url()).origin;
  const stale = await page.request.post(approvalPath, {
    headers: { Origin: origin, 'Idempotency-Key': 'apf-browser-stale-approval' },
    data: { action: 'APPROVE_AND_START', expectedApprovalVersion: approval.approvalVersion + 1,
      bindingHash: approval.bindingHash },
  });
  expect(stale.status()).toBe(409);
  const forged = await page.request.post(approvalPath, {
    headers: { Origin: origin, 'Idempotency-Key': 'apf-browser-forged-approval' },
    data: { action: 'APPROVE_AND_START', expectedApprovalVersion: approval.approvalVersion,
      bindingHash: '0'.repeat(64) },
  });
  expect(forged.status()).toBe(409);
  expect((await findApproval())?.state).toBe('PENDING');
  await companion.getByRole('button', { name: 'Freigaben laden' }).click();
  const approvals = companion.getByRole('region', { name: 'Quant-Freigaben' });
  await approvals.getByRole('button', { name: approval.question, exact: true }).click();
  await approvals.getByText('Quant-Entscheidung treffen', { exact: true }).click();
  const started = posted(page, approvalPath);
  await approvals.getByRole('button', { name: 'Freigeben und Quant-Lauf starten' }).click();
  const admitted = await started;
  expect(admitted.status()).toBe(202);
  const startKey = admitted.request().headers()['idempotency-key'];
  expect(startKey).toBeTruthy();
  const startReplay = await page.request.post(approvalPath, {
    headers: { Origin: origin, 'Idempotency-Key': startKey },
    data: admitted.request().postDataJSON(),
  });
  expect(startReplay.status()).toBe(202);
  const findReport = async () => (await read<AgentReportPageV1>(page, agent + '/reports'))
    .items.find(item => item.ideaId === quantIdea.id);
  await expect.poll(async () => (await findReport())?.reportId, { timeout: 90_000 }).toBeTruthy();
  const report = (await findReport())!;
  const detail = await page.request.get(
    `${agent}/reports/${report.reportId}?version=${report.reportVersion}`,
  );
  expect(detail.status(), await detail.text()).toBe(200);
  const reportUrl = new URL(page.url());
  reportUrl.searchParams.delete('researchApproval');
  reportUrl.searchParams.set('researchReport', report.reportId);
  reportUrl.searchParams.set('researchReportVersion', String(report.reportVersion));
  await page.goto(reportUrl.toString());
  await expect(companion.getByRole('heading', { name: 'Holdout' })).toBeVisible();
  await page.reload();
  await expect(companion.getByRole('heading', { name: 'Holdout' })).toBeVisible();
});
