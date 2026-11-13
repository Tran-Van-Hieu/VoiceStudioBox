import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, FlaskConical, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import { type AuditionJob, studioApi } from '@/lib/api/studio';
import { useProfile } from '@/lib/hooks/useProfiles';
import { ErrorNote, Panel, PlayButton } from './common';

/** One row per model: can it read this language, and how close is it to the original voice. */
export function AuditionPanel({ profileId, language }: { profileId: string; language: string }) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const { data: profile } = useProfile(profileId);
  const [jobId, setJobId] = useState<string | null>(null);

  const { data: options } = useQuery({
    queryKey: ['auditionOptions', language],
    queryFn: () => studioApi.auditionOptions(language),
  });
  const { data: saved } = useQuery({
    queryKey: ['voiceModels', profileId],
    queryFn: () => studioApi.voiceModels(profileId),
  });
  const { data: job } = useQuery({
    queryKey: ['labJob', jobId],
    queryFn: () => studioApi.labJob<AuditionJob>(jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const done = q.state.data?.stage === 'done';
      if (done) client.invalidateQueries({ queryKey: ['voiceModels', profileId] });
      return done ? false : 1500;
    },
  });
  const running = !!jobId && job?.stage !== 'done';

  const start = useMutation({
    mutationFn: () => studioApi.startAudition(profileId, language),
    onSuccess: (j) => setJobId(j.id),
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });
  const setDefault = useMutation({
    mutationFn: (engine: string) =>
      apiClient.updateProfile(profileId, {
        name: profile?.name ?? '',
        language: profile?.language ?? language,
        default_engine: engine,
      } as Parameters<typeof apiClient.updateProfile>[1]),
    onSuccess: () => client.invalidateQueries({ queryKey: ['profiles'] }),
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });

  const results = { ...saved, ...job?.results };
  const ranked = [...(options ?? [])].sort(
    (a, b) => (results[b.key]?.similarity ?? -1) - (results[a.key]?.similarity ?? -1),
  );
  const top = ranked.find((o) => results[o.key]);

  return (
    <Panel title={t('studio.audition.title')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{t('studio.audition.hint')}</p>
        <Button size="sm" disabled={running || start.isPending} onClick={() => start.mutate()}>
          {running ? (
            <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
          ) : (
            <FlaskConical className="h-3.5 w-3.5 mr-1.5" />
          )}
          {running ? (job?.current ?? t('studio.audition.running')) : t('studio.audition.run')}
        </Button>
      </div>
      <div className="divide-y rounded-md border">
        {ranked.map((option) => {
          const row = results[option.key];
          const error = job?.errors[option.key];
          return (
            <div key={option.key} className="flex items-center gap-3 px-3 py-2 text-sm">
              <span className="flex-1 min-w-0 truncate">
                {option.name}
                {top?.key === option.key && (
                  <span className="ml-2 text-xs text-accent">{t('studio.audition.best')}</span>
                )}
              </span>
              {option.unavailable ? (
                <span className="text-xs text-muted-foreground">
                  {t(`studio.audition.${option.unavailable}`)}
                </span>
              ) : row ? (
                <>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {t('studio.quality.voice', { value: Math.round(row.similarity * 100) })} ·{' '}
                    {t('studio.quality.words', { value: Math.round(row.text_score * 100) })}
                  </span>
                  <PlayButton url={studioApi.auditionUrl(profileId, option.key)} />
                  {profile?.default_engine === option.engine ? (
                    <Check
                      className="h-4 w-4 text-emerald-500"
                      aria-label={t('studio.audition.isDefault')}
                    />
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8"
                      onClick={() => setDefault.mutate(option.engine)}
                    >
                      {t('studio.audition.makeDefault')}
                    </Button>
                  )}
                </>
              ) : error ? (
                <ErrorNote>{error}</ErrorNote>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {t('studio.audition.notTested')}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
