import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { Trans, useTranslation } from 'react-i18next';
import * as z from 'zod';
import { Button } from '@/components/ui/button';
import { Form, FormControl, FormField, FormItem, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Toggle } from '@/components/ui/toggle';
import { useToast } from '@/components/ui/use-toast';
import { useServerHealth } from '@/lib/hooks/useServer';
import { usePlatform } from '@/platform/PlatformContext';
import { useServerStore } from '@/stores/serverStore';
import { LanguageSelect } from './LanguageSelect';
import { SettingRow, SettingSection } from './SettingRow';
import { ThemeSelect } from './ThemeSelect';

function makeConnectionSchema(invalidUrl: string) {
  return z.object({
    serverUrl: z.string().url(invalidUrl),
  });
}

type ConnectionFormValues = { serverUrl: string };

export function GeneralPage() {
  const { t } = useTranslation();
  const platform = usePlatform();
  const serverUrl = useServerStore((state) => state.serverUrl);
  const setServerUrl = useServerStore((state) => state.setServerUrl);
  const keepServerRunningOnClose = useServerStore((state) => state.keepServerRunningOnClose);
  const setKeepServerRunningOnClose = useServerStore((state) => state.setKeepServerRunningOnClose);
  const mode = useServerStore((state) => state.mode);
  const setMode = useServerStore((state) => state.setMode);
  const { toast } = useToast();
  const { data: health, isLoading, error: healthError } = useServerHealth();

  const resolver = useMemo(
    () => zodResolver(makeConnectionSchema(t('settings.general.serverUrl.invalidUrl'))),
    [t],
  );
  const form = useForm<ConnectionFormValues>({
    resolver,
    defaultValues: { serverUrl },
  });

  useEffect(() => {
    form.reset({ serverUrl });
  }, [serverUrl, form]);

  // Re-run validation when the locale changes so existing error messages retranslate.
  useEffect(() => {
    if (form.formState.errors.serverUrl) {
      form.trigger('serverUrl');
    }
  }, [t, form]);

  const { isDirty } = form.formState;

  function onSubmit(data: ConnectionFormValues) {
    setServerUrl(data.serverUrl);
    form.reset(data);
    toast({
      title: t('settings.general.serverUrl.updatedTitle'),
      description: t('settings.general.serverUrl.updatedDescription', { url: data.serverUrl }),
    });
  }

  return (
    <div className="space-y-8 max-w-2xl">
      <SettingSection>
        <SettingRow
          title={t('settings.general.serverUrl.title')}
          description={t('settings.general.serverUrl.description')}
          action={
            <ConnectionStatus health={health} isLoading={isLoading} healthError={healthError} />
          }
        >
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="flex gap-2">
              <FormField
                control={form.control}
                name="serverUrl"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormControl>
                      <Input placeholder="http://127.0.0.1:17493" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {isDirty && (
                <Button type="submit" size="sm">
                  {t('common.save')}
                </Button>
              )}
            </form>
          </Form>
        </SettingRow>

        <SettingRow
          title={t('settings.general.keepServerRunning.title')}
          description={t('settings.general.keepServerRunning.description')}
          htmlFor="keepServerRunning"
          action={
            <Toggle
              id="keepServerRunning"
              checked={keepServerRunningOnClose}
              onCheckedChange={(checked: boolean) => {
                setKeepServerRunningOnClose(checked);
                platform.lifecycle.setKeepServerRunning(checked).catch((error) => {
                  console.error('Failed to sync setting to Rust:', error);
                  setKeepServerRunningOnClose(!checked);
                  toast({
                    title: t('settings.general.keepServerRunning.failedTitle'),
                    description: t('settings.general.keepServerRunning.failedDescription'),
                    variant: 'destructive',
                  });
                  return;
                });
                toast({
                  title: t('settings.general.keepServerRunning.updatedTitle'),
                  description: checked
                    ? t('settings.general.keepServerRunning.runningDescription')
                    : t('settings.general.keepServerRunning.stoppedDescription'),
                });
              }}
            />
          }
        />

        {platform.metadata.isTauri && (
          <SettingRow
            title={t('settings.general.networkAccess.title')}
            description={t('settings.general.networkAccess.description')}
            htmlFor="allowNetworkAccess"
            action={
              <Toggle
                id="allowNetworkAccess"
                checked={mode === 'remote'}
                onCheckedChange={(checked: boolean) => {
                  setMode(checked ? 'remote' : 'local');
                  toast({
                    title: t('settings.general.networkAccess.updatedTitle'),
                    description: checked
                      ? t('settings.general.networkAccess.enabled')
                      : t('settings.general.networkAccess.disabled'),
                  });
                }}
              />
            }
          />
        )}

        <SettingRow
          title={t('settings.language.label')}
          description={t('settings.language.description')}
          action={<LanguageSelect />}
        />

        <SettingRow
          title={t('settings.theme.label')}
          description={t('settings.theme.description')}
          action={<ThemeSelect />}
        />
      </SettingSection>

      <ApiReferenceCard serverUrl={serverUrl} />
    </div>
  );
}

