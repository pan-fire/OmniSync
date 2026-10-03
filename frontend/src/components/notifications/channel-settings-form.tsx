'use client';

import { useId, useState, type FormEvent, type ReactNode } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useSaveChannelSettings } from '@/hooks/use-notifications';
import { useTranslation } from '@/i18n';
import type {
  ChannelConfig,
  ChannelConfigUpdate,
  EmailSettings,
  NtfySettings,
  SmtpSecurity,
  WebhookSettings,
} from '@/types';

/** Channels that have a settings form. */
export const CONFIGURABLE_CHANNELS = ['webhook', 'ntfy', 'email'] as const;

export function isConfigurable (name: string): boolean {
  return (CONFIGURABLE_CHANNELS as readonly string[]).includes(name);
}

// --- Validation (the backend checks again; these catch typos early) ---

const HEADER_NAME = /^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$/;
const RESERVED_HEADERS = ['host', 'content-length', 'content-type', 'transfer-encoding', 'connection', 'upgrade', 'te'];
const NTFY_TOPIC = /^[-_A-Za-z0-9]{1,64}$/;
const MAIL_ADDRESS = /^[^\s@,<>"]{1,64}@[^\s@,<>"]{1,255}$/;

/** Error key for a webhook or ntfy URL, or null. */
export function urlError (url: string, allowHttp: boolean): string | null {
  const value = url.trim();
  if (!value) return 'urlRequired';
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return 'urlScheme';
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return 'urlScheme';
  if (parsed.username || parsed.password) return 'urlCredentials';
  if (parsed.protocol === 'http:' && !allowHttp) return 'httpNotAllowed';
  return null;
}

export interface HeaderRow { name: string; value: string; valueSet: boolean }

export function webhookErrors (url: string, allowHttp: boolean, headers: HeaderRow[]): Record<string, string> {
  const errors: Record<string, string> = {};
  const u = urlError(url, allowHttp);
  if (u) errors.url = u;
  const seen = new Set<string>();
  headers.forEach((h, i) => {
    const name = h.name.trim();
    if (!HEADER_NAME.test(name) || RESERVED_HEADERS.includes(name.toLowerCase())) {
      errors[`header-${i}`] = 'headerName';
    } else if (seen.has(name.toLowerCase())) {
      errors[`header-${i}`] = 'headerDuplicate';
    } else if (!h.value && !h.valueSet) {
      errors[`header-${i}`] = 'headerValue';
    }
    seen.add(name.toLowerCase());
  });
  return errors;
}

export function ntfyErrors (server: string, topic: string, allowHttp: boolean): Record<string, string> {
  const errors: Record<string, string> = {};
  const u = urlError(server, allowHttp);
  if (u) errors.server = u;
  if (!NTFY_TOPIC.test(topic.trim())) errors.topic = 'topic';
  return errors;
}

export function splitAddresses (text: string): string[] {
  return text.split(/[,;\s]+/).map((a) => a.trim()).filter(Boolean);
}

export function emailErrors (host: string, port: string, from: string, to: string): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!/^[A-Za-z0-9.:\-[\]]{1,253}$/.test(host.trim())) errors.host = 'host';
  const p = Number(port);
  if (!Number.isInteger(p) || p < 1 || p > 65535) errors.port = 'port';
  if (!MAIL_ADDRESS.test(from.trim())) errors.from_addr = 'from';
  const recipients = splitAddresses(to);
  if (recipients.length === 0 || recipients.some((a) => !MAIL_ADDRESS.test(a))) errors.to = 'to';
  return errors;
}

// --- Fields ---

interface FieldProps {
  id:            string;
  label:         string;
  value:         string;
  onChange:      (v: string) => void;
  error?:        string;
  help?:         string;
  type?:         string;
  placeholder?:  string;
  autoComplete?: string;
  inputMode?:    'numeric' | 'email' | 'url' | 'text';
}

