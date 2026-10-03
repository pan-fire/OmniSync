import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

/**
 * Scan the page with axe-core (WCAG 2.x A and AA rules) and fail on any
 * serious or critical violation, listing each rule and the elements it hit.
 */
export async function expectNoSeriousA11yViolations (page: Page): Promise<void> {
  // Settle every transition and animation first: a button fading in from
  // its disabled opacity, or the drawer fading in, would otherwise be
  // measured mid-way and fail the contrast check at random.
  await page.addStyleTag({
    content: '*, *::before, *::after { transition: none !important; animation: none !important; }',
  });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  const serious = results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.impact} ${violation.id}: ${violation.help}\n` +
      violation.nodes.map((node) => `  ${node.target.join(' ')}`).join('\n'));
  expect(serious, serious.join('\n')).toEqual([]);
}
