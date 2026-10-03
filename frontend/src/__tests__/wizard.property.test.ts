import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  wizardReducer,
  canAdvance,
  isValidRemoteName,
  INITIAL_STATE,
  STEP_ORDER,
  type WizardState,
  type WizardStep,
} from '@/components/config/wizard/remote-wizard';
import enLocale from '@/i18n/locales/en.json';
import faLocale from '@/i18n/locales/fa.json';
import deLocale from '@/i18n/locales/de.json';

// --- Arbitraries (fast-check v4 compatible) ---

/** Arbitrary for valid remote names: non-empty, alphanumeric + hyphens + underscores */
const validNameArb = fc
  .stringMatching(/^[a-zA-Z0-9_-]+$/)
  .filter((s) => s.length >= 1 && s.length <= 30);

/** Arbitrary for provider IDs from the known set */
const providerIdArb = fc.constantFrom('drive', 'dropbox', 'onedrive', 's3', 'b2', 'sftp', 'ftp');

/** Arbitrary for auth types */
const authTypeArb = fc.constantFrom('key' as const, 'oauth' as const);

/** Arbitrary for a fields record */
const fieldsArb = fc.dictionary(
  fc.string({ minLength: 1, maxLength: 10 }),
  fc.string({ maxLength: 50 })
);

/** Arbitrary for invalid remote names (contain at least one special char) */
const invalidNameArb = fc
  .string({ minLength: 1, maxLength: 30 })
  .filter((s) => !/^[a-zA-Z0-9_-]+$/.test(s));

describe('Wizard step transition order', () => {
  /**
   * For any sequence of forward transitions from the initial wizard state,
   * the steps must follow the order: provider → config → test → complete.
   * No step may be skipped, and complete has no forward transition.
   */
  it('forward transitions always follow provider → config → test → complete', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 10 }),
        (numForwardSteps) => {
          let state: WizardState = {
            ...INITIAL_STATE,
            remoteName:       'test-remote',
            providerId:       's3',
            providerAuthType: 'key',
            fields:           {
              access_key_id:     'key',
              secret_access_key: 'secret',
            },
          };

          const visitedSteps: WizardStep[] = [state.step];

          for (let i = 0; i < numForwardSteps; i++) {
            const prevStep = state.step;
            state = wizardReducer(state, { type: 'NEXT_STEP' });
            visitedSteps.push(state.step);

            const prevIdx = STEP_ORDER.indexOf(prevStep);
            const currIdx = STEP_ORDER.indexOf(state.step);
            // Step index should never decrease on NEXT_STEP
            expect(currIdx).toBeGreaterThanOrEqual(prevIdx);
            // Should never skip a step (at most +1)
            expect(currIdx - prevIdx).toBeLessThanOrEqual(1);
          }

          // Verify all visited steps are in order
          for (let i = 1; i < visitedSteps.length; i++) {
            const prevIdx = STEP_ORDER.indexOf(visitedSteps[i - 1]);
            const currIdx = STEP_ORDER.indexOf(visitedSteps[i]);
            expect(currIdx).toBeGreaterThanOrEqual(prevIdx);
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  it('complete step has no forward transition', () => {
    const state: WizardState = { ...INITIAL_STATE, step: 'complete' };
    const next = wizardReducer(state, { type: 'NEXT_STEP' });
    expect(next.step).toBe('complete');
  });
});

describe('Incomplete step blocks advancement', () => {
  /**
   * For any wizard state on the provider step where the remote name is empty
   * or invalid or no provider is selected, OR on the config step where any
   * required field for the selected key-based provider is empty, the canAdvance
   * predicate must return false.
   */
  it('provider step: canAdvance is false when no provider selected', () => {
    fc.assert(
      fc.property(fc.string(), (name) => {
        const state: WizardState = {
          ...INITIAL_STATE,
          step:             'provider',
          remoteName:       name,
          providerId:       null,
          providerAuthType: null,
        };
        expect(canAdvance(state)).toBe(false);
      }),
      { numRuns: 100 }
    );
  });

  it('provider step: canAdvance is false when name is empty', () => {
    fc.assert(
      fc.property(providerIdArb, (providerId) => {
        const state: WizardState = {
          ...INITIAL_STATE,
          step:             'provider',
          remoteName:       '',
          providerId,
          providerAuthType: 'key',
        };
        expect(canAdvance(state)).toBe(false);
      }),
      { numRuns: 100 }
    );
  });

  it('provider step: canAdvance is false when name has invalid characters', () => {
    fc.assert(
      fc.property(providerIdArb, invalidNameArb, (providerId, name) => {
        const state: WizardState = {
          ...INITIAL_STATE,
          step:             'provider',
          remoteName:       name,
          providerId,
          providerAuthType: 'key',
        };
        expect(canAdvance(state)).toBe(false);
      }),
      { numRuns: 100 }
    );
  });

  it('config step: canAdvance is false when required fields are empty', () => {
    fc.assert(
      fc.property(
        fc.array(fc.string({ minLength: 1, maxLength: 20 }), { minLength: 1, maxLength: 5 }),
        (requiredFieldNames) => {
          const state: WizardState = {
            ...INITIAL_STATE,
            step:             'config',
            remoteName:       'test',
            providerId:       's3',
            providerAuthType: 'key',
            fields:           {},
          };
          expect(canAdvance(state, requiredFieldNames)).toBe(false);
        }
      ),
      { numRuns: 100 }
    );
  });
});

describe('Back navigation preserves entered data', () => {
  /**
   * For any wizard state that has advanced past the provider step, applying
   * a PREV_STEP action followed by a NEXT_STEP action must produce a state
   * where all previously entered field values, the selected provider, and
   * the remote name are identical to the state before the back navigation.
   */
  it('PREV_STEP then NEXT_STEP preserves all entered data', () => {
    fc.assert(
      fc.property(
        validNameArb,
        providerIdArb,
        authTypeArb,
        fieldsArb,
        fc.constantFrom('config' as WizardStep, 'test' as WizardStep),
        (name, providerId, authType, fields, step) => {
          const state: WizardState = {
            ...INITIAL_STATE,
            step,
            remoteName:       name,
            providerId,
            providerAuthType: authType,
            fields,
          };

          const afterBack = wizardReducer(state, { type: 'PREV_STEP' });
          const afterForward = wizardReducer(afterBack, { type: 'NEXT_STEP' });

          expect(afterForward.remoteName).toBe(state.remoteName);
          expect(afterForward.providerId).toBe(state.providerId);
          expect(afterForward.providerAuthType).toBe(state.providerAuthType);
          expect(afterForward.fields).toEqual(state.fields);
          expect(afterForward.step).toBe(state.step);
        }
      ),
      { numRuns: 100 }
    );
  });
});

describe('Cancel resets wizard to initial state', () => {
  /**
   * For any wizard state (regardless of current step, entered fields, or
   * selected provider), applying a RESET action must produce a state identical
   * to the initial wizard state.
   */
  it('RESET always produces the initial state', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...STEP_ORDER),
        validNameArb,
        providerIdArb,
        authTypeArb,
        fieldsArb,
        fc.string({ maxLength: 50 }),
        fc.string({ maxLength: 100 }),
        (step, name, providerId, authType, fields, sessionId, error) => {
          const state: WizardState = {
            step,
            mode:             'reconnect',
            remoteName:       name,
            providerId,
            providerAuthType: authType,
            fields,
            sessionId:        sessionId || null,
            error:            error || null,
          };

          const reset = wizardReducer(state, { type: 'RESET' });
          expect(reset).toEqual(INITIAL_STATE);
        }
      ),
      { numRuns: 100 }
    );
  });
});

