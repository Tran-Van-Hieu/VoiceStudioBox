import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { type ManagedModel, studioApi } from '@/lib/api/studio';
import { ErrorNote, Panel } from './common';

const RUNNING = new Set(['downloading', 'testing']);

function gigabytes(bytes = 0) {
  return (bytes / 1024 ** 3).toFixed(2);
}

function ModelRow({
  model,
  onChanged,
}: {
  model: ManagedModel;
  onChanged: (rows?: ManagedModel[]) => void;
}) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const handleError = (e: Error) => toast({ title: e.message, variant: 'destructive' });
  const running = !!model.job && RUNNING.has(model.job.stage);
  const update = model.update;
  const licenseChanged = update?.available && update.license !== update.license_before;
  const hasNewer = model.active !== model.original;

  const install = useMutation({
    mutationFn: () => studioApi.installModel(model.repo, update?.latest as string),
    onSuccess: () => onChanged(),
    onError: handleError,
  });

  return (
    <div className="rounded-lg border p-3 space-y-2 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium truncate">{model.names.join(' / ')}</p>
          <p className="text-xs text-muted-foreground">
            {model.repo} · {model.active.slice(0, 8)}
            {model.active === model.original ? ` · ${t('studio.updates.original')}` : ''}
          </p>
        </div>
        {update?.available && !running && (
          <Button size="sm" disabled={install.isPending} onClick={() => install.mutate()}>
            {t('studio.updates.install')}
          </Button>
        )}
        {hasNewer && !running && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              if (window.confirm(t('studio.updates.confirmRollback'))) {
                studioApi
                  .activateModel(model.repo, model.original)
                  .then(onChanged)
                  .catch(handleError);
              }
            }}
          >
            <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
            {t('studio.updates.rollback')}
          </Button>
        )}
      </div>
      {update?.error && <ErrorNote>{t('studio.updates.checkFailed')}</ErrorNote>}
      {update?.available && (
        <div className="text-xs text-muted-foreground">
          <p>{update.title}</p>
          <p>
            {update.date && `${new Date(update.date).toLocaleDateString()} · `}
            {t('studio.updates.size', {
              size: gigabytes(update.download_bytes),
              files: update.changed_files?.length ?? 0,
            })}
          </p>
          {licenseChanged && (
            <p className="text-amber-500">
              {t('studio.updates.licenseChanged', {
                before: update.license_before ?? '?',
                after: update.license ?? '?',
              })}
            </p>
          )}
        </div>
      )}
      {update && !update.available && !update.error && (
        <p className="text-xs text-muted-foreground">{t('studio.updates.latest')}</p>
      )}
      {model.job && (
        <p className="text-xs flex items-center gap-1.5">
          {running && <Loader2 className="h-3 w-3 animate-spin" />}
          {model.job.message}
        </p>
      )}
      {model.versions.length > 1 && (
        <div className="divide-y rounded-md border text-xs">
          {model.versions.map((v) => (
            <div key={v.commit} className="flex items-center gap-2 px-2 py-1.5">
              <span className="font-mono">{v.commit.slice(0, 8)}</span>
              <span className="text-muted-foreground flex-1 truncate">
                {v.active && `${t('studio.updates.active')} · `}
                {v.original && `${t('studio.updates.original')} · `}
                {t(`studio.updates.status.${v.status}`, { defaultValue: v.status })}
                {v.note ? ` · ${v.note}` : ''}
              </span>
              {!v.active && v.present && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6"
                  onClick={() =>
                    studioApi.activateModel(model.repo, v.commit).then(onChanged).catch(handleError)
                  }
                >
                  {t('studio.updates.use')}
                </Button>
              )}
              {!v.active && !v.original && v.present && (
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-6 w-6"
                  aria-label={t('studio.updates.delete')}
                  onClick={() => {
                    if (window.confirm(t('studio.updates.confirmDelete'))) {
                      studioApi
                        .deleteModelVersion(model.repo, v.commit)
                        .then(onChanged)
                        .catch(handleError);
                    }
                  }}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ModelUpdates() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const key = ['modelUpdates'];
  const { data } = useQuery({
    queryKey: key,
    queryFn: () => studioApi.models(),
    refetchInterval: (q) =>
      q.state.data?.some((m) => m.job && RUNNING.has(m.job.stage)) ? 2000 : false,
  });
  const onChanged = (rows?: ManagedModel[]) => {
    if (rows) client.setQueryData(key, rows);
    else client.invalidateQueries({ queryKey: key });
  };
  const check = useMutation({
    mutationFn: () => studioApi.checkModels(),
    onSuccess: onChanged,
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });
  return (
    <Panel title={t('studio.updates.title')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{t('studio.updates.hint')}</p>
        <Button
          size="sm"
          variant="outline"
          disabled={check.isPending}
          onClick={() => check.mutate()}
        >
          {check.isPending ? (
            <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          )}
          {t('studio.updates.check')}
        </Button>
      </div>
      {data?.map((m) => (
        <ModelRow key={m.repo} model={m} onChanged={onChanged} />
      ))}
    </Panel>
  );
}
