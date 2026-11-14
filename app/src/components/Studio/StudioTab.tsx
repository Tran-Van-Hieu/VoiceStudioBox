import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowDown,
  ArrowUp,
  Download,
  FileUp,
  Loader2,
  Music,
  Play,
  Plus,
  RefreshCw,
  Square,
  Trash2,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
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
import { type StudioProject, type StudioSegment, studioApi } from '@/lib/api/studio';
import { EMOTIONS } from '@/lib/constants/emotions';
import { useProfiles } from '@/lib/hooks/useProfiles';
import { cn } from '@/lib/utils/cn';
import {
  ErrorNote,
  Field,
  FinalCheckView,
  formatSeconds,
  heardOf,
  LanguagePicker,
  ModelPicker,
  PageHeader,
  Panel,
  PlayButton,
  parseEngine,
  QualityBadge,
  useSaveBlob,
  VoicePicker,
  WordDiffList,
  wordDiffOf,
} from './common';
import { effectsChainFor, useEffectPresets, VoiceTuning } from './VoiceTuning';

function modelValue(s: StudioSegment) {
  if (!s.engine) return null;
  return s.model_size ? `${s.engine}:${s.model_size}` : s.engine;
}

function SegmentRow(props: {
  index: number;
  segment: StudioSegment;
  project: StudioProject;
  selected: boolean;
  onSelect: (value: boolean) => void;
  onChange: (patch: Partial<StudioSegment>) => void;
  onMove: (delta: number) => void;
  onRemove: () => void;
  onRender: () => void;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const { segment: s, project } = props;
  const { data: profiles } = useProfiles();
  const language = s.language ?? project.language;
  const profile = profiles?.find((p) => p.id === (s.profile_id ?? project.defaults.profile_id));
  const [open, setOpen] = useState(false);
  const busy = s.status === 'generating' || s.status === 'loading_model';
  return (
    <div className="rounded-lg border p-3 space-y-2">
      <div className="flex items-start gap-2">
        <span className="flex items-center gap-1.5 pt-2 shrink-0">
          <Checkbox
            checked={props.selected}
            aria-label={t('studio.adv.select')}
            onCheckedChange={(v) => props.onSelect(!!v)}
          />
          <span className="text-xs tabular-nums text-muted-foreground w-5">{props.index + 1}</span>
        </span>
        <Textarea
          value={s.text}
          onChange={(e) => props.onChange({ text: e.target.value })}
          rows={2}
          className="flex-1 min-h-0 resize-y"
          placeholder={t('studio.adv.textPlaceholder')}
        />
        <div className="flex flex-col gap-1 shrink-0">
          <Button
            size="icon"
            variant="ghost"
            className="h-6 w-6"
            aria-label={t('studio.adv.up')}
            onClick={() => props.onMove(-1)}
          >
            <ArrowUp className="h-3 w-3" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-6 w-6"
            aria-label={t('studio.adv.down')}
            onClick={() => props.onMove(1)}
          >
            <ArrowDown className="h-3 w-3" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-6 w-6"
            aria-label={t('studio.adv.remove')}
            onClick={props.onRemove}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </div>
      <div className="grid gap-2 pl-8 sm:grid-cols-2 xl:grid-cols-[1.3fr_1.3fr_1fr_0.7fr_0.7fr]">
        <VoicePicker
          className="h-8 text-xs"
          value={s.profile_id}
          onChange={(v) => props.onChange({ profile_id: v })}
          emptyLabel={`${s.speaker ? `${s.speaker} · ` : ''}${t('studio.common.voice')}: ${t('studio.adv.useDefault')}`}
        />
        <ModelPicker
          className="h-8 text-xs"
          value={modelValue(s)}
          language={language}
          profile={profile}
          emptyLabel={`${t('studio.common.model')}: ${t('studio.adv.useDefault')}`}
          onChange={(v) => {
            const parsed = v ? parseEngine(v) : { engine: null, modelSize: null };
            props.onChange({ engine: parsed.engine, model_size: parsed.modelSize });
          }}
        />
        <Select
          value={s.emotion ?? 'neutral'}
          onValueChange={(v) => props.onChange({ emotion: v === 'neutral' ? null : v })}
        >
          <SelectTrigger className="h-8 text-xs" title={t('emotion.label')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EMOTIONS.map((e) => (
              <SelectItem key={e.id} value={e.id}>
                {t(`emotion.names.${e.id}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          className="h-8 text-xs"
          type="number"
          min={0.5}
          max={2}
          step={0.05}
          aria-label={t('studio.common.speed')}
          title={t('studio.adv.speedHint')}
          placeholder={`${t('studio.common.speed')} ×`}
          value={s.speed ?? ''}
          onChange={(e) =>
            props.onChange({ speed: e.target.value ? Number(e.target.value) : null })
          }
        />
        <div className="relative">
          <Input
            className="h-8 text-xs pr-12"
            type="number"
            min={0}
            max={60}
            step={0.1}
            aria-label={t('studio.adv.pause')}
            title={t('studio.adv.pause')}
            value={s.pause_after_ms / 1000}
            onChange={(e) =>
              props.onChange({ pause_after_ms: Math.round((Number(e.target.value) || 0) * 1000) })
            }
          />
          <span className="pointer-events-none absolute right-2 top-2 text-[10px] text-muted-foreground">
            {t('studio.adv.pauseUnit')}
          </span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 pl-8 text-xs text-muted-foreground">
        <Button
          size="sm"
          variant="outline"
          className="h-7"
          disabled={busy || !s.text.trim()}
          onClick={props.onRender}
        >
          {busy ? (
            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3 mr-1" />
          )}
          {s.active ? t('studio.adv.remake') : t('studio.adv.make')}
        </Button>
        {s.active && s.status === 'completed' && (
          <PlayButton className="h-7 w-7" url={apiClient.getAudioUrl(s.active)} />
        )}
        {s.takes.length > 1 && (
          <Select value={s.active ?? ''} onValueChange={(v) => props.onChange({ active: v })}>
            <SelectTrigger className="h-7 w-[110px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {s.takes.map((take, i) => (
                <SelectItem key={take} value={take}>
                  {t('studio.adv.take', { n: i + 1 })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {s.duration ? <span className="tabular-nums">{formatSeconds(s.duration)}</span> : null}
        {s.quality && (
          <button type="button" onClick={() => setOpen(!open)}>
            <QualityBadge level={s.quality} />
          </button>
        )}
        {s.status === 'failed' && <ErrorNote>{s.error}</ErrorNote>}
      </div>
      {s.status === 'completed' && wordDiffOf(s.final).length > 0 && (
        <div className="pl-8">
          <WordDiffList ops={wordDiffOf(s.final)} heard={heardOf(s.final)} />
        </div>
      )}
      {open && s.final && s.active && (
        <div className="pl-8">
          <FinalCheckView final={s.final} generationId={s.active} onRepaired={props.onRefresh} />
        </div>
      )}
    </div>
  );
}

/** `embedded`: shown as a mode inside the Speak page, which already has the title. */
export function StudioTab({ embedded = false }: { embedded?: boolean }) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const saveBlob = useSaveBlob();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState<StudioSegment[] | null>(null);
  const [script, setScript] = useState('');
  const [exportFormat, setExportFormat] = useState('mp3');
  const [withSrt, setWithSrt] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState<{
    voice: string | null;
    emotion: string;
    speed: string;
    pause: string;
  }>({
    voice: null,
    emotion: 'keep',
    speed: '',
    pause: '',
  });
  const fileRef = useRef<HTMLInputElement>(null);
  const musicRef = useRef<HTMLInputElement>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data: projects } = useQuery({
    queryKey: ['studioList'],
    queryFn: () => studioApi.listStudio(),
  });
  useEffect(() => {
    if (!activeId && projects?.length) setActiveId(projects[0].id);
  }, [projects, activeId]);

  const key = ['studio', activeId];
  const { data: project } = useQuery({
    queryKey: key,
    queryFn: () => studioApi.getStudio(activeId as string),
    enabled: !!activeId,
    refetchInterval: (q) => (q.state.data?.pending ? 1500 : false),
  });
  const segments = draft ?? project?.segments ?? [];

  const apply = (p: StudioProject) => {
    client.setQueryData(key, p);
    setDraft(null);
  };
  const handleError = (e: Error) => toast({ title: e.message, variant: 'destructive' });

  const persist = (next: StudioSegment[]) =>
    studioApi.updateStudio(activeId as string, { segments: next }).then(apply);
  const change = (next: StudioSegment[]) => {
    setDraft(next);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => persist(next).catch(handleError), 500);
  };
  const flush = async () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    if (draft) await persist(draft);
  };

  const create = useMutation({
    mutationFn: () =>
      studioApi.createStudio({
        name: t('studio.adv.newName'),
        language: 'vi',
        defaults: { engine: 'omnivoice' },
      }),
    onSuccess: (p) => {
      client.invalidateQueries({ queryKey: ['studioList'] });
      setActiveId(p.id);
    },
    onError: handleError,
  });
  const updateProject = useMutation({
    mutationFn: (patch: Parameters<typeof studioApi.updateStudio>[1]) =>
      studioApi.updateStudio(activeId as string, patch),
    onSuccess: (p) => {
      client.setQueryData(key, p);
      client.invalidateQueries({ queryKey: ['studioList'] });
    },
    onError: handleError,
  });
  const { data: presets } = useEffectPresets();
  // The slider moves locally; the project is saved once it stops moving.
  const [denoise, setDenoise] = useState(0);
  const savedDenoise = project?.defaults.denoise ?? 0;
  // biome-ignore lint/correctness/useExhaustiveDependencies: reseed only when another project opens
  useEffect(() => setDenoise(savedDenoise), [project?.id]);
  useEffect(() => {
    if (!project || denoise === savedDenoise) return;
    const timer = setTimeout(
      () =>
        updateProject.mutate({
          defaults: { ...project.defaults, denoise: denoise > 0 ? denoise : undefined },
        }),
      400,
    );
    return () => clearTimeout(timer);
  }, [denoise, savedDenoise, project, updateProject]);
  const importScript = useMutation({
    mutationFn: async (file?: File) => {
      await flush();
      const form = new FormData();
      if (file) form.append('file', file);
      else form.append('script', script);
      return studioApi.importStudio(activeId as string, form);
    },
    onSuccess: (p) => {
      apply(p);
      setScript('');
    },
    onError: handleError,
  });
  const render = useMutation({
    mutationFn: async (ids?: string[]) => {
      await flush();
      return studioApi.renderStudio(activeId as string, ids);
    },
    onSuccess: (r) => {
      if (r.skipped.length) toast({ title: t('studio.adv.skipped', { count: r.skipped.length }) });
      client.invalidateQueries({ queryKey: key });
    },
    onError: handleError,
  });
  const music = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return studioApi.setMusic(activeId as string, form);
    },
    onSuccess: apply,
    onError: handleError,
  });

  const exportAll = async (mode: 'merged' | 'files') => {
    if (!project) return;
    setExporting(true);
    try {
      await flush();
      const blob = await studioApi.exportStudio(project.id, mode, exportFormat, withSrt);
      const zip = mode === 'files' || withSrt;
      await saveBlob(
        `${project.name}.${zip ? 'zip' : exportFormat}`,
        blob,
        zip ? 'zip' : exportFormat,
      );
    } catch (e) {
      handleError(e as Error);
    } finally {
      setExporting(false);
    }
  };

  const applyBulk = () => {
    const patch: Partial<StudioSegment> = {};
    if (bulk.voice) patch.profile_id = bulk.voice;
    if (bulk.emotion !== 'keep') patch.emotion = bulk.emotion === 'neutral' ? null : bulk.emotion;
    if (bulk.speed) patch.speed = Number(bulk.speed);
    if (bulk.pause) patch.pause_after_ms = Math.round(Number(bulk.pause) * 1000);
    change(
      segments.map((s) => (selected.size === 0 || selected.has(s.id) ? { ...s, ...patch } : s)),
    );
  };

  const total = segments.reduce((n, s) => n + (s.duration ?? 0) + s.pause_after_ms / 1000, 0);
  const missing = segments.filter((s) => !s.active).map((s) => s.id);
  const failedIds = segments
    .filter((s) => s.status === 'failed' || s.quality === 'red')
    .map((s) => s.id);
  const chosen = segments.filter((s) => selected.has(s.id)).map((s) => s.id);

  return (
    <div className="h-full flex flex-col min-h-0">
      <PageHeader
        title={embedded ? undefined : t('studio.adv.title')}
        subtitle={t('studio.adv.subtitle')}
        actions={
          <>
            <Select
              value={activeId ?? ''}
              onValueChange={(v) => {
                setDraft(null);
                setActiveId(v);
              }}
            >
              <SelectTrigger className="w-[220px] h-9">
                <SelectValue placeholder={t('studio.adv.choose')} />
              </SelectTrigger>
              <SelectContent>
                {projects?.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button size="sm" onClick={() => create.mutate()}>
              <Plus className="h-3.5 w-3.5 mr-1.5" />
              {t('studio.adv.new')}
            </Button>
          </>
        }
      />
      {!project ? (
        <div className="rounded-lg border border-dashed p-12 text-center text-sm text-muted-foreground">
          {t('studio.adv.none')}
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px] flex-1 min-h-0">
          <div className="overflow-y-auto pb-44 space-y-2 min-w-0">
            {segments.length > 0 && (
              <div
                className="flex h-7 gap-0.5 rounded-md bg-muted/40 p-1 overflow-hidden"
                aria-hidden="true"
              >
                {segments.map((s) => (
                  <span
                    key={s.id}
                    style={{ flexGrow: Math.max(1, s.duration ?? s.text.length / 15) }}
                    className={cn(
                      'rounded-sm',
                      s.quality === 'green' && 'bg-emerald-500/50',
                      s.quality === 'yellow' && 'bg-amber-500/60',
                      s.quality === 'red' && 'bg-red-500/60',
                      !s.quality && 'bg-muted-foreground/25',
                    )}
                  />
                ))}
              </div>
            )}
            {segments.map((s, i) => (
              <SegmentRow
                key={s.id}
                index={i}
                segment={s}
                project={project}
                selected={selected.has(s.id)}
                onSelect={(value) =>
                  setSelected((old) => {
                    const next = new Set(old);
                    if (value) next.add(s.id);
                    else next.delete(s.id);
                    return next;
                  })
                }
                onChange={(patch) =>
                  change(segments.map((x) => (x.id === s.id ? { ...x, ...patch } : x)))
                }
                onMove={(delta) => {
                  const j = i + delta;
                  if (j < 0 || j >= segments.length) return;
                  const next = [...segments];
                  [next[i], next[j]] = [next[j], next[i]];
                  change(next);
                }}
                onRemove={() => change(segments.filter((x) => x.id !== s.id))}
                onRender={() => render.mutate([s.id])}
                onRefresh={() => client.invalidateQueries({ queryKey: key })}
              />
            ))}
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                change([
                  ...segments,
                  {
                    id: crypto.randomUUID().replace(/-/g, ''),
                    text: '',
                    speaker: null,
                    profile_id: null,
                    engine: null,
                    model_size: null,
                    language: null,
                    emotion: null,
                    emotion_intensity: 0.6,
                    speed: null,
                    seed: null,
                    pause_after_ms: 400,
                    filename: '',
                    takes: [],
                    active: null,
                  },
                ])
              }
            >
              <Plus className="h-3.5 w-3.5 mr-1.5" />
              {t('studio.adv.addSegment')}
            </Button>
          </div>
          <div className="overflow-y-auto pb-44 space-y-4">
            <Panel title={t('studio.adv.project')}>
              <Field label={t('studio.adv.name')}>
                <Input
                  defaultValue={project.name}
                  key={project.id}
                  onBlur={(e) =>
                    e.target.value !== project.name &&
                    updateProject.mutate({ name: e.target.value })
                  }
                />
              </Field>
              <Field label={t('studio.common.language')}>
                <LanguagePicker
                  value={project.language}
                  onChange={(language) => updateProject.mutate({ language })}
                />
              </Field>
              <Field label={t('studio.adv.defaultVoice')}>
                <VoicePicker
                  value={project.defaults.profile_id ?? null}
                  onChange={(v) =>
                    updateProject.mutate({
                      defaults: { ...project.defaults, profile_id: v ?? undefined },
                    })
                  }
                />
              </Field>
              <Field label={t('studio.adv.defaultModel')}>
                <ModelPicker
                  value={
                    project.defaults.engine
                      ? project.defaults.model_size
                        ? `${project.defaults.engine}:${project.defaults.model_size}`
                        : project.defaults.engine
                      : null
                  }
                  language={project.language}
                  onChange={(v) => {
                    const parsed = v ? parseEngine(v) : null;
                    updateProject.mutate({
                      defaults: {
                        ...project.defaults,
                        engine: parsed?.engine,
                        model_size: parsed?.modelSize ?? undefined,
                      },
                    });
                  }}
                />
              </Field>
            </Panel>
            <VoiceTuning
              engine={project.defaults.engine ?? 'omnivoice'}
              denoise={denoise}
              onDenoise={setDenoise}
              effects={project.defaults.effects ?? 'voice'}
              onEffects={(effects) =>
                updateProject.mutate({
                  defaults: {
                    ...project.defaults,
                    effects,
                    effects_chain: effectsChainFor(effects, presets) ?? null,
                  },
                })
              }
            />
            <Panel title={t('studio.adv.import')}>
              <Textarea
                value={script}
                onChange={(e) => setScript(e.target.value)}
                rows={4}
                placeholder={t('studio.adv.scriptPlaceholder')}
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!script.trim()}
                  onClick={() => importScript.mutate(undefined)}
                >
                  {t('studio.adv.addScript')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
                  <FileUp className="h-3.5 w-3.5 mr-1.5" />
                  .txt / .csv
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".txt,.csv,.tsv"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) importScript.mutate(f);
                    e.target.value = '';
                  }}
                />
              </div>
              <p className="text-xs text-muted-foreground">{t('studio.adv.pauseHint')}</p>
              <p className="text-xs text-muted-foreground">{t('studio.adv.csvHint')}</p>
            </Panel>
            <Panel
              title={
                selected.size
                  ? t('studio.adv.bulkSelected', { count: selected.size })
                  : t('studio.adv.bulkAll')
              }
            >
              <Field label={t('studio.common.voice')}>
                <VoicePicker
                  className="h-8 text-xs"
                  value={bulk.voice}
                  onChange={(voice) => setBulk({ ...bulk, voice })}
                  emptyLabel={t('studio.adv.keep')}
                />
              </Field>
              <Field label={t('emotion.label')}>
                <Select
                  value={bulk.emotion}
                  onValueChange={(emotion) => setBulk({ ...bulk, emotion })}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="keep">{t('studio.adv.keep')}</SelectItem>
                    {EMOTIONS.map((e) => (
                      <SelectItem key={e.id} value={e.id}>
                        {t(`emotion.names.${e.id}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label={t('studio.common.speed')}>
                  <Input
                    className="h-8 text-xs"
                    type="number"
                    min={0.5}
                    max={2}
                    step={0.05}
                    value={bulk.speed}
                    placeholder={t('studio.adv.keep')}
                    onChange={(e) => setBulk({ ...bulk, speed: e.target.value })}
                  />
                </Field>
                <Field label={t('studio.adv.pause')}>
                  <Input
                    className="h-8 text-xs"
                    type="number"
                    min={0}
                    max={60}
                    step={0.1}
                    value={bulk.pause}
                    placeholder={t('studio.adv.keep')}
                    onChange={(e) => setBulk({ ...bulk, pause: e.target.value })}
                  />
                </Field>
              </div>
              <Button
                size="sm"
                variant="outline"
                className="w-full"
                disabled={!segments.length}
                onClick={applyBulk}
              >
                {t('studio.adv.applyBulk')}
              </Button>
            </Panel>
            <Panel title={t('studio.adv.output')}>
              <p className="text-xs text-muted-foreground flex items-center justify-between">
                <span>
                  {t('studio.adv.total', { time: formatSeconds(total), count: segments.length })}
                </span>
                <QualityBadge level={project.quality} />
              </p>
              <div className="flex gap-2">
                {project.pending ? (
                  <Button
                    size="sm"
                    variant="destructive"
                    className="flex-1"
                    onClick={() =>
                      studioApi
                        .stopStudio(project.id)
                        .then(() => client.invalidateQueries({ queryKey: key }))
                        .catch(handleError)
                    }
                  >
                    <Square className="h-3.5 w-3.5 mr-1.5" />
                    {t('studio.adv.stop')}
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    className="flex-1"
                    disabled={render.isPending || !segments.length}
                    onClick={() => render.mutate(undefined)}
                  >
                    <Play className="h-3.5 w-3.5 mr-1.5" />
                    {t('studio.adv.makeAll')}
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!missing.length || project.pending}
                  onClick={() => render.mutate(missing)}
                >
                  {t('studio.adv.makeMissing', { count: missing.length })}
                </Button>
              </div>
              {(chosen.length > 0 || failedIds.length > 0) && (
                <div className="flex gap-2">
                  {chosen.length > 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="flex-1"
                      disabled={render.isPending || project.pending}
                      onClick={() => render.mutate(chosen)}
                    >
                      <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                      {t('studio.check.regenSelected', { count: chosen.length })}
                    </Button>
                  )}
                  {failedIds.length > 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="flex-1 border-red-500/50 text-red-500"
                      disabled={render.isPending || project.pending}
                      onClick={() => render.mutate(failedIds)}
                    >
                      <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                      {t('studio.check.regenFailed', { count: failedIds.length })}
                    </Button>
                  )}
                </div>
              )}
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate text-muted-foreground">
                  {project.music ? project.music.name : t('studio.adv.noMusic')}
                </span>
                <span className="flex gap-1 shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7"
                    onClick={() => musicRef.current?.click()}
                  >
                    <Music className="h-3 w-3 mr-1" />
                    {t('studio.adv.music')}
                  </Button>
                  {project.music && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7"
                      onClick={() =>
                        studioApi.removeMusic(project.id).then(apply).catch(handleError)
                      }
                    >
                      {t('studio.adv.remove')}
                    </Button>
                  )}
                </span>
                <input
                  ref={musicRef}
                  type="file"
                  accept="audio/*"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) music.mutate(f);
                    e.target.value = '';
                  }}
                />
              </div>
              {project.music && (
                <div className="grid grid-cols-2 gap-2">
                  <Field label={t('studio.adv.musicSpeech')}>
                    <Input
                      className="h-8 text-xs"
                      type="number"
                      min={0}
                      max={100}
                      defaultValue={project.music.speech_pct ?? 25}
                      key={`speech-${project.id}-${project.music.name}`}
                      onBlur={(e) =>
                        updateProject.mutate({ music_speech_pct: Number(e.target.value) })
                      }
                    />
                  </Field>
                  <Field label={t('studio.adv.musicPause')}>
                    <Input
                      className="h-8 text-xs"
                      type="number"
                      min={0}
                      max={100}
                      defaultValue={project.music.pause_pct ?? 100}
                      key={`pause-${project.id}-${project.music.name}`}
                      onBlur={(e) =>
                        updateProject.mutate({ music_pause_pct: Number(e.target.value) })
                      }
                    />
                  </Field>
                </div>
              )}
              <div className="flex items-center gap-3">
                <Select value={exportFormat} onValueChange={setExportFormat}>
                  <SelectTrigger className="w-[90px] h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="mp3">MP3</SelectItem>
                    <SelectItem value="wav">WAV</SelectItem>
                    <SelectItem value="flac">FLAC</SelectItem>
                  </SelectContent>
                </Select>
                <label className="flex items-center gap-2 text-xs cursor-pointer">
                  <Checkbox checked={withSrt} onCheckedChange={(v) => setWithSrt(!!v)} />
                  SRT
                </label>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  className="flex-1"
                  disabled={exporting}
                  onClick={() => exportAll('merged')}
                >
                  <Download className="h-3.5 w-3.5 mr-1.5" />
                  {t('studio.adv.exportMerged')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={exporting}
                  onClick={() => exportAll('files')}
                >
                  {t('studio.adv.exportFiles')}
                </Button>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="w-full text-muted-foreground"
                onClick={async () => {
                  if (!window.confirm(t('studio.adv.confirmDelete', { name: project.name })))
                    return;
                  await studioApi.deleteStudio(project.id);
                  setActiveId(null);
                  client.invalidateQueries({ queryKey: ['studioList'] });
                }}
              >
                <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                {t('studio.adv.deleteProject')}
              </Button>
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
