import { test, expect } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

test('IEF actual text and primary action meet normal-text contrast', async ({ page }) => {
  await page.goto('/home');
  await expect(page.getByRole('heading', { name: 'Mein Überblick' })).toBeVisible();
  const ratios = await page.locator('main h1, main .lead, main .prototype-banner, main a[mat-flat-button]').evaluateAll(elements => {
    function rgb(value: string): number[] {
      const parts = value.match(/[\d.]+/g)?.map(Number) ?? [];
      if (parts.length < 3) throw new Error(`Unsupported computed color ${value}`);
      return parts;
    }
    function luminance(color: number[]): number {
      const linear = color.slice(0, 3).map(value => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    }
    return elements.map(element => {
      const foreground = rgb(getComputedStyle(element).color);
      let parent: Element | null = element;
      let background = [255, 255, 255];
      while (parent) {
        const candidate = rgb(getComputedStyle(parent).backgroundColor);
        if (candidate.length === 3 || candidate[3] === 1) { background = candidate; break; }
        if (candidate[3] !== 0) throw new Error('Translucent background needs compositing');
        parent = parent.parentElement;
      }
      const values = [luminance(foreground), luminance(background)].sort((a, b) => a - b);
      return (values[1] + 0.05) / (values[0] + 0.05);
    });
  });
  expect(ratios.length).toBeGreaterThanOrEqual(4);
  for (const ratio of ratios) expect(ratio).toBeGreaterThanOrEqual(4.5);
});

test('IEF 200 percent text enlargement preserves representative mobile and desktop screens', async ({ page }) => {
  for (const width of [360, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ['/home', '/onboarding', '/paper/orders/new?phase=status&status=UNKNOWN&context=PAPER']) {
      await page.goto(path);
      // Explicit text enlargement; this is not a claim of browser-chrome zoom certification.
      await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
      await expect(page.locator('main h1')).toBeVisible();
      const overflow = await page.evaluate(() => {
        const main = document.querySelector('main')!;
        const content = document.querySelector('mat-sidenav-content')!;
        return {
          page: document.documentElement.scrollWidth - innerWidth,
          main: main.scrollWidth - main.clientWidth,
          content: content.scrollWidth - content.clientWidth,
        };
      });
      expect(overflow.page, `${width}px ${path} page`).toBeLessThanOrEqual(1);
      expect(overflow.main, `${width}px ${path} main`).toBeLessThanOrEqual(1);
      expect(overflow.content, `${width}px ${path} content`).toBeLessThanOrEqual(1);
    }
  }
});
