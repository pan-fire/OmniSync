'use client';

import { useState } from 'react';
import { HelpCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useTranslation } from '@/i18n';

interface PageHelpProps {
  pageKey: 'dashboard' | 'config' | 'jobs' | 'logs' | 'conflicts' | 'profiles' | 'remotes';
}

function renderMarkdownLite (text: string) {
  return text.split('\n').map((line, i) => {
    // Bold
    const parts = line.split(/\*\*(.*?)\*\*/g);
    const rendered = parts.map((part, j) =>
      j % 2 === 1
        ? (
        <span key={j} className="font-semibold text-foreground">
          {part}
        </span>
          )
        : (
        <span key={j}>{part}</span>
          )
    );

    // Backtick inline code
    const withCode = rendered.flatMap((node, j) => {
      if (typeof node.props.children !== 'string') return [node];
      const codeParts = (node.props.children as string).split(/`(.*?)`/g);
      if (codeParts.length === 1) return [node];
      return codeParts.map((cp, k) =>
        k % 2 === 1
          ? (
          <code
            key={`${j}-${k}`}
            className="rounded bg-muted px-1 py-0.5 text-xs font-mono"
          >
            {cp}
          </code>
            )
          : (
          <span key={`${j}-${k}`}>{cp}</span>
            )
      );
    });

    if (line.trim() === '') return <br key={i} />;

    // Bullet points
    if (line.trim().startsWith('•') || line.trim().startsWith('-')) {
      return (
        <p key={i} className="ms-4 text-sm text-muted-foreground">
          {withCode}
        </p>
      );
    }

    // Numbered list
    if (/^\d+\./.test(line.trim())) {
      return (
        <p key={i} className="ms-4 text-sm text-muted-foreground">
          {withCode}
        </p>
      );
    }

    return (
      <p key={i} className="text-sm text-muted-foreground">
        {withCode}
      </p>
    );
  });
}

export function PageHelp ({ pageKey }: PageHelpProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('help.button')}
        >
          <HelpCircle className="h-5 w-5" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg max-h-[80vh]">
        <DialogHeader>
          <DialogTitle>{t(`help.${pageKey}.title`)}</DialogTitle>
          <DialogDescription className="sr-only">
            {t(`help.${pageKey}.title`)}
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[60vh] pe-4">
          <div className="space-y-1">
            {renderMarkdownLite(t(`help.${pageKey}.content`))}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
