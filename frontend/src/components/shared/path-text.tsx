import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

/**
 * A file path, remote:path or other left-to-right technical string. It is
 * isolated and laid out left to right, so in Persian `/home/you/Sync`
 * and `gdrive:Backup` keep their order instead of being reordered by the
 * surrounding right-to-left text.
 */
export function PathText ({ className, ...props }: ComponentProps<'bdi'>) {
  return <bdi dir="ltr" className={cn('[unicode-bidi:isolate]', className)} {...props} />;
}
