import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, FileUp, ListMusic, Loader2, Play, Scissors, Square } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import { type StudioSegment, studioApi } from '@/lib/api/studio';
import { useExtrasSettings } from '@/lib/hooks/useExtras';
import { useProfiles } from '@/lib/hooks/useProfiles';
import {
  engineSupports,
  ErrorNote,
  Field,
  LanguagePicker,
  ModelPicker,
  Panel,
  parseEngine,
  PlayButton,
  QualityBadge,
  SliderField,
  useSaveBlob,
  VoicePicker,
} from './common';

const PROJECT_KEY = 'voicebox.scriptProject';

function readSavedProject(): string | null {
  try {
    return localStorage.getItem(PROJECT_KEY);
  } catch {
    return null;
  }
}

function PartRow({
  index,
  segment,
  onText,
}: {
  index: number;
  segment: StudioSegment;
  onText: (text: string) => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState(segment.text);
  useEffect(() => setText(segment.text), [segment.text]);
  const busy = segment.status === 'generating' || segment.status === 'loading_model';
  return (
    <div className="grid gap-2 border-b px-3 py-2 md:grid-cols-[40px_minmax(0,1fr)_170px]">
      <span className="text-xs tabular-nums text-muted-foreground pt-2">
        {String(index + 1).padStart(3, '0')}
      </span>
      <Textarea
        rows={2}
        value={text}
        className="min-h-0 text-sm"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => text !== segment.text && onText(text)}
      />
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        {segment.active && segment.status === 'completed' && (
          <PlayButton className="h-7 w-7" url={apiClient.getAudioUrl(segment.active)} />
        )}
        {segment.quality && <QualityBadge level={segment.quality} />}
        <span className="tabular-nums">
          {t('studio.script.partLength', { count: text.length })}
        </span>
      </div>
      {segment.status === 'failed' && (
        <div className="md:col-start-2">
          <ErrorNote>{segment.error}</ErrorNote>
        </div>
      )}
    </div>
  );
}

