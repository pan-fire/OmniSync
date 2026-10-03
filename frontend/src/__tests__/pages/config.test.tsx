import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ConfigPage from '@/app/config/page';

vi.mock('@/i18n', () => ({ useTranslation: () => ({ t: (k: string) => k, locale: 'en' }) }));
const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));
vi.mock('@/hooks/use-config', () => ({
  useConfig:       () => ({ data: { log_level: 'INFO', history_days: 90 }, isLoading: false }),
  useUpdateConfig: () => ({ mutate, isPending: false }),
}));
vi.mock('@/hooks/use-remotes', () => ({ useRemotes: () => ({ data: [], isLoading: false, isError: false }) }));
vi.mock('@/components/config/remote-manager', () => ({ RemoteManager: () => null }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock('@/components/layout/page-help', () => ({ PageHelp: () => null }));

beforeEach(() => mutate.mockClear());

describe('Config page: job history retention', () => {
  it('shows the saved value and saves a new one', () => {
    render(<ConfigPage />);
    const input = screen.getByLabelText('config.historyDays');
    expect(input).toHaveValue(90);
    const save = screen.getByRole('button', { name: 'common.save' });
    expect(save).toBeDisabled(); // nothing changed yet

    fireEvent.change(input, { target: { value: '30' } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(mutate).toHaveBeenCalledWith({ log_level: 'INFO', history_days: 30 });
  });

  it('accepts 0 (keep everything) and refuses values out of range', () => {
    render(<ConfigPage />);
    const input = screen.getByLabelText('config.historyDays');
    const save = screen.getByRole('button', { name: 'common.save' });

    fireEvent.change(input, { target: { value: '5000' } });
    expect(save).toBeDisabled();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('config.historyDaysInvalid')).toBeInTheDocument();

    fireEvent.change(input, { target: { value: '0' } });
    expect(save).toBeEnabled();
    expect(screen.getByText('config.historyDaysHelp')).toBeInTheDocument();
  });
});
