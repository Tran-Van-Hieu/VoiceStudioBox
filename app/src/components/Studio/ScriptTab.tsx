import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Download,
  FileUp,
  ListMusic,
  Loader2,
  Play,
  RefreshCw,
  Scissors,
  Square,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
import { cn } from '@/lib/utils/cn';
import {
  ErrorNote,
  engineSupports,
  Field,
  heardOf,
  LanguagePicker,
  ModelPicker,
  Panel,
  PlayButton,
  parseEngine,
  QualityBadge,
  SliderField,
  useSaveBlob,
  VoicePicker,
  WordDiffList,
  wordDiffOf,
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
  selected,
  locked,
  onSelect,
  onText,
  onTake,
  onRender,
}: {
  index: number;
  segment: StudioSegment;
  selected: boolean;
  locked: boolean;
  onSelect: (on: boolean) => void;
  onText: (text: string) => void;
  onTake: (generationId: string) => void;
  onRender: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState(segment.text);
  useEffect(() => setText(segment.text), [segment.text]);
  const busy = segment.status === 'generating' || segment.status === 'loading_model';
  const done = !!segment.active && segment.status === 'completed';
  const ops = wordDiffOf(segment.final);
  return (
    <div
      className={cn(
        'grid gap-2 border-b px-3 py-2 md:grid-cols-[56px_minmax(0,1fr)_230px]',
        segment.quality === 'red' && 'bg-red-500/5',
      )}
    >
      <label className="flex items-start gap-1.5 pt-2 text-xs tabular-nums text-muted-foreground cursor-pointer">
        <Checkbox checked={selected} onCheckedChange={(v) => onSelect(!!v)} />
        {String(index + 1).padStart(3, '0')}
      </label>
      <div className="space-y-1 min-w-0">
        <Textarea
          rows={2}
          value={text}
          className="min-h-0 text-sm"
          onChange={(e) => setText(e.target.value)}
          onBlur={() => text !== segment.text && onText(text)}
        />
        {done && ops.length > 0 && <WordDiffList ops={ops} heard={heardOf(segment.final)} />}
        {segment.status === 'failed' && <ErrorNote>{segment.error}</ErrorNote>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground content-start pt-1">
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2"
          disabled={busy || locked || !text.trim()}
          onClick={onRender}
        >
          {busy ? (
            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3 mr-1" />
          )}
          {segment.active ? t('studio.check.regen') : t('studio.adv.make')}
        </Button>
        {done && (
          <PlayButton className="h-7 w-7" url={apiClient.getAudioUrl(segment.active as string)} />
        )}
        {segment.takes.length > 1 && (
          <Select value={segment.active ?? ''} onValueChange={onTake}>
            <SelectTrigger className="h-7 w-[84px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {segment.takes.map((take, i) => (
                <SelectItem key={take} value={take}>
                  {t('studio.check.take', { n: i + 1 })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {segment.quality && <QualityBadge level={segment.quality} />}
        <span className="tabular-nums">
          {t('studio.script.partLength', { count: text.length })}
        </span>
      </div>
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
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // A text edit saves on blur; regenerating right after must wait for that save.
  const pendingSave = useRef<Promise<unknown>>(Promise.resolve());
  const [playingAll, setPlayingAll] = useState(false);
  useEffect(() => () => playing.current?.pause(), []);

  const profile = useMemo(
    () => profiles?.find((p) => p.id === profileId) ?? null,
    [profiles, profileId],
  );
  // Picking a voice brings its language and engine along.
  const chooseVoice = (id: string | null) => {
    setProfileId(id);
    const picked = profiles?.find((p) => p.id === id);
    if (!picked) return;
    setLanguage(picked.language);
    if (picked.default_engine) setModel(picked.default_engine);
  };

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

  // A reopened script keeps the voice it was made with: regenerating a part
  // must not quietly switch to whatever voice happens to be first.
  const restored = useRef<string | null>(null);
  useEffect(() => {
    if (!project || restored.current === project.id) return;
    restored.current = project.id;
    const d = project.defaults;
    if (d.profile_id) setProfileId(d.profile_id);
    setLanguage(project.language);
    if (d.engine) setModel(d.engine === 'qwen' ? `qwen:${d.model_size ?? '1.7B'}` : d.engine);
    if (d.speed) setSpeed(d.speed);
    if (d.sentence_pause_ms !== undefined) setPause(d.sentence_pause_ms);
  }, [project]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: only seeds a fresh page
  useEffect(() => {
    if (projectId || profileId || !profiles?.length) return;
    const first = profiles.find((p) => (p.voice_type as string) !== 'import');
    if (first) chooseVoice(first.id);
  }, [profiles, projectId, profileId]);

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
      await pendingSave.current;
      await studioApi.updateStudio(id, { language, defaults });
      return studioApi.renderStudio(id, ids);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
    onError: handleError,
  });

  const patchPart = (segmentId: string, patch: Partial<StudioSegment>) => {
    if (!project) return;
    pendingSave.current = studioApi
      .updateStudio(project.id, {
        segments: project.segments.map((s) => (s.id === segmentId ? { ...s, ...patch } : s)),
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
  const failedIds = segments
    .filter((s) => s.status === 'failed' || s.quality === 'red')
    .map((s) => s.id);
  const chosen = segments.filter((s) => selected.has(s.id)).map((s) => s.id);
  const select = (id: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
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
            <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer w-fit">
              <Checkbox
                checked={chosen.length > 0 && chosen.length === segments.length}
                onCheckedChange={(v) =>
                  setSelected(v ? new Set(segments.map((s) => s.id)) : new Set())
                }
              />
              {t('studio.check.selectAll')}
            </label>
            <div className="rounded-md border max-h-[560px] overflow-y-auto">
              {segments.map((s, i) => (
                <PartRow
                  key={s.id}
                  index={i}
                  segment={s}
                  selected={selected.has(s.id)}
                  locked={!canGenerate || render.isPending}
                  onSelect={(on) => select(s.id, on)}
                  onText={(text) => patchPart(s.id, { text })}
                  onTake={(active) => patchPart(s.id, { active })}
                  onRender={() => render.mutate([s.id])}
                />
              ))}
            </div>
          </Panel>
        )}
      </div>

      <div className="space-y-4">
        <Panel title={t('studio.speak.voice')}>
          <Field label={t('studio.common.voice')}>
            <VoicePicker value={profileId} onChange={chooseVoice} />
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
                  {done ? t('studio.check.regenAll') : t('studio.script.generate')}
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
                {chosen.length > 0 && (
                  <Button
                    variant="outline"
                    className="w-full"
                    disabled={!canGenerate || render.isPending}
                    onClick={() => render.mutate(chosen)}
                  >
                    <RefreshCw className="h-4 w-4 mr-2" />
                    {t('studio.check.regenSelected', { count: chosen.length })}
                  </Button>
                )}
                {failedIds.length > 0 && (
                  <Button
                    variant="outline"
                    className="w-full border-red-500/50 text-red-500"
                    disabled={!canGenerate || render.isPending}
                    onClick={() => render.mutate(failedIds)}
                  >
                    <RefreshCw className="h-4 w-4 mr-2" />
                    {t('studio.check.regenFailed', { count: failedIds.length })}
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
              title={
                missing.length ? t('studio.check.unfinished', { count: missing.length }) : undefined
              }
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