/** Paste a whole script, cut it at whole sentences, and get numbered files or one. */
export function ScriptTab() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const saveBlob = useSaveBlob();
  const fileRef = useRef<HTMLInputElement>(null);
  const { data: profiles } = useProfiles();

  const [projectId, setProjectId] = useState<string | null>(readSavedProject);
  const [script, setScript] = useState('');
  const [maxChars, setMaxChars] = useState(500);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [language, setLanguage] = useState('vi');
  const [model, setModel] = useState<string | null>('omnivoice');
  const [fmt, setFmt] = useState('mp3');
  const [saving, setSaving] = useState(false);
  const { settings: extras } = useExtrasSettings();
  const [speedChoice, setSpeed] = useState<number | null>(null);
  const speed = speedChoice ?? extras?.speed ?? 1;
  const [pause, setPause] = useState(250);
  const playing = useRef<HTMLAudioElement | null>(null);
  const [playingAll, setPlayingAll] = useState(false);
  useEffect(() => () => playing.current?.pause(), []);

  const profile = useMemo(
    () => profiles?.find((p) => p.id === profileId) ?? null,
    [profiles, profileId],
  );
  useEffect(() => {
    if (!profileId && profiles?.length) {
      const first = profiles.find((p) => (p.voice_type as string) !== 'import');
      if (first) setProfileId(first.id);
    }
  }, [profiles, profileId]);
  useEffect(() => {
    if (!profile) return;
    setLanguage(profile.language);
    if (profile.default_engine) setModel(profile.default_engine);
  }, [profile]);

  const key = ['scriptProject', projectId];
  const { data: project, isError } = useQuery({
    queryKey: key,
    queryFn: () => studioApi.getStudio(projectId as string),
    enabled: !!projectId,
    retry: false,
    refetchInterval: (q) => (q.state.data?.pending ? 1500 : false),
  });
  useEffect(() => {
    if (isError) setProjectId(null);
  }, [isError]);

  const handleError = (e: Error) => toast({ title: e.message, variant: 'destructive' });
  const { engine, modelSize } = parseEngine(model ?? '');
  const defaults = {
    profile_id: profileId ?? undefined,
    engine,
    model_size: modelSize ?? undefined,
    sentence_pause_ms: pause,
    speed,
  };

  const split = useMutation({
    mutationFn: async () => {
      const name = script.trim().slice(0, 50);
      let id = project?.id ?? null;
      if (id) await studioApi.updateStudio(id, { name, language, defaults });
      else id = (await studioApi.createStudio({ name, language, defaults })).id;
      return studioApi.splitStudio(id, script, maxChars);
    },
    onSuccess: (p) => {
      try {
        localStorage.setItem(PROJECT_KEY, p.id);
      } catch {
        // Only means the parts are not reopened next time.
      }
      setProjectId(p.id);
      client.setQueryData(['scriptProject', p.id], p);
      client.invalidateQueries({ queryKey: ['studioList'] });
    },
    onError: handleError,
  });

  const render = useMutation({
    mutationFn: async (ids?: string[]) => {
      const id = project?.id as string;
      await studioApi.updateStudio(id, { language, defaults });
      return studioApi.renderStudio(id, ids);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
    onError: handleError,
  });

  const editPart = (segmentId: string, text: string) => {
    if (!project) return;
    studioApi
      .updateStudio(project.id, {
        segments: project.segments.map((s) => (s.id === segmentId ? { ...s, text } : s)),
      })
      .then((p) => client.setQueryData(key, p))
      .catch(handleError);
  };

  const download = async (mode: 'files' | 'merged') => {
    if (!project) return;
    setSaving(true);
    try {
      const blob = await studioApi.exportStudio(project.id, mode, fmt, false);
      const extension = mode === 'files' ? 'zip' : fmt;
      await saveBlob(`${project.name || 'kich-ban'}.${extension}`, blob, extension);
    } catch (e) {
      handleError(e as Error);
    } finally {
      setSaving(false);
    }
  };

  const segments = project?.segments ?? [];
  const done = segments.filter((s) => s.status === 'completed').length;
  const missing = segments.filter((s) => s.status !== 'completed').map((s) => s.id);
  const canGenerate = !!profileId && !!model && engineSupports(engine, language);

  // Finished parts one after another, in script order.
  const playAll = () => {
    playing.current?.pause();
    if (playingAll) return setPlayingAll(false);
    const urls = segments
      .filter((s) => s.active && s.status === 'completed')
      .map((s) => `${apiClient.getAudioUrl(s.active as string)}?t=${Date.now()}`);
    const next = (i: number) => {
      if (i >= urls.length) return setPlayingAll(false);
      const element = new Audio(urls[i]);
      element.onended = () => next(i + 1);
      playing.current = element;
      element.play();
    };
    setPlayingAll(true);
    next(0);
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px] pb-44 pt-2">
      <div className="space-y-4 min-w-0">
        <Panel>
          <div className="flex items-center justify-between gap-2">
            <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
              <FileUp className="h-3.5 w-3.5 mr-1.5" />
              {t('studio.speak.openFile')}
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept=".txt,text/plain"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) setScript(await file.text());
                e.target.value = '';
              }}
            />
            <span className="text-xs text-muted-foreground tabular-nums">
              {t('studio.speak.chars', { count: script.length })}
            </span>
          </div>
          <Textarea
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder={t('studio.script.placeholder')}
            className="min-h-[220px] resize-y text-[15px] leading-relaxed"
          />
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-40">
              <Field label={t('studio.script.maxChars')}>
                <Input
                  type="number"
                  min={20}
                  max={20000}
                  step={50}
                  value={maxChars}
                  onChange={(e) => setMaxChars(Number(e.target.value) || 0)}
                />
              </Field>
            </div>
            <Button
              disabled={!script.trim() || maxChars < 20 || split.isPending || project?.pending}
              onClick={() => split.mutate()}
            >
              {split.isPending ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Scissors className="h-4 w-4 mr-2" />
              )}
              {segments.length ? t('studio.script.resplit') : t('studio.script.split')}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t('studio.script.hint')}</p>
        </Panel>

        {segments.length > 0 && (
          <Panel
            title={`${t('studio.script.parts', { count: segments.length })} · ${t('studio.script.progress', { done, total: segments.length })}`}
          >
            <div className="rounded-md border max-h-[460px] overflow-y-auto">
              {segments.map((s, i) => (
                <PartRow key={s.id} index={i} segment={s} onText={(text) => editPart(s.id, text)} />
              ))}
            </div>
          </Panel>
        )}
      </div>

      <div className="space-y-4">
        <Panel title={t('studio.speak.voice')}>
          <Field label={t('studio.common.voice')}>
            <VoicePicker value={profileId} onChange={setProfileId} />
          </Field>
          <Field label={t('studio.common.language')}>
            <LanguagePicker value={language} onChange={setLanguage} />
          </Field>
          <Field label={t('studio.common.model')}>
            <ModelPicker value={model} onChange={setModel} language={language} profile={profile} />
          </Field>
          {!!model && !engineSupports(engine, language) && (
            <ErrorNote>{t('studio.speak.modelLanguage')}</ErrorNote>
          )}
          <SliderField
            label={t('studio.common.speed')}
            value={speed}
            min={0.5}
            max={2}
            step={0.05}
            format={(v) => `${v.toFixed(2)}×`}
            onChange={setSpeed}
          />
          <SliderField
            label={t('studio.speak.pause')}
            value={pause}
            min={0}
            max={1000}
            step={50}
            format={(v) => `${v} ms`}
            onChange={setPause}
          />
        </Panel>
        {segments.length > 0 && project && (
          <Panel title={t('studio.adv.output')}>
            {project.pending ? (
              <Button
                variant="destructive"
                className="w-full"
                onClick={() =>
                  studioApi
                    .stopStudio(project.id)
                    .then(() => client.invalidateQueries({ queryKey: key }))
                    .catch(handleError)
                }
              >
                <Square className="h-4 w-4 mr-2" />
                {t('studio.script.stop')}
              </Button>
            ) : (
              <>
                <Button
                  className="w-full"
                  disabled={!canGenerate || render.isPending}
                  onClick={() => render.mutate(undefined)}
                >
                  <Play className="h-4 w-4 mr-2" />
                  {t('studio.script.generate')}
                </Button>
                {missing.length > 0 && missing.length < segments.length && (
                  <Button
                    variant="outline"
                    className="w-full"
                    disabled={!canGenerate || render.isPending}
                    onClick={() => render.mutate(missing)}
                  >
                    {t('studio.script.generateMissing', { count: missing.length })}
                  </Button>
                )}
              </>
            )}
            <Button variant="outline" className="w-full" disabled={!done} onClick={playAll}>
              {playingAll ? (
                <Square className="h-4 w-4 mr-2" />
              ) : (
                <ListMusic className="h-4 w-4 mr-2" />
              )}
              {playingAll ? t('studio.script.stopPlay') : t('studio.script.playAll')}
            </Button>
            <Select value={fmt} onValueChange={setFmt}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="mp3">MP3</SelectItem>
                <SelectItem value="wav">WAV</SelectItem>
                <SelectItem value="flac">FLAC</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              className="w-full"
              disabled={!done || saving}
              onClick={() => download('files')}
            >
              <Download className="h-4 w-4 mr-2" />
              {t('studio.script.downloadZip')}
            </Button>
            <Button
              variant="outline"
              className="w-full"
              disabled={!done || saving}
              onClick={() => download('merged')}
            >
              <Download className="h-4 w-4 mr-2" />
              {t('studio.script.downloadMerged')}
            </Button>
          </Panel>
        )}
      </div>
    </div>
  );
}
