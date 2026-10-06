import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { api } from '@/lib/api';
import {
  useCreateProfile,
  useDeleteProfile,
  useProfile,
  useProfiles,
  useToggleProfile,
  useUpdateProfile,
} from '@/hooks/use-profiles';
import type { ProfileCreateRequest, ProfileStatus } from '@/types';

vi.mock('@/lib/api', () => ({
  api: {
    getProfiles:    vi.fn(),
    getProfile:     vi.fn(),
    createProfile:  vi.fn(),
    updateProfile:  vi.fn(),
    deleteProfile:  vi.fn(),
    enableProfile:  vi.fn(),
    disableProfile: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockedApi = vi.mocked(api);
let queryClient: QueryClient;

const PROFILE = { slug: 'docs', name: 'Docs', state: 'idle' } as ProfileStatus;

function wrapper ({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="en">{children}</I18nProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});

describe('useProfiles / useProfile', () => {
  // A running sync is followed every 2 s; an idle list only every 30 s.
  it.each([
    ['polls fast while one profile syncs', 'pushing', 2],
    ['polls slowly while all are idle', 'idle', 1],
  ] as const)('loads the profile list and %s', async (_label, state, callsAfter2s) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockedApi.getProfiles.mockResolvedValue([PROFILE, { ...PROFILE, slug: 'photos', state }]);
      const { result } = renderHook(() => useProfiles(), { wrapper });
      await waitFor(() => expect(result.current.data).toHaveLength(2));
      expect(mockedApi.getProfiles).toHaveBeenCalledTimes(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      await waitFor(() => expect(mockedApi.getProfiles).toHaveBeenCalledTimes(callsAfter2s));
    } finally {
      vi.useRealTimers();
    }
  });

  it('loads one profile and does not fetch without a slug', async () => {
    mockedApi.getProfile.mockResolvedValue(PROFILE);
    const { result } = renderHook(() => useProfile('docs'), { wrapper });
    await waitFor(() => expect(result.current.data?.name).toBe('Docs'));
    expect(mockedApi.getProfile).toHaveBeenCalledWith('docs');

    renderHook(() => useProfile(''), { wrapper });
    expect(mockedApi.getProfile).toHaveBeenCalledTimes(1);
  });
});

describe('useCreateProfile', () => {
  it('creates the profile and names it', async () => {
    mockedApi.createProfile.mockResolvedValue(PROFILE);
    const { result } = renderHook(() => useCreateProfile(), { wrapper });
    await act(async () => { await result.current.mutateAsync({ name: 'Docs' } as ProfileCreateRequest); });
    expect(toast.success).toHaveBeenCalledWith('Profile "Docs" created');
  });

  it('shows the server error, or the create fallback', async () => {
    mockedApi.createProfile.mockRejectedValueOnce(new Error('name taken')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useCreateProfile(), { wrapper });
    act(() => { result.current.mutate({ name: 'Docs' } as ProfileCreateRequest); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('name taken'));
    act(() => { result.current.mutate({ name: 'Docs' } as ProfileCreateRequest); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to create profile'));
  });
});

describe('useUpdateProfile', () => {
  it('updates the profile and names it', async () => {
    mockedApi.updateProfile.mockResolvedValue({ ...PROFILE, name: 'Documents' });
    const { result } = renderHook(() => useUpdateProfile('docs'), { wrapper });
    await act(async () => { await result.current.mutateAsync({ name: 'Documents' }); });
    expect(mockedApi.updateProfile).toHaveBeenCalledWith('docs', { name: 'Documents' });
    expect(toast.success).toHaveBeenCalledWith('Profile "Documents" updated');
  });

  it('shows the server error, or the update fallback', async () => {
    mockedApi.updateProfile.mockRejectedValueOnce(new Error('invalid path')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useUpdateProfile('docs'), { wrapper });
    act(() => { result.current.mutate({ name: 'x' }); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('invalid path'));
    act(() => { result.current.mutate({ name: 'x' }); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to update profile'));
  });
});

describe('useDeleteProfile', () => {
  it('deletes the profile and refreshes the list', async () => {
    mockedApi.deleteProfile.mockResolvedValue(undefined as never);
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useDeleteProfile(), { wrapper });
    await act(async () => { await result.current.mutateAsync('docs'); });
    expect(mockedApi.deleteProfile).toHaveBeenCalledWith('docs');
    expect(toast.success).toHaveBeenCalledWith('Profile deleted');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['profiles'] });
  });

  it('shows the server error, or the delete fallback', async () => {
    mockedApi.deleteProfile.mockRejectedValueOnce(new Error('sync running')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useDeleteProfile(), { wrapper });
    act(() => { result.current.mutate('docs'); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('sync running'));
    act(() => { result.current.mutate('docs'); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to delete profile'));
  });
});

describe('useToggleProfile', () => {
  it('enables or disables the profile', async () => {
    mockedApi.enableProfile.mockResolvedValue(PROFILE);
    mockedApi.disableProfile.mockResolvedValue(PROFILE);
    const { result } = renderHook(() => useToggleProfile(), { wrapper });
    await act(async () => { await result.current.mutateAsync({ slug: 'docs', enabled: true }); });
    await act(async () => { await result.current.mutateAsync({ slug: 'docs', enabled: false }); });
    expect(mockedApi.enableProfile).toHaveBeenCalledWith('docs');
    expect(mockedApi.disableProfile).toHaveBeenCalledWith('docs');
  });

  it('shows the server error', async () => {
    mockedApi.disableProfile.mockRejectedValue(new Error('locked'));
    const { result } = renderHook(() => useToggleProfile(), { wrapper });
    act(() => { result.current.mutate({ slug: 'docs', enabled: false }); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('locked'));
  });
});
