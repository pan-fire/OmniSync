'use client';

import * as React from 'react';
import { CheckIcon } from 'lucide-react';

import { cn } from '@/lib/utils';

function Checkbox ({
  className,
  checked,
  onCheckedChange,
  ...props
}: React.ComponentProps<'button'> & {
  checked?:         boolean
  onCheckedChange?: (checked: boolean) => void
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      data-state={checked ? 'checked' : 'unchecked'}
      className={cn(
        'peer size-4 shrink-0 rounded-[4px] border border-primary shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground',
        className
      )}
      onClick={() => onCheckedChange?.(!checked)}
      {...props}
    >
      {checked && (
        <span className="flex items-center justify-center">
          <CheckIcon className="size-3.5" />
        </span>
      )}
    </button>
  );
}

export { Checkbox };
