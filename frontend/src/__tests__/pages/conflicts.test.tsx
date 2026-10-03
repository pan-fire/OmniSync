import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import ConflictsPage from '@/app/conflicts/page';
import { api } from '@/lib/api';

vi.mock('@/lib/api', () => ({ api: { getConflicts: vi.fn(), getProfiles: vi.fn(), resolveConflict: vi.fn() } }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock('@/components/layout/page-help', () => ({ PageHelp: () => null }));

const mocked = vi.mocked(api);

function conflict (id: number, slug: string) {
  return {
    id,
    job_id:          null,
    file_path:       `${slug}/file-${id}.txt`,
    local_modified:  '2026-09-27T10:00:00Z',
    remote_modified: '2026-09-27T11:00:00Z',
    resolved:        false,
    resolution:      null,
    profile_slug:    slug,
    profile_name:    slug,
  };
}

function renderPage () {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider><ConflictsPage /></I18nProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getConflicts.mockImplementation(async (profile?: string) =>
    [conflict(1, 'docs'), conflict(2, 'work')].filter((c) => !profile || c.profile_slug === profile) as never);
});

describe('Conflicts page profile filter', () => {
  it('asks the server for one profile\'s conflicts', async () => {
    mocked.getProfiles.mockResolvedValue([{ slug: 'docs', name: 'Docs' }, { slug: 'work', name: 'Work' }] as never);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText('work/file-2.txt')).toBeInTheDocument();
    expect(mocked.getConflicts).toHaveBeenLastCalledWith(undefined);

    await user.selectOptions(await screen.findByLabelText('Profile'), 'docs');
    await waitFor(() => expect(mocked.getConflicts).toHaveBeenLastCalledWith('docs'));
    await waitFor(() => expect(screen.queryByText('work/file-2.txt')).not.toBeInTheDocument());
    expect(screen.getByText('docs/file-1.txt')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Profile'), 'All profiles');
    expect(await screen.findByText('work/file-2.txt')).toBeInTheDocument();
  });

  it('has no filter with a single profile', async () => {
    mocked.getProfiles.mockResolvedValue([{ slug: 'docs', name: 'Docs' }] as never);
    renderPage();
    await screen.findByText('docs/file-1.txt');
    expect(screen.queryByLabelText('Profile')).not.toBeInTheDocument();
  });
});
