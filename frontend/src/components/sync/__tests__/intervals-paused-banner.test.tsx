import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { IntervalsPausedBanner } from '../intervals-paused-banner';
import type { PausedProfileSummary } from '@/types';

// --- Mocks ---

const mockMutate = vi.fn();
vi.mock('@/hooks/use-profile-sync', () => ({
  useResumeProfileIntervals: () => ({ mutate: mockMutate, isPending: false }),
}));

vi.mock('@/i18n', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

function makePaused (overrides: Partial<PausedProfileSummary> = {}): PausedProfileSummary {
  return {
    slug:            'default',
    name:            'Default',
    pending_changes: 3,
    paused_at:       '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('IntervalsPausedBanner', () => {
  beforeEach(() => mockMutate.mockClear());

  it('renders nothing for empty array', () => {
    const { container } = render(<IntervalsPausedBanner pausedProfiles={[]} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders single profile compact layout', () => {
    render(<IntervalsPausedBanner pausedProfiles={[makePaused()]} />);
    expect(screen.getByTestId('intervals-paused-banner')).toBeInTheDocument();
    expect(screen.getByText('Default')).toBeInTheDocument();
  });

  it('renders multiple profiles as list', () => {
    const profiles = [
      makePaused({ slug: 'work', name: 'Work' }),
      makePaused({ slug: 'personal', name: 'Personal' }),
    ];
    render(<IntervalsPausedBanner pausedProfiles={profiles} />);
    expect(screen.getByText('Work')).toBeInTheDocument();
    expect(screen.getByText('Personal')).toBeInTheDocument();
  });

  it('disables Resume when pending_changes > 0', () => {
    render(<IntervalsPausedBanner pausedProfiles={[makePaused({ pending_changes: 5 })]} />);
    const btn = screen.getByTestId('resume-default');
    expect(btn).toBeDisabled();
  });

  it('enables Resume when pending_changes === 0', () => {
    render(<IntervalsPausedBanner pausedProfiles={[makePaused({ pending_changes: 0 })]} />);
    const btn = screen.getByTestId('resume-default');
    expect(btn).not.toBeDisabled();
  });

  it('calls mutate on Resume click', async () => {
    const user = userEvent.setup();
    render(<IntervalsPausedBanner pausedProfiles={[makePaused({ pending_changes: 0 })]} />);
    await user.click(screen.getByTestId('resume-default'));
    expect(mockMutate).toHaveBeenCalledOnce();
  });

  it('Review link targets correct profile slug', () => {
    render(<IntervalsPausedBanner pausedProfiles={[makePaused({ slug: 'my-proj' })]} />);
    const link = screen.getByRole('link', { name: 'intervalsPaused.review' });
    expect(link).toHaveAttribute('href', '/profiles/my-proj?tab=differences');
  });
});
