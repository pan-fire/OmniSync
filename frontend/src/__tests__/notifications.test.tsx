import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import fc from 'fast-check';
import { I18nProvider } from '@/i18n';
import { ChannelCard } from '@/components/notifications/channel-card';
import { TestNotificationButton } from '@/components/notifications/test-notification-button';
import { NotificationHistory } from '@/components/notifications/notification-history';
import type { ChannelConfig, ChannelStatusInfo, NotificationSeverity } from '@/types';

// --- Mock fetch ---

let fetchMock: ReturnType<typeof vi.fn>;

function mockFetchResponse (data: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(data),
  });
}

beforeEach(() => {
  fetchMock = vi.fn().mockImplementation(() =>
    mockFetchResponse({ items: [], total: 0 })
  );
  global.fetch = fetchMock as typeof fetch;
});

function createWrapper () {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries:   { retry: false },
      mutations: { retry: false },
    },
  });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

// --- ChannelCard tests ---

describe('ChannelCard', () => {
  const baseConfig: ChannelConfig = { enabled: true, min_severity: 'warning' };
  const availableStatus: ChannelStatusInfo = { available: true };
  const unavailableStatus: ChannelStatusInfo = { available: false };

  it('renders channel name, toggle, and severity selector', () => {
    render(
      <ChannelCard
        channelName="webpush"
        config={baseConfig}
        status={availableStatus}
        onToggle={() => {}}
        onSeverityChange={() => {}}
      />,
      { wrapper: createWrapper() }
    );

    expect(screen.getByText('Web Push')).toBeInTheDocument();
    expect(screen.getByText('Available')).toBeInTheDocument();
    expect(screen.getByRole('switch')).toBeChecked();
  });

  it('shows unavailable badge when channel is not available', () => {
    render(
      <ChannelCard
        channelName="host_native"
        config={{ enabled: false, min_severity: 'error' }}
        status={{ available: false }}
        onToggle={() => {}}
        onSeverityChange={() => {}}
      />,
      { wrapper: createWrapper() }
    );

    expect(screen.getByText('Unavailable')).toBeInTheDocument();
  });

  it('shows setup instructions when enabled but unavailable', () => {
    render(
      <ChannelCard
        channelName="webpush"
        config={{ enabled: true, min_severity: 'warning' }}
        status={unavailableStatus}
        onToggle={() => {}}
        onSeverityChange={() => {}}
      />,
      { wrapper: createWrapper() }
    );

    expect(screen.getByText(/Enable browser notifications/)).toBeInTheDocument();
  });

  it('does not show setup instructions when disabled', () => {
    render(
      <ChannelCard
        channelName="webpush"
        config={{ enabled: false, min_severity: 'warning' }}
        status={{ available: false }}
        onToggle={() => {}}
        onSeverityChange={() => {}}
      />,
      { wrapper: createWrapper() }
    );

    expect(screen.queryByText(/Enable browser notifications/)).not.toBeInTheDocument();
  });

  it('fires onToggle callback when switch is toggled', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();

    render(
      <ChannelCard
        channelName="webpush"
        config={baseConfig}
        status={availableStatus}
        onToggle={onToggle}
        onSeverityChange={() => {}}
      />,
      { wrapper: createWrapper() }
    );

    await user.click(screen.getByRole('switch'));
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});

// --- PBT: severity badge renders for all severity values ---

describe('PBT: severity badge renders for all severities', () => {
  const allSeverities: NotificationSeverity[] = ['debug', 'info', 'warning', 'error'];

  it('for any NotificationSeverity, ChannelCard renders without error', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...allSeverities),
        (severity) => {
          const config: ChannelConfig = { enabled: true, min_severity: severity };
          const status: ChannelStatusInfo = { available: true };

          const { unmount } = render(
            <ChannelCard
              channelName="webpush"
              config={config}
              status={status}
              onToggle={() => {}}
              onSeverityChange={() => {}}
            />,
            { wrapper: createWrapper() }
          );

          expect(screen.getByRole('switch')).toBeInTheDocument();
          unmount();
        }
      ),
      { numRuns: 20 }
    );
  });
});

// --- TestNotificationButton ---

describe('TestNotificationButton', () => {
  it('renders send test button', () => {
    render(<TestNotificationButton />, { wrapper: createWrapper() });
    expect(screen.getByText('Send test')).toBeInTheDocument();
  });

  it('shows loading state when mutation is pending', async () => {
    const user = userEvent.setup();
    // Make fetch hang to keep mutation pending
    fetchMock.mockImplementation(() => new Promise(() => {}));

    render(<TestNotificationButton />, { wrapper: createWrapper() });
    await user.click(screen.getByRole('button'));

    await waitFor(() => {
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });
  });
});

// --- NotificationHistory ---

