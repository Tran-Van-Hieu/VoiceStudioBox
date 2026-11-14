import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, RefreshCw, Wand2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { type DesignTakeGroup, studioApi } from '@/lib/api/studio';
import { ALL_LANGUAGES } from '@/lib/constants/languages';
import { cn } from '@/lib/utils/cn';
import { LanguagePicker, Panel, PlayButton } from './common';

/**
 * A designed voice speaks every generation by cloning one fixed clip, so long
 * texts and separate lines stay the same person. This lists that clip's takes:
 * listen, see the scores, pick another, or make a fresh set.
 */
export function DesignTakesPanel({ profileId, language }: { profileId: string; language: string }) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const [lang, setLang] = useState(language);
  const key = ['designTakes', profileId];
  const { data: groups } = useQuery({
    queryKey: key,
    queryFn: () => studioApi.designTakes(profileId),
  });
  const refresh = () => client.invalidateQueries({ queryKey: key });
  const onError = (e: Error) => toast({ title: e.message, variant: 'destructive' });

  const create = useMutation({
    mutationFn: () => studioApi.createDesignTakes(profileId, lang),
    onSuccess: refresh,
    onError,
  });
  const redo = useMutation({
    mutationFn: (groupId: string) => studioApi.redoDesignTakes(profileId, groupId),
    onSuccess: refresh,
    onError,
  });
  const choose = useMutation({
    mutationFn: ({ groupId, index }: { groupId: string; index: number }) =>
      studioApi.chooseDesignTake(profileId, groupId, index),
    onSuccess: refresh,
    onError,
  });
  const busy = create.isPending || redo.isPending;

  return (
    <Panel title={t('studio.designTakes.title')}>
      <p className="text-xs text-muted-foreground">{t('studio.designTakes.hint')}</p>
      <div className="flex flex-wrap items-center gap-2">
        <LanguagePicker value={lang} onChange={setLang} className="h-8 w-48 text-xs" />
        <Button size="sm" variant="outline" disabled={busy} onClick={() => create.mutate()}>
          {create.isPending ? (
            <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
          ) : (
            <Wand2 className="h-3.5 w-3.5 mr-1.5" />
          )}
          {t('studio.designTakes.create')}
        </Button>
        {busy && (
          <span className="text-xs text-muted-foreground">{t('studio.designTakes.working')}</span>
        )}
      </div>
      {groups?.length === 0 && (
        <p className="text-xs text-muted-foreground">{t('studio.designTakes.none')}</p>
      )}
      {groups?.map((g) => (
        <TakeGroup
          key={g.id}
          profileId={profileId}
          group={g}
          busy={busy}
          onRedo={() => redo.mutate(g.id)}
          onChoose={(index) => choose.mutate({ groupId: g.id, index })}
        />
      ))}
    </Panel>
  );
}

function TakeGroup({
  profileId,
  group: g,
  busy,
  onRedo,
  onChoose,
}: {
  profileId: string;
  group: DesignTakeGroup;
  busy: boolean;
  onRedo: () => void;
  onChoose: (index: number) => void;
}) {
  const { t } = useTranslation();
  const language = ALL_LANGUAGES[g.language as keyof typeof ALL_LANGUAGES] ?? g.language;
  return (
    <div className="rounded-lg border p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium">
          {language}
          {g.emotion !== 'neutral' && ` · ${t(`emotion.names.${g.emotion}`, g.emotion)}`}
          {g.instruct && ` · ${g.instruct}`}
        </span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {t(
            g.picked_by === 'user'
              ? 'studio.designTakes.pickedByYou'
              : 'studio.designTakes.pickedAuto',
          )}
          <Button size="sm" variant="ghost" className="h-7" disabled={busy} onClick={onRedo}>
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            {t('studio.designTakes.redo')}
          </Button>
        </span>
      </div>
      <p className="text-xs text-muted-foreground line-clamp-2">{g.text}</p>
      <div className="grid gap-1.5">
        {g.takes.map((take, i) => {
          const chosen = i === g.chosen;
          return (
            <div
              key={`${g.created_at}-${take.seed}`}
              className={cn(
                'flex flex-wrap items-center gap-3 rounded-md border px-2 py-1.5 text-xs',
                chosen ? 'border-accent bg-accent/10' : 'border-transparent bg-muted/30',
              )}
            >
              <PlayButton
                url={`${studioApi.designTakeUrl(profileId, g.id, i)}?v=${g.created_at}`}
              />
              <span className="tabular-nums w-24">
                {t('studio.designTakes.natural', { value: take.mos?.toFixed(2) ?? '—' })}
              </span>
              <span
                className={cn('tabular-nums w-24', take.words < 0.9 && 'text-amber-500')}
                title={t('studio.designTakes.wordsHint')}
              >
                {t('studio.designTakes.words', { value: Math.round(take.words * 100) })}
              </span>
              <span className="tabular-nums text-muted-foreground">
                {take.seconds.toFixed(1)} s
              </span>
              <span className="ml-auto">
                {chosen ? (
                  <span className="flex items-center gap-1 text-accent font-medium">
                    <Check className="h-3.5 w-3.5" />
                    {t('studio.designTakes.inUse')}
                  </span>
                ) : (
                  <Button size="sm" variant="outline" className="h-7" onClick={() => onChoose(i)}>
                    {t('studio.designTakes.use')}
                  </Button>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
