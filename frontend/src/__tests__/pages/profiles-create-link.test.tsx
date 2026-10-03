import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProfilesPage from '@/app/profiles/page';

vi.mock('@/i18n', () => ({ useTranslation: () => ({ t: (k: string) => k, locale: 'en' }) }));
const { mutation } = vi.hoisted(() => ({ mutation: () => ({ mutate: () => {}, mutateAsync: async () => ({}), isPending: false }) }));
const refetch = vi.fn();
let profilesQuery: Record<string, unknown> = { data: [], isError: false };
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles:      () => profilesQuery,
  useCreateProfile: mutation,
  useDeleteProfile: mutation,
  useToggleProfile: mutation,
}));
vi.mock('@/components/sync/sync-confirm-dialog', () => ({
  useConfirmedSync: () => ({ request: vi.fn(), syncNow: vi.fn(), isStarting: false, dialog: null }),
}));
vi.mock('@/components/profiles/profile-form', () => ({ ProfileForm: () => <div data-testid="profile-form" /> }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock('@/components/layout/page-help', () => ({ PageHelp: () => null }));

afterEach(() => {
  window.history.replaceState({}, '', '/');
  profilesQuery = { data: [], isError: false };
});

describe('Profiles page ?create=1', () => {
  it('opens the create dialog when the dashboard links here', async () => {
    window.history.replaceState({}, '', '/profiles?create=1');
    render(<ProfilesPage />);
    expect(await screen.findByTestId('profile-form')).toBeInTheDocument();
  });

  it('starts closed otherwise', () => {
    window.history.replaceState({}, '', '/profiles');
    render(<ProfilesPage />);
    expect(screen.queryByTestId('profile-form')).not.toBeInTheDocument();
  });
});

describe('Profiles page states', () => {
  it('shows a loading placeholder, not an empty page, while the profiles load', () => {
    profilesQuery = { data: undefined, isLoading: true, isError: false };
    render(<ProfilesPage />);
    expect(screen.getByRole('status', { name: 'common.loading' })).toBeInTheDocument();
    expect(screen.queryByText('profiles.empty')).not.toBeInTheDocument();
  });

  it('explains a failed load and retries', async () => {
    profilesQuery = { data: undefined, isLoading: false, isError: true, error: new Error('Backend down'), refetch };
    const user = userEvent.setup();
    render(<ProfilesPage />);
    expect(screen.getByRole('alert')).toHaveTextContent('profiles.loadListFailed: Backend down');
    await user.click(screen.getByRole('button', { name: 'common.retry' }));
    expect(refetch).toHaveBeenCalled();
  });
});