describe('NotificationHistory', () => {
  it('renders history toggle button', () => {
    render(<NotificationHistory />, { wrapper: createWrapper() });
    expect(screen.getByText('History')).toBeInTheDocument();
  });

  it('shows entries when expanded', async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/notifications/history')) {
        return mockFetchResponse({
          items: [
            {
              id:                 1,
              event_type:         'test',
              severity:           'info',
              title:              'Test notification',
              body:               'body text',
              timestamp:          new Date().toISOString(),
              channels_delivered: ['webpush'],
            },
          ],
          total: 1,
        });
      }
      return mockFetchResponse({});
    });

    render(<NotificationHistory />, { wrapper: createWrapper() });
    await user.click(screen.getByText('History'));

    await waitFor(() => {
      expect(screen.getByText('Test notification')).toBeInTheDocument();
      expect(screen.getByText('Info')).toBeInTheDocument();
      expect(screen.getByText('via Web Push')).toBeInTheDocument();
    });
  });

  it('shows no history message when empty', async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/notifications/history')) {
        return mockFetchResponse({ items: [], total: 0 });
      }
      return mockFetchResponse({});
    });

    render(<NotificationHistory />, { wrapper: createWrapper() });
    await user.click(screen.getByText('History'));

    await waitFor(() => {
      expect(screen.getByText('No notifications yet')).toBeInTheDocument();
    });
  });
});

// --- Severity selector fires onSeverityChange (task 21.1) ---

describe('ChannelCard severity selector', () => {
  // Radix Select uses pointer capture and scrollIntoView, which jsdom lacks.
  beforeEach(() => {
    Element.prototype.hasPointerCapture ??= () => false;
    Element.prototype.releasePointerCapture ??= () => {};
    Element.prototype.scrollIntoView ??= () => {};
  });

  it('fires onSeverityChange with the chosen severity', async () => {
    const user = userEvent.setup();
    const onSeverityChange = vi.fn();

    render(
      <ChannelCard
        channelName="webpush"
        config={{ enabled: true, min_severity: 'warning' }}
        status={{ available: true }}
        onToggle={() => {}}
        onSeverityChange={onSeverityChange}
      />,
      { wrapper: createWrapper() }
    );

    const trigger = screen.getByRole('combobox', { name: 'Min severity' });
    expect(trigger).toHaveTextContent('Warning');

    await user.click(trigger);
    await user.click(await screen.findByRole('option', { name: 'Error' }));

    expect(onSeverityChange).toHaveBeenCalledTimes(1);
    expect(onSeverityChange).toHaveBeenCalledWith('error');
  });
});

// --- History badge colour per severity (task 21.1) ---

const SEVERITY_LABELS: Record<NotificationSeverity, string> = {
  debug:   'Debug',
  info:    'Info',
  warning: 'Warning',
  error:   'Error',
};

// The colour family NotificationHistory uses for each severity's badge.
const SEVERITY_COLOUR: Record<NotificationSeverity, string> = {
  error:   'red',
  warning: 'amber',
  info:    'blue',
  debug:   'zinc',
};

// The text shade on the light background (AA contrast); dark mode uses -400.
const LIGHT_SHADE: Record<NotificationSeverity, number> = {
  error:   700,
  warning: 800,
  info:    700,
  debug:   700,
};

function historyEntry (id: number, severity: NotificationSeverity) {
  return {
    id,
    event_type:         'test',
    severity,
    title:              `Entry ${severity} ${id}`,
    body:               '',
    timestamp:          new Date().toISOString(),
    channels_delivered: [],
  };
}

function mockHistory (items: ReturnType<typeof historyEntry>[]) {
  fetchMock.mockImplementation((url: string) => {
    if (url.includes('/notifications/history')) {
      return mockFetchResponse({ items, total: items.length });
    }
    return mockFetchResponse({});
  });
}

function badgeFor (title: string) {
  const row = screen.getByText(title).closest('div')!.parentElement!;
  return row.querySelector('[data-slot="badge"]') as HTMLElement;
}

describe('NotificationHistory severity badges', () => {
  const allSeverities: NotificationSeverity[] = ['debug', 'info', 'warning', 'error'];

  it('gives each severity its own badge colour', async () => {
    const user = userEvent.setup();
    mockHistory(allSeverities.map((s, i) => historyEntry(i + 1, s)));

    render(<NotificationHistory />, { wrapper: createWrapper() });
    await user.click(screen.getByText('History'));
    await screen.findByText('Entry error 4');

    for (const [i, severity] of allSeverities.entries()) {
      const badge = badgeFor(`Entry ${severity} ${i + 1}`);
      const colour = SEVERITY_COLOUR[severity];
      expect(badge).toHaveTextContent(SEVERITY_LABELS[severity]);
      expect(badge).toHaveClass(
        `bg-${colour}-500/15`, `text-${colour}-${LIGHT_SHADE[severity]}`, `dark:text-${colour}-400`, `border-${colour}-500/30`
      );
      for (const other of Object.values(SEVERITY_COLOUR).filter((c) => c !== colour)) {
        expect(badge.className).not.toContain(`${other}-`);
      }
    }
  });

  it('PBT: every severity maps to a valid badge style', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom(...allSeverities),
        async (severity) => {
          mockHistory([historyEntry(1, severity)]);
          const user = userEvent.setup();
          const { unmount } = render(<NotificationHistory />, { wrapper: createWrapper() });
          try {
            await user.click(screen.getByText('History'));
            await screen.findByText(`Entry ${severity} 1`);
            const badge = badgeFor(`Entry ${severity} 1`);
            const colour = SEVERITY_COLOUR[severity];
            expect(badge).toHaveAttribute('data-variant', 'outline');
            expect(badge).toHaveTextContent(SEVERITY_LABELS[severity]);
            expect(badge).toHaveClass(
              `bg-${colour}-500/15`, `text-${colour}-${LIGHT_SHADE[severity]}`, `dark:text-${colour}-400`, `border-${colour}-500/30`
            );
          } finally {
            unmount();
          }
        }
      ),
      { numRuns: 12 }
    );
  });
});