describe('Remote name validation (frontend)', () => {
  /**
   * For any string, the remote name validator must return true if and only if
   * the string is non-empty and matches the pattern ^[a-zA-Z0-9_-]+$.
   * Strings containing spaces, dots, colons, slashes, or other special
   * characters must be rejected.
   */
  it('accepts only non-empty alphanumeric strings with hyphens and underscores', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 0, maxLength: 50 }), (name) => {
        const expected = name.length > 0 && /^[a-zA-Z0-9_-]+$/.test(name);
        expect(isValidRemoteName(name)).toBe(expected);
      }),
      { numRuns: 100 }
    );
  });

  it('always rejects empty strings', () => {
    expect(isValidRemoteName('')).toBe(false);
  });

  it('always accepts valid names', () => {
    fc.assert(
      fc.property(validNameArb, (name) => {
        expect(isValidRemoteName(name)).toBe(true);
      }),
      { numRuns: 100 }
    );
  });

  it('rejects names with special characters', () => {
    fc.assert(
      fc.property(
        fc.tuple(
          validNameArb,
          fc.constantFrom(' ', '.', ':', '/', '\\', '@', '!', '#', '$', '%'),
          validNameArb
        ),
        ([prefix, special, suffix]) => {
          const name = prefix + special + suffix;
          expect(isValidRemoteName(name)).toBe(false);
        }
      ),
      { numRuns: 100 }
    );
  });
});

describe('Translation key completeness', () => {
  /**
   * For any translation key under the wizard.* namespace that exists in the
   * English locale file, that same key must also exist and have a non-empty
   * string value in both the Farsi and German locale files.
   */

  // Import locale files using static imports (defined at top of file)
  const en = enLocale;
  const fa = faLocale;
  const de = deLocale;

  /** Recursively extract all leaf keys from a nested object with a given prefix */
  function extractKeys (obj: Record<string, unknown>, prefix: string): string[] {
    const keys: string[] = [];
    for (const [key, value] of Object.entries(obj)) {
      const fullKey = prefix ? `${prefix}.${key}` : key;
      if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
        keys.push(...extractKeys(value as Record<string, unknown>, fullKey));
      } else {
        keys.push(fullKey);
      }
    }
    return keys;
  }

  /** Get a nested value from an object by dot-separated key */
  function getNestedValue (obj: Record<string, unknown>, key: string): unknown {
    const parts = key.split('.');
    let current: unknown = obj;
    for (const part of parts) {
      if (current === null || current === undefined || typeof current !== 'object') {
        return undefined;
      }
      current = (current as Record<string, unknown>)[part];
    }
    return current;
  }

  // Extract all wizard.* keys from English locale
  const wizardKeys = extractKeys(en.wizard ?? {}, 'wizard');

  it('all wizard.* keys from English exist with non-empty values in Farsi and German', () => {
    // Ensure we actually have wizard keys to test
    expect(wizardKeys.length).toBeGreaterThan(0);

    fc.assert(
      fc.property(
        fc.constantFrom(...wizardKeys),
        (key) => {
          // Key must exist in Farsi with a non-empty string value
          const faValue = getNestedValue(fa, key);
          expect(faValue).toBeDefined();
          expect(typeof faValue).toBe('string');
          expect((faValue as string).length).toBeGreaterThan(0);

          // Key must exist in German with a non-empty string value
          const deValue = getNestedValue(de, key);
          expect(deValue).toBeDefined();
          expect(typeof deValue).toBe('string');
          expect((deValue as string).length).toBeGreaterThan(0);
        }
      ),
      { numRuns: 100 }
    );
  });
});
