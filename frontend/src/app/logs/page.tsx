'use client';

import { useState } from 'react';
import { LOGS_PAGE_SIZE, useLogs } from '@/hooks/use-logs';
import { LogViewer, type LogLevel } from '@/components/logs/log-viewer';
import { PageHelp } from '@/components/layout/page-help';
import { PageHeader } from '@/components/layout/page-header';
import { useTranslation } from '@/i18n';

export default function LogsPage () {
  const { t } = useTranslation();
  const [level, setLevel] = useState<LogLevel>('ALL');
  const [page, setPage] = useState(0);
  const logs = useLogs(page, level);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('logs.title')}
        actions={<PageHelp pageKey="logs" />}
      />
      <LogViewer
        logs={logs.data}
        onRefresh={() => logs.refetch()}
        isLoading={logs.isLoading}
        isError={logs.isError}
        isFetching={logs.isFetching}
        level={level}
        onLevelChange={(next) => { setLevel(next); setPage(0); }}
        page={page}
        // A full page means there may be older entries.
        hasNext={(logs.data?.length ?? 0) >= LOGS_PAGE_SIZE}
        onPageChange={setPage}
      />
    </div>
  );
}
