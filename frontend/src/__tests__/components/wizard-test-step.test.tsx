import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '@/i18n';
import { INITIAL_STATE } from '@/components/config/wizard/remote-wizard';
import { TestStep } from '@/components/config/wizard/test-step';
import { stubBackend } from '../helpers/fake-backend';

function Wrapper ({ children }: { children: ReactNode }) {
  return <I18nProvider initialLocale="en">{children}</I18nProvider>;
}

const state = { ...INITIAL_STATE, step: 'test' as const, remoteName: 's3' };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('TestStep', () => {
  it('tests the remote on mount and moves on when it works', async () => {
    const { requests } = stubBackend({ 'POST /wizard/test': { success: true, error: null } });
    const dispatch = vi.fn();
    render(<TestStep state={state} dispatch={dispatch} />, { wrapper: Wrapper });
    expect(screen.getByText('Testing connection...')).toBeInTheDocument();
    expect(await screen.findByText('Connection successful')).toBeInTheDocument();
    expect(dispatch).toHaveBeenCalledWith({ type: 'NEXT_STEP' });
    expect(requests).toEqual(['POST /wizard/test']);
  });

  it('shows a failure, and Retry tests again without the old answer', async () => {
    // Retry starts a new attempt from the click: the failure disappears at
    // once, and only the new answer counts.
    const answers = [{ success: false, error: 'bad key' }, { success: true, error: null }];
    const { requests } = stubBackend({ 'POST /wizard/test': () => answers.shift() });
    const dispatch = vi.fn();
    const user = userEvent.setup();
    render(<TestStep state={state} dispatch={dispatch} />, { wrapper: Wrapper });
    expect(await screen.findByText('bad key')).toBeInTheDocument();
    expect(dispatch).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(screen.queryByText('bad key')).not.toBeInTheDocument();
    expect(await screen.findByText('Connection successful')).toBeInTheDocument();
    expect(dispatch).toHaveBeenCalledWith({ type: 'NEXT_STEP' });
    expect(requests).toHaveLength(2);
  });

  it('explains a failed request, and Back returns to the settings', async () => {
    stubBackend({});
    const dispatch = vi.fn();
    const user = userEvent.setup();
    render(<TestStep state={state} dispatch={dispatch} />, { wrapper: Wrapper });
    expect(await screen.findByText('Connection failed')).toBeInTheDocument();
    expect(screen.getByText(/no route for POST \/wizard\/test/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(dispatch).toHaveBeenCalledWith({ type: 'PREV_STEP' });
  });

  it('falls back to a generic message when the failure has none', async () => {
    stubBackend({ 'POST /wizard/test': { success: false, error: null } });
    render(<TestStep state={state} dispatch={vi.fn()} />, { wrapper: Wrapper });
    expect(await screen.findByText('An unknown error occurred')).toBeInTheDocument();
  });

  it('shows only the answer for the remote now being tested', async () => {
    // An answer for an earlier remote name that arrives late is dropped.
    const pending: ((body: unknown) => void)[] = [];
    vi.stubGlobal('fetch', vi.fn(() => new Promise((resolve) => {
      pending.push((body) => resolve(new Response(JSON.stringify(body), { status: 200 })));
    })));
    const dispatch = vi.fn();
    const { rerender } = render(<TestStep state={{ ...state, remoteName: 'old' }} dispatch={dispatch} />, { wrapper: Wrapper });
    rerender(<TestStep state={{ ...state, remoteName: 'new' }} dispatch={dispatch} />);
    await waitFor(() => expect(pending).toHaveLength(2));
    await act(async () => pending[0]({ success: true, error: null }));
    expect(screen.getByText('Testing connection...')).toBeInTheDocument();
    expect(dispatch).not.toHaveBeenCalled();
    await act(async () => pending[1]({ success: false, error: 'bad key' }));
    expect(await screen.findByText('bad key')).toBeInTheDocument();
  });
});
