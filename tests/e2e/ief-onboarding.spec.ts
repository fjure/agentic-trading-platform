import { expect, test, type BrowserContext, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { InvestmentPreferencesV1 as Preferences, InvestmentPreferencesUpdateV1 as Update } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';
const steps = ['EXPERIENCE','GOALS','HORIZON','LOSS_TOLERANCE','CONTEXT'] as const;
const defaults = (): Preferences => ({ schemaVersion:'1', version:0, updatedAt:null, experienceLevel:'UNSPECIFIED', goals:[], horizon:'UNSPECIFIED', lossTolerance:'UNSPECIFIED', contextReference:{context:'FAKE',namespace:'fake'}, onboardingSteps:steps.map(step=>({step,state:'UNSEEN',updatedAt:null})) });
async function fixture(context: BrowserContext) {
  let current=defaults();
  let loseNext=false;
  let revoked=false;
  const updates: {key:string;body:Update}[]=[];
  const receipts=new Map<string,Preferences>();
  await context.route('**/api/v1/experience/preferences', async route=>{
    const req=route.request();
    if(revoked || !req.headers()['cookie']?.includes('atl_session=synthetic-e2e-session')) return route.fulfill({status:401,json:{code:'SESSION_REQUIRED'}});
    if(req.method()==='GET') return route.fulfill({json:current});
    const body=req.postDataJSON() as Update;
    const key=req.headers()['idempotency-key'];
    updates.push({key,body});
    if(receipts.has(key)) return route.fulfill({json:receipts.get(key)});
    if(body.expectedVersion!==current.version) return route.fulfill({status:409,json:{code:'PREFERENCES_VERSION_CONFLICT'}});
    const at='2026-11-24T09:00:00Z';
    current=body.reset?{...defaults(),version:current.version+1,updatedAt:at}:{schemaVersion:'1',version:current.version+1,updatedAt:at,experienceLevel:body.experienceLevel,goals:body.goals,horizon:body.horizon,lossTolerance:body.lossTolerance,contextReference:body.contextReference,onboardingSteps:body.onboardingSteps.map(step=>({...step,updatedAt:at}))};
    receipts.set(key,structuredClone(current));
    if(loseNext){loseNext=false;return route.abort('failed');}
    return route.fulfill({json:current});
  });
  return {updates,get current(){return current},lose(){loseNext=true},revoke(){revoked=true}};
}
async function activate(page:Page){
  await page.request.post('/__test/reset');
  await page.goto('/onboarding');
  await page.getByLabel('Aktivierungscode',{exact:true}).fill('synthetic-local-activation-code-000001');
  await page.getByRole('button',{name:'Lokale Sitzung aktivieren',exact:true}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 0',{exact:true})).toBeVisible();
}

test('IEF02 signed-in no-key path skips optional fields, explicitly creates and saves demo, reloads real snapshot',async({page,context})=>{
  const f=await fixture(context);
  const mutations:string[]=[];
  page.on('request',req=>{if(['POST','PUT','PATCH','DELETE'].includes(req.method())&&new URL(req.url()).pathname.startsWith('/api/'))mutations.push(new URL(req.url()).pathname)});
  await activate(page);
  expect(f.updates).toEqual([]);
  await page.getByRole('button',{name:'Alle freiwilligen Fragen überspringen und speichern'}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 1',{exact:true})).toBeVisible();
  expect(f.current.onboardingSteps.filter(step=>step.step!=='CONTEXT').every(step=>step.state==='SKIPPED')).toBe(true);
  expect(f.current.onboardingSteps.find(step=>step.step==='CONTEXT')?.state).toBe('UNSEEN');
  await page.getByText('Neue isolierte Demo ausdrücklich anlegen',{exact:true}).click();
  await page.getByRole('button',{name:'Isolierte synthetische Demo mit 100 USD anlegen'}).click();
  await expect(page.getByLabel('Vorhandener Demolauf')).not.toHaveValue('');
  await page.getByRole('button',{name:'Diese Demoauswahl speichern'}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 2',{exact:true})).toBeVisible();
  const selected=f.current.contextReference.demoRunId!;
  expect(selected).toBe('8c0c8b85-b804-4f0e-89bc-4387a73231b4');
  await page.getByRole('button',{name:'Gespeichertes Demo-Cockpit öffnen'}).last().click();
  await expect(page).toHaveURL(`/onboarding/demo/${selected}`);
  await expect(page.getByRole('heading',{name:'Bestätigter synthetischer Bestand'})).toBeVisible();
  await expect(page.getByText('99.50',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Portfolio lokal analysieren · noch nicht verfügbar'})).toBeDisabled();
  const count=mutations.length;
  await page.reload();
  await expect(page.getByRole('heading',{name:'Bestätigter synthetischer Bestand'})).toBeVisible();
  expect(mutations.length).toBe(count);
  expect(mutations.every(path=>['/api/v1/session','/api/v1/experience/preferences','/api/v1/demo/runs'].includes(path))).toBe(true);
  expect(await page.evaluate(()=>localStorage.length+sessionStorage.length)).toBe(0);
});

test('IEF02 saved and skipped steps resume independently, high tolerance changes no trading authority and reset preserves demo',async({page,context})=>{
  const f=await fixture(context); await activate(page);
  await page.getByLabel('Erfahrung',{exact:true}).selectOption('BEGINNER');
  await page.getByRole('button',{name:'Erfahrung speichern',exact:true}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 1',{exact:true})).toBeVisible();
  await page.reload();
  await page.getByRole('tab',{name:/Zeithorizont/}).click();
  await page.getByRole('button',{name:'Zeithorizont überspringen',exact:true}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 2',{exact:true})).toBeVisible();
  await page.getByRole('tab',{name:/Verlusttoleranz/}).click();
  await page.getByLabel('Verlusttoleranz',{exact:true}).selectOption('HIGH');
  await page.getByRole('button',{name:'Verlusttoleranz speichern',exact:true}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 3',{exact:true})).toBeVisible();
  expect(f.current.experienceLevel).toBe('BEGINNER');expect(f.current.horizon).toBe('UNSPECIFIED');expect(f.current.lossTolerance).toBe('HIGH');
  expect(Object.keys(f.updates.at(-1)!.body).sort()).toEqual(['contextReference','expectedVersion','experienceLevel','goals','horizon','lossTolerance','onboardingSteps','reset'].sort());
  await page.getByText('Präferenzen zurücksetzen',{exact:true}).click();
  await page.getByRole('checkbox',{name:'Nur Präferenzen und Fortschritt zurücksetzen'}).check();
  await page.getByRole('button',{name:'Präferenzreset bestätigen'}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 4',{exact:true})).toBeVisible();
  expect(f.current.experienceLevel).toBe('UNSPECIFIED');expect(f.current.onboardingSteps.every(step=>step.state==='UNSEEN')).toBe(true);
  expect(await page.evaluate(()=>localStorage.length+sessionStorage.length)).toBe(0);
});

test('IEF02 parallel stale version is never overwritten; conflict requires explicit reload and renewed choice',async({page,context})=>{
  const f=await fixture(context); await activate(page);
  const second=await context.newPage(); await second.goto('/onboarding');
  await expect(second.getByText('Bestätigte Präferenzversion: 0',{exact:true})).toBeVisible();
  await page.getByLabel('Erfahrung',{exact:true}).selectOption('BEGINNER');await page.getByRole('button',{name:'Erfahrung speichern',exact:true}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 1',{exact:true})).toBeVisible();
  await second.getByLabel('Erfahrung',{exact:true}).selectOption('EXPERIENCED');await second.getByRole('button',{name:'Erfahrung speichern',exact:true}).click();
  await expect(second.getByText(/Versionskonflikt:/)).toBeVisible();expect(f.current.experienceLevel).toBe('BEGINNER');expect(f.updates).toHaveLength(2);
  await second.getByRole('button',{name:'Aktuellen Stand neu laden'}).click();
  await expect(second.getByText('Bestätigte Präferenzversion: 1',{exact:true})).toBeVisible();expect(f.updates).toHaveLength(2);
  await second.getByRole('tab',{name:/Erfahrung/}).click();await expect(second.getByLabel('Erfahrung',{exact:true})).toHaveValue('BEGINNER');
  await second.getByLabel('Erfahrung',{exact:true}).selectOption('EXPERIENCED');await second.getByRole('button',{name:'Erfahrung speichern',exact:true}).click();
  await expect(second.getByText('Bestätigte Präferenzversion: 2',{exact:true})).toBeVisible();expect(f.current.experienceLevel).toBe('EXPERIENCED');
});

test('IEF02 ambiguous preference write retries exact payload/key and session loss clears drafts without replay',async({page,context})=>{
  const f=await fixture(context);await activate(page);f.lose();
  await page.getByLabel('Erfahrung',{exact:true}).selectOption('BEGINNER');await page.getByRole('button',{name:'Erfahrung speichern',exact:true}).click();
  await expect(page.getByText(/Speicherergebnis ungeklärt/)).toBeVisible();await expect(page.getByRole('button',{name:'Erfahrung speichern',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Denselben Speichervorgang erneut prüfen'}).click();
  await expect(page.getByText('Bestätigte Präferenzversion: 1',{exact:true})).toBeVisible();expect(f.updates).toHaveLength(2);expect(f.updates[0]).toEqual(f.updates[1]);
  f.revoke();await page.getByLabel('Erfahrung',{exact:true}).selectOption('EXPERIENCED');await page.getByRole('button',{name:'Erfahrung speichern',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Lokale Sitzung aktivieren'})).toBeVisible();await expect(page.getByLabel('Aktivierungscode',{exact:true})).toHaveValue('');
  const writes=f.updates.length;await page.reload();await expect(page.getByRole('heading',{name:'Lokale Sitzung aktivieren'})).toBeVisible();expect(f.updates).toHaveLength(writes);
  expect(await page.evaluate(()=>localStorage.length+sessionStorage.length)).toBe(0);
});

test('IEF02 GET failures remain honest, foreign demo binding never loads another snapshot, mobile keyboard layout',async({page,context})=>{
  await fixture(context);await activate(page);
  await page.goto('/onboarding/demo/foreign-run');await expect(page.getByText(/nicht der gespeicherte explizite Kontext/)).toBeVisible();await expect(page.getByRole('heading',{name:'Bestätigter synthetischer Bestand'})).toHaveCount(0);
  for(const width of [320,360,768,1440]){
    await page.setViewportSize({width,height:900});await page.goto('/onboarding');await expect(page.getByRole('heading',{name:'Sicher einsteigen'})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  }
  await context.route('**/api/v1/experience/preferences',route=>route.fulfill({status:503,json:{code:'TEMPORARILY_UNAVAILABLE'}}));
  await page.reload();await expect(page.getByText(/Bestätigter Stand nicht verfügbar/)).toBeVisible();await expect(page.getByRole('button',{name:'Aktuellen Stand neu laden'})).toBeVisible();
});
