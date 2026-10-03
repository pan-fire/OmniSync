import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RemotePicker } from '@/components/shared/remote-picker';

vi.mock('@/i18n', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const base = { value: '', onValueChange: vi.fn(), placeholder: 'pick', emptyMessage: 'none configured' };

describe('RemotePicker', () => {
  it('does not claim there are no remotes while they load', () => {
    render(<RemotePicker {...base} isLoading />);
    expect(screen.getByRole('status')).toHaveTextContent('remotes.loading');
    expect(screen.queryByText('none configured')).not.toBeInTheDocument();
  });

  it('reports a failed load with a retry', async () => {
    const onRetry = vi.fn();
    render(<RemotePicker {...base} isError onRetry={onRetry} />);
    expect(screen.getByRole('alert')).toHaveTextContent('remotes.loadFailed');
    await userEvent.setup().click(screen.getByRole('button', { name: 'common.retry' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('says there are none once loaded empty', () => {
    render(<RemotePicker {...base} remotes={[]} />);
    expect(screen.getByText('none configured')).toBeInTheDocument();
  });
});