function Field ({ id, label, value, onChange, error, help, type = 'text', placeholder, autoComplete, inputMode }: FieldProps) {
  const { t } = useTranslation();
  const describedBy = [error ? `${id}-error` : null, help ? `${id}-help` : null].filter(Boolean).join(' ') || undefined;
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        placeholder={placeholder}
        autoComplete={autoComplete ?? 'off'}
        inputMode={inputMode}
        className="h-8 text-xs"
        dir="ltr"
      />
      {help && <p id={`${id}-help`} className="text-[11px] text-muted-foreground">{help}</p>}
      {error && (
        <p id={`${id}-error`} className="text-[11px] text-destructive">
          {t(`notifications.form.errors.${error}`)}
        </p>
      )}
    </div>
  );
}

function SecretField ({
  id, label, value, onChange, isSet, onClear, cleared,
}: {
  id:       string;
  label:    string;
  value:    string;
  onChange: (v: string) => void;
  isSet:    boolean;
  onClear?: () => void;
  cleared?: boolean;
}) {
  const { t } = useTranslation();
  const placeholder = isSet && !cleared ? t('notifications.form.secretSet') : t('notifications.form.optional');
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">{label}</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          type="password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete="new-password"
          className="h-8 text-xs"
          dir="ltr"
        />
        {isSet && onClear && !cleared && (
          <Button type="button" variant="ghost" size="sm" className="h-8 text-xs" onClick={onClear}>
            {t('notifications.form.clearSecret')}
          </Button>
        )}
      </div>
    </div>
  );
}