function ConnectionStatus({
  health,
  isLoading,
  healthError,
}: {
  health: ReturnType<typeof useServerHealth>['data'];
  isLoading: boolean;
  healthError: ReturnType<typeof useServerHealth>['error'];
}) {
  const { t } = useTranslation();
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-full border border-border/60 px-3 py-1">
        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
        <span className="text-xs text-muted-foreground">
          {t('settings.general.connection.connecting')}
        </span>
      </div>
    );
  }
  if (healthError) {
    return (
      <div className="flex items-center gap-2 rounded-full border border-destructive/30 px-3 py-1">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full rounded-full bg-destructive/40" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-destructive" />
        </span>
        <span className="text-xs text-destructive">{t('settings.general.connection.offline')}</span>
      </div>
    );
  }
  if (health) {
    return (
      <div className="flex items-center gap-2 rounded-full border border-accent/30 px-3 py-1">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-accent shadow-[0_0_6px_1px_hsl(var(--accent)/0.5)]" />
        </span>
        <span className="text-xs text-muted-foreground">
          {t('settings.general.connection.online')}
        </span>
      </div>
    );
  }
  return null;
}

function ApiReferenceCard({ serverUrl }: { serverUrl: string }) {
  const { t } = useTranslation();
  const endpoints = [
    { method: 'POST', path: '/generate', label: t('settings.general.api.endpoints.generate') },
    { method: 'GET', path: '/health', label: t('settings.general.api.endpoints.health') },
    { method: 'GET', path: '/profiles', label: t('settings.general.api.endpoints.profiles') },
    { method: 'GET', path: '/history', label: t('settings.general.api.endpoints.history') },
  ];

  return (
    <div className="rounded-lg border border-border/60 p-4 space-y-3">
      <div>
        <h3 className="text-sm font-medium">{t('settings.general.api.title')}</h3>
        <p className="text-sm text-muted-foreground">
          <Trans
            i18nKey="settings.general.api.description"
            values={{ url: serverUrl }}
            components={{
              code: <code className="text-xs bg-muted px-1 py-0.5 rounded font-mono" />,
            }}
          />
        </p>
      </div>
      <div className="space-y-1">
        {endpoints.map((ep) => (
          <div key={ep.path} className="flex items-center gap-2.5 py-1">
            <span
              className={`text-[10px] font-mono font-semibold w-9 text-center rounded px-1 py-px ${
                ep.method === 'POST' ? 'bg-accent/10 text-accent' : 'bg-muted text-muted-foreground'
              }`}
            >
              {ep.method}
            </span>
            <code className="text-xs font-mono text-muted-foreground">{ep.path}</code>
            <span className="text-xs text-muted-foreground/50 ml-auto">{ep.label}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        <a
          href={`${serverUrl}/docs`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent hover:underline"
        >
          {t('settings.general.api.viewReference')}
        </a>
      </p>
    </div>
  );
}
