import { act } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';

/**
 * Enter text into a field the way a paste does: focus it and paste, one
 * input event instead of one per character. The profile and channel forms
 * re-render as a whole on every key and pointer event, and typing their
 * values took over 5 s per test under coverage on a loaded machine.
 * Validation and submission only see the final value.
 */
export async function fill (user: UserEvent, field: HTMLElement, text: string) {
  act(() => { field.focus(); });
  await user.paste(text);
}