function AllowHttp ({ id, checked, onChange }: { id: string; checked: boolean; onChange: (v: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Checkbox id={id} checked={checked} onCheckedChange={onChange} aria-describedby={`${id}-help`} />
        <Label htmlFor={id} className="text-xs">{t('notifications.form.allowHttp')}</Label>
      </div>
      <p id={`${id}-help`} className="text-[11px] text-muted-foreground">{t('notifications.form.allowHttpHelp')}</p>
    </div>
  );
}

// --- Forms ---

interface FormShellProps {
  channel:  string;
  onSubmit: () => ChannelConfigUpdate | null;
  children: ReactNode;
}

function FormShell ({ channel, onSubmit, children }: FormShellProps) {
  const { t } = useTranslation();
  const save = useSaveChannelSettings();
  const [serverError, setServerError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setServerError(null);
    const update = onSubmit();
    if (!update) return;
    save.mutate({ channel, update }, {
      onError: (err: Error) => setServerError(err.message || t('notifications.saveFailed')),
    });
  };

  return (
    <form onSubmit={submit} className="space-y-3 rounded-md border border-sidebar-border p-3" noValidate>
      {children}
      {serverError && (
        <Alert variant="destructive" role="alert">
          <AlertDescription className="text-xs">{serverError}</AlertDescription>
        </Alert>
      )}
      <div className="flex justify-end">
        <Button type="submit" size="sm" className="h-8 text-xs" disabled={save.isPending}>
          {save.isPending ? t('notifications.form.saving') : t('notifications.form.save')}
        </Button>
      </div>
    </form>
  );
}

function WebhookForm ({ settings }: { settings: WebhookSettings }) {
  const { t } = useTranslation();
  const id = useId();
  const [url, setUrl] = useState(settings.url);
  const [allowHttp, setAllowHttp] = useState(settings.allow_http);
  const [headers, setHeaders] = useState<HeaderRow[]>(
    settings.headers.map((h) => ({ name: h.name, value: '', valueSet: h.value_set }))
  );
  const [errors, setErrors] = useState<Record<string, string>>({});

  const onSubmit = (): ChannelConfigUpdate | null => {
    const found = webhookErrors(url, allowHttp, headers);
    setErrors(found);
    if (Object.keys(found).length > 0) return null;
    return {
      webhook: {
        url:        url.trim(),
        allow_http: allowHttp,
        headers:    headers.map((h) => ({ name: h.name.trim(), value: h.value })),
      },
    };
  };

  const setHeader = (i: number, patch: Partial<HeaderRow>) =>
    setHeaders((rows) => rows.map((row, j) => (j === i ? { ...row, ...patch } : row)));

  return (
    <FormShell channel="webhook" onSubmit={onSubmit}>
      <Field
        id={`${id}-url`} label={t('notifications.form.url')} value={url} onChange={setUrl}
        error={errors.url} help={t('notifications.form.urlHelp')} placeholder="https://" inputMode="url"
      />
      <AllowHttp id={`${id}-http`} checked={allowHttp} onChange={setAllowHttp} />
      <fieldset className="space-y-2">
        <legend className="text-xs font-medium">{t('notifications.form.headers')}</legend>
        {headers.map((h, i) => (
          <div key={i} className="space-y-1">
            <div className="flex gap-2">
              <Input
                aria-label={t('notifications.form.headerName')}
                value={h.name}
                onChange={(e) => setHeader(i, { name: e.target.value })}
                placeholder="Authorization"
                aria-invalid={errors[`header-${i}`] ? true : undefined}
                className="h-8 text-xs"
                dir="ltr"
              />
              <Input
                aria-label={t('notifications.form.headerValue')}
                type="password"
                value={h.value}
                onChange={(e) => setHeader(i, { value: e.target.value })}
                placeholder={h.valueSet ? t('notifications.form.secretSet') : 'Bearer …'}
                autoComplete="new-password"
                className="h-8 text-xs"
                dir="ltr"
              />
              <Button
                type="button" variant="ghost" size="sm" className="h-8 px-2"
                aria-label={t('notifications.form.removeHeader')}
                onClick={() => setHeaders((rows) => rows.filter((_, j) => j !== i))}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
            {errors[`header-${i}`] && (
              <p className="text-[11px] text-destructive">{t(`notifications.form.errors.${errors[`header-${i}`]}`)}</p>
            )}
          </div>
        ))}
        {headers.length < 10 && (
          <Button
            type="button" variant="outline" size="sm" className="h-7 gap-1 text-xs"
            onClick={() => setHeaders((rows) => [...rows, { name: '', value: '', valueSet: false }])}
          >
            <Plus className="h-3.5 w-3.5" />
            {t('notifications.form.addHeader')}
          </Button>
        )}
      </fieldset>
    </FormShell>
  );
}

function NtfyForm ({ settings }: { settings: NtfySettings }) {
  const { t } = useTranslation();
  const id = useId();
  const [server, setServer] = useState(settings.server || 'https://ntfy.sh');
  const [topic, setTopic] = useState(settings.topic);
  const [allowHttp, setAllowHttp] = useState(settings.allow_http);
  const [token, setToken] = useState('');
  const [username, setUsername] = useState(settings.username);
  const [password, setPassword] = useState('');
  const [clear, setClear] = useState<('token' | 'password')[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const onSubmit = (): ChannelConfigUpdate | null => {
    const found = ntfyErrors(server, topic, allowHttp);
    setErrors(found);
    if (Object.keys(found).length > 0) return null;
    return {
      ntfy: {
        server:     server.trim(),
        topic:      topic.trim(),
        allow_http: allowHttp,
        username:   username.trim(),
        ...(token && { token }),
        ...(password && { password }),
        ...(clear.length > 0 && { clear: clear.filter((c) => (c === 'token' ? !token : !password)) }),
      },
    };
  };

  return (
    <FormShell channel="ntfy" onSubmit={onSubmit}>
      <Field
        id={`${id}-server`} label={t('notifications.form.server')} value={server} onChange={setServer}
        error={errors.server} inputMode="url"
      />
      <Field
        id={`${id}-topic`} label={t('notifications.form.topic')} value={topic} onChange={setTopic}
        error={errors.topic} help={t('notifications.form.topicHelp')}
      />
      <AllowHttp id={`${id}-http`} checked={allowHttp} onChange={setAllowHttp} />
      <SecretField
        id={`${id}-token`} label={t('notifications.form.token')} value={token} onChange={setToken}
        isSet={settings.token_set} cleared={clear.includes('token')}
        onClear={() => setClear((c) => [...c, 'token'])}
      />
      <Field
        id={`${id}-user`} label={t('notifications.form.username')} value={username} onChange={setUsername}
        autoComplete="off"
      />
      <SecretField
        id={`${id}-password`} label={t('notifications.form.password')} value={password} onChange={setPassword}
        isSet={settings.password_set} cleared={clear.includes('password')}
        onClear={() => setClear((c) => [...c, 'password'])}
      />
    </FormShell>
  );
}

const SECURITIES: SmtpSecurity[] = ['starttls', 'tls', 'none'];

function EmailForm ({ settings }: { settings: EmailSettings }) {
  const { t } = useTranslation();
  const id = useId();
  const [host, setHost] = useState(settings.host);
  const [port, setPort] = useState(String(settings.port || 587));
  const [security, setSecurity] = useState<SmtpSecurity>(settings.security || 'starttls');
  const [username, setUsername] = useState(settings.username);
  const [password, setPassword] = useState('');
  const [clearPassword, setClearPassword] = useState(false);
  const [from, setFrom] = useState(settings.from_addr);
  const [to, setTo] = useState(settings.to.join(', '));
  const [errors, setErrors] = useState<Record<string, string>>({});

  const onSubmit = (): ChannelConfigUpdate | null => {
    const found = emailErrors(host, port, from, to);
    setErrors(found);
    if (Object.keys(found).length > 0) return null;
    return {
      email: {
        host:      host.trim(),
        port:      Number(port),
        security,
        username:  username.trim(),
        from_addr: from.trim(),
        to:        splitAddresses(to),
        ...(password && { password }),
        ...(clearPassword && !password && { clear: ['password' as const] }),
      },
    };
  };

  const changeSecurity = (value: SmtpSecurity) => {
    // Follow the usual port of the new mode unless the user chose another one.
    const usual: Record<SmtpSecurity, string> = { starttls: '587', tls: '465', none: '25' };
    if (port === usual[security]) setPort(usual[value]);
    setSecurity(value);
  };

  return (
    <FormShell channel="email" onSubmit={onSubmit}>
      <div className="grid gap-3 sm:grid-cols-[1fr_6rem]">
        <Field id={`${id}-host`} label={t('notifications.form.host')} value={host} onChange={setHost} error={errors.host} />
        <Field
          id={`${id}-port`} label={t('notifications.form.port')} value={port} onChange={setPort}
          error={errors.port} inputMode="numeric"
        />
      </div>
      <div className="space-y-1">
        <span className="text-xs font-medium" id={`${id}-security`}>{t('notifications.form.security')}</span>
        <Select value={security} onValueChange={(v) => changeSecurity(v as SmtpSecurity)}>
          <SelectTrigger className="h-8 text-xs" aria-labelledby={`${id}-security`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SECURITIES.map((s) => (
              <SelectItem key={s} value={s} className="text-xs">{t(`notifications.form.securityMode.${s}`)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {security === 'none' && (
          <p className="text-[11px] text-amber-600 dark:text-amber-400">{t('notifications.form.plainWarning')}</p>
        )}
      </div>
      <Field id={`${id}-user`} label={t('notifications.form.username')} value={username} onChange={setUsername} />
      <SecretField
        id={`${id}-password`} label={t('notifications.form.password')} value={password} onChange={setPassword}
        isSet={settings.password_set} cleared={clearPassword} onClear={() => setClearPassword(true)}
      />
      <Field
        id={`${id}-from`} label={t('notifications.form.from')} value={from} onChange={setFrom}
        error={errors.from_addr} inputMode="email" placeholder="omnisync@example.com"
      />
      <Field
        id={`${id}-to`} label={t('notifications.form.to')} value={to} onChange={setTo}
        error={errors.to} help={t('notifications.form.toHelp')} inputMode="email"
      />
    </FormShell>
  );
}

/** The settings form of a webhook, ntfy or email channel; null for other channels. */
export function ChannelSettingsForm ({ channelName, config }: { channelName: string; config: ChannelConfig }) {
  if (channelName === 'webhook') {
    return <WebhookForm settings={config.webhook ?? { url: '', allow_http: false, headers: [] }} />;
  }
  if (channelName === 'ntfy') {
    return (
      <NtfyForm settings={config.ntfy ?? {
        server: 'https://ntfy.sh', topic: '', allow_http: false, username: '', token_set: false, password_set: false,
      }} />
    );
  }
  if (channelName === 'email') {
    return (
      <EmailForm settings={config.email ?? {
        host: '', port: 587, security: 'starttls', username: '', password_set: false, from_addr: '', to: [],
      }} />
    );
  }
  return null;
}
