import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Eraser, ListX, Loader2, ShieldOff, Square } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useToast } from '@/components/ui/use-toast';
import { studioApi } from '@/lib/api/studio';
import { cn } from '@/lib/utils/cn';

function gb(mb: number | null | undefined): string {
  return mb === null || mb === undefined ? '–' : (mb / 1024).toFixed(1);
}

function Meter({
  label,
  used,
  total,
}: {
  label: string;
  used?: number | null;
  total?: number | null;
}) {
  const ratio = used && total ? used / total : 0;
  return (
    <span className="flex items-center gap-1.5 shrink-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="h-1.5 w-16 rounded-full bg-muted overflow-hidden">
        <span
          className={cn(
            'block h-full',
            ratio > 0.92 ? 'bg-red-500' : ratio > 0.75 ? 'bg-amber-500' : 'bg-accent',
          )}
          style={{ width: `${Math.min(100, ratio * 100)}%` }}
        />
      </span>
      <span className="tabular-nums">
        {gb(used)}/{gb(total)} GB
      </span>
    </span>
  );
}

function BarAction(props: {
  onClick: () => void;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={cn(
        'flex items-center gap-1.5 shrink-0 hover:underline disabled:opacity-60',
        props.className,
      )}
      disabled={props.disabled}
      onClick={props.onClick}
    >
      {props.children}
    </button>
  );
}

/** VRAM, RAM, loaded models, queue, and the Stop controls: always visible. */
export function StatusBar() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const { data, isError } = useQuery({
    queryKey: ['systemStatus'],
    queryFn: () => studioApi.status(),
    refetchInterval: 1500,
    retry: false,
  });
  const fail = (e: Error) => toast({ title: e.message, variant: 'destructive' });
  const refresh = () => client.invalidateQueries({ queryKey: ['systemStatus'] });
  const release = useMutation({
    mutationFn: () => studioApi.release(),
    onSuccess: (r) =>
      toast({ title: r.deferred ? t('studio.status.deferred') : t('studio.status.released') }),
    onError: fail,
  });
  const stop = useMutation({
    mutationFn: () => studioApi.stopJobs(),
    onSuccess: refresh,
    onError: fail,
  });
  const clear = useMutation({
    mutationFn: () => studioApi.clearQueue(),
    onSuccess: (r) => {
      refresh();
      toast({ title: t('studio.status.queueCleared', { count: r.cleared }) });
    },
    onError: fail,
  });
  const skip = useMutation({
    mutationFn: () => studioApi.skipCheck(),
    onSuccess: refresh,
    onError: fail,
  });

  const pending = release.isPending || data?.release_pending;
  const job = data?.job;
  const running = !!job?.running;
  return (
    <footer className="fixed bottom-0 left-0 right-0 z-[60] h-8 border-t bg-sidebar flex items-center gap-5 px-4 text-xs overflow-hidden">
      {isError || !data ? (
        <span className="text-muted-foreground">{t('studio.status.offline')}</span>
      ) : (
        <>
          <Meter label="VRAM" used={data.used_mb} total={data.total_mb} />
          <Meter label="RAM" used={data.ram_used_mb} total={data.ram_total_mb} />
          <span className="truncate text-muted-foreground min-w-0">
            {data.loaded_models.length
              ? data.loaded_models.map((m) => m.display_name.split(' (')[0]).join(', ')
              : t('studio.status.noModel')}
          </span>
          {!!data.queue_length && (
            <span className="shrink-0">
              {t('studio.status.queue', { count: data.queue_length })}
            </span>
          )}
          {!!job?.cooldown_seconds && (
            <span className="shrink-0 text-amber-500 tabular-nums">
              {t('studio.status.cooldown', { count: job.cooldown_seconds })}
            </span>
          )}
          <span className="ml-auto flex items-center gap-4 shrink-0">
            {job?.checking && (
              <BarAction
                className="text-amber-500"
                disabled={skip.isPending}
                onClick={() => skip.mutate()}
              >
                <ShieldOff className="h-3.5 w-3.5" />
                {t('studio.status.skipCheck')}
              </BarAction>
            )}
            {!!job?.queued && (
              <BarAction
                className="text-amber-500"
                disabled={clear.isPending}
                onClick={() => clear.mutate()}
              >
                <ListX className="h-3.5 w-3.5" />
                {t('studio.status.clearQueue')}
              </BarAction>
            )}
            {running && (
              <BarAction
                className="text-red-500"
                disabled={stop.isPending || job?.stopping}
                onClick={() => stop.mutate()}
              >
                {job?.stopping ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Square className="h-3.5 w-3.5" />
                )}
                {job?.stopping ? t('studio.status.stopping') : t('studio.status.stop')}
              </BarAction>
            )}
            <BarAction className="text-accent" disabled={pending} onClick={() => release.mutate()}>
              {pending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Eraser className="h-3.5 w-3.5" />
              )}
              {pending ? t('studio.status.releasing') : t('studio.status.release')}
            </BarAction>
          </span>
        </>
      )}
    </footer>
  );
}
