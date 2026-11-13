import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  ClipboardPaste,
  Download,
  FileUp,
  Loader2,
  Play,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ENGINE_OPTIONS,
  isProfileCompatibleWithEngine,
} from '@/components/Generation/EngineModelSelector';
import {
  heardOf,
  PlayButton,
  QualityBadge,
  WordDiffList,
  wordDiffOf,
} from '@/components/Studio/common';
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
import { Slider } from '@/components/ui/slider';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import {
  type BatchDetail,
  type BatchItemInput,
  type ExportFormat,
  extrasApi,
} from '@/lib/api/extras';
import { EMOTIONS, type EmotionId, emotionSupport } from '@/lib/constants/emotions';
import { getLanguageOptionsForEngine } from '@/lib/constants/languages';
import { useExtrasSettings } from '@/lib/hooks/useExtras';
import { useProfiles } from '@/lib/hooks/useProfiles';
import { cn } from '@/lib/utils/cn';
import { usePlatform } from '@/platform/PlatformContext';

type SplitMode = 'paragraph' | 'line';

function splitPasted(text: string, mode: SplitMode): BatchItemInput[] {
  const parts = mode === 'line' ? text.split(/\r?\n/) : text.split(/\r?\n\s*\r?\n/);
  return parts
    .map((p) => p.replace(/\s*\r?\n\s*/g, ' ').trim())
    .filter(Boolean)
    .map((t) => ({ text: t, filename: '' }));
}

function engineFromValue(value: string): { engine: string; modelSize: string | null } {
  const [engine, size] = value.split(':');
  return { engine, modelSize: size ?? null };
}

export function BatchTab() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const platform = usePlatform();
  const queryClient = useQueryClient();
  const { data: profiles } = useProfiles();
  const { settings: extras } = useExtrasSettings();

  // ── Input ────────────────────────────────────────────────────────────
  const [items, setItems] = useState<BatchItemInput[]>([]);
  const [split, setSplit] = useState<SplitMode>('paragraph');
  const [pasted, setPasted] = useState('');
  const [name, setName] = useState('');
  const [parsing, setParsing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const lastFile = useRef<File | null>(null);

  // ── Voice / engine ───────────────────────────────────────────────────
  const [profileId, setProfileId] = useState<string>('');
  const profile = profiles?.find((p) => p.id === profileId) ?? null;
  const engineOptions = useMemo(
    () =>
      profile
        ? ENGINE_OPTIONS.filter((o) => isProfileCompatibleWithEngine(profile, o.engine))
        : ENGINE_OPTIONS,
    [profile],
  );
  const [engineValue, setEngineValue] = useState<string>('qwen:1.7B');
  const { engine, modelSize } = engineFromValue(engineValue);
  const languageOptions = getLanguageOptionsForEngine(engine);
  const [language, setLanguage] = useState<string>('en');
  const [speed, setSpeed] = useState<number | null>(null);
  const effectiveSpeed = speed ?? extras?.speed ?? 1;
  const [emotion, setEmotion] = useState<EmotionId>('neutral');
  const [emotionIntensity, setEmotionIntensity] = useState(0.6);
  const [denoise, setDenoise] = useState(0);

  useEffect(() => {
    if (!profileId && profiles?.length) setProfileId(profiles[0].id);
  }, [profiles, profileId]);

  useEffect(() => {
    if (!engineOptions.some((o) => o.value === engineValue) && engineOptions.length) {
      const preferred = profile?.default_engine || profile?.preset_engine;
      const match = engineOptions.find((o) => o.engine === preferred) ?? engineOptions[0];
      setEngineValue(match.value);
    }
  }, [engineOptions, engineValue, profile]);

  useEffect(() => {
    if (!languageOptions.some((l) => l.value === language)) {
      const fromProfile = languageOptions.find((l) => l.value === profile?.language);
      setLanguage((fromProfile ?? languageOptions[0])?.value ?? 'en');
    }
  }, [languageOptions, language, profile]);

  // ── Batches ──────────────────────────────────────────────────────────
  const [activeId, setActiveId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { data: batches } = useQuery({
    queryKey: ['batches'],
    queryFn: () => extrasApi.listBatches(),
    refetchInterval: 5000,
  });
  const { data: active } = useQuery({
    queryKey: ['batch', activeId],
    queryFn: () => extrasApi.getBatch(activeId as string),
    enabled: !!activeId,
    refetchInterval: (q) => {
      const d = q.state.data as BatchDetail | undefined;
      const pending = d?.items.some(
        (i) => i.status === 'generating' || i.status === 'loading_model',
      );
      return pending ? 2000 : false;
    },
  });
  useEffect(() => {
    if (!activeId && batches?.length) setActiveId(batches[0].id);
  }, [batches, activeId]);

  // ── Regenerate ───────────────────────────────────────────────────────
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // biome-ignore lint/correctness/useExhaustiveDependencies: clear the ticks when another batch is shown
  useEffect(() => setSelected(new Set()), [activeId]);
  const [regenerating, setRegenerating] = useState(false);
  const regenerate = async (indices: number[]) => {
    if (!active || !indices.length) return;
    setRegenerating(true);
    try {
      queryClient.setQueryData(
        ['batch', active.id],
        await extrasApi.regenerateBatch(active.id, indices),
      );
      queryClient.invalidateQueries({ queryKey: ['batch', active.id] });
    } catch (e) {
      toast({
        title: t('batch.startFailed'),
        description: (e as Error).message,
        variant: 'destructive',
      });
    } finally {
      setRegenerating(false);
    }
  };

  // ── Download options ─────────────────────────────────────────────────
  const [fmt, setFmt] = useState<ExportFormat | null>(null);
  const format = fmt ?? extras?.export_format ?? 'wav';
  const [withSrt, setWithSrt] = useState(true);
  const [gapMs, setGapMs] = useState(500);
  const [downloading, setDownloading] = useState<'files' | 'merged' | null>(null);

  const loadFile = async (file: File, mode: SplitMode) => {
    setParsing(true);
    try {
      const r = await extrasApi.parseBatchFile(file, mode);
      setItems(r.items);
      if (!name) setName(file.name.replace(/\.[^.]+$/, ''));
      if (!r.items.length) toast({ title: t('batch.input.empty'), variant: 'destructive' });
    } catch (e) {
      toast({
        title: t('batch.input.parseFailed'),
        description: (e as Error).message,
        variant: 'destructive',
      });
    } finally {
      setParsing(false);
    }
  };

  const changeSplit = (mode: SplitMode) => {
    setSplit(mode);
    if (lastFile.current && !lastFile.current.name.toLowerCase().endsWith('.csv')) {
      loadFile(lastFile.current, mode);
    } else if (pasted.trim()) {
      setItems(splitPasted(pasted, mode));
    }
  };

  const totalChars = items.reduce((n, i) => n + i.text.length, 0);

  const start = async () => {
    if (!profileId || !items.length) return;
    setCreating(true);
    try {
      const b = await extrasApi.createBatch({
        name: name.trim(),
        profile_id: profileId,
        language,
        engine,
        model_size: modelSize,
        speed: speed ?? undefined,
        emotion: emotion === 'neutral' ? undefined : emotion,
        emotion_intensity: emotionIntensity,
        denoise: denoise > 0 ? denoise : undefined,
        sentence_pause_ms: 250,
        items,
      });
      setActiveId(b.id);
      queryClient.setQueryData(['batch', b.id], b);
      queryClient.invalidateQueries({ queryKey: ['batches'] });
      queryClient.invalidateQueries({ queryKey: ['history'] });
      toast({ title: t('batch.started', { count: b.total }) });
    } catch (e) {
      toast({
        title: t('batch.startFailed'),
        description: (e as Error).message,
        variant: 'destructive',
      });
    } finally {
      setCreating(false);
    }
  };

  const download = async (mode: 'files' | 'merged') => {
    if (!active) return;
    setDownloading(mode);
    try {
      const blob = await extrasApi.downloadBatch(active.id, {
        mode,
        format,
        bitrate: extras?.mp3_bitrate ?? 192,
        srt: withSrt,
        gapMs,
      });
      const base = active.name.replace(/[<>:"/\\|?*]+/g, '_') || 'batch';
      const isZip = mode === 'files' || withSrt;
      const filename = isZip
        ? `${base}${mode === 'merged' ? '-gop' : ''}.zip`
        : `${base}.${format}`;
      await platform.filesystem.saveFile(filename, blob, [
        isZip ? { name: 'ZIP', extensions: ['zip'] } : { name: 'Audio', extensions: [format] },
      ]);
    } catch (e) {
      toast({
        title: t('batch.downloadFailed'),
        description: (e as Error).message,
        variant: 'destructive',
      });
    } finally {
      setDownloading(null);
    }
  };

  const removeBatch = async (id: string) => {
    await extrasApi.deleteBatch(id);
    if (activeId === id) setActiveId(null);
    queryClient.invalidateQueries({ queryKey: ['batches'] });
  };

  const done = active?.counts.completed ?? 0;
  const failed = (active?.counts.failed ?? 0) + (active?.counts.missing ?? 0);
  const progress = active?.total ? ((done + failed) / active.total) * 100 : 0;
  const failedItems = (active?.items ?? [])
    .filter((it) => it.status === 'failed' || it.status === 'missing' || it.final?.level === 'red')
    .map((it) => it.index);

  return (
    <div className="h-full overflow-y-auto pb-24">
      <div className="py-3 space-y-1">
        <h1 className="text-2xl font-semibold">{t('batch.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('batch.description')}</p>
      </div>

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* ── Left: build a batch ───────────────────────────────────── */}
        <section className="space-y-5">
          <div className="rounded-xl border p-4 space-y-3">
            <h2 className="text-sm font-semibold">{t('batch.input.title')}</h2>
            <div className="flex flex-wrap gap-2 items-center">
              <Button
                size="sm"
                variant="outline"
                onClick={() => fileRef.current?.click()}
                disabled={parsing}
              >
                {parsing ? (
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                ) : (
                  <FileUp className="h-3.5 w-3.5 mr-1.5" />
                )}
                {t('batch.input.chooseFile')}
              </Button>
              <input
                ref={fileRef}
                type="file"
                accept=".txt,.csv,.tsv"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) {
                    lastFile.current = f;
                    loadFile(f, split);
                  }
                  e.target.value = '';
                }}
              />
              <Select value={split} onValueChange={(v) => changeSplit(v as SplitMode)}>
                <SelectTrigger className="w-[220px] h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="paragraph">{t('batch.input.splitParagraph')}</SelectItem>
                  <SelectItem value="line">{t('batch.input.splitLine')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <p className="text-xs text-muted-foreground">{t('batch.input.hint')}</p>
            <Textarea
              value={pasted}
              onChange={(e) => setPasted(e.target.value)}
              placeholder={t('batch.input.pastePlaceholder')}
              rows={5}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!pasted.trim()}
              onClick={() => {
                lastFile.current = null;
                setItems(splitPasted(pasted, split));
              }}
            >
              <ClipboardPaste className="h-3.5 w-3.5 mr-1.5" />
              {t('batch.input.usePasted')}
            </Button>
          </div>

          {items.length > 0 && (
            <div className="rounded-xl border overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2 bg-muted/40">
                <span className="text-sm font-medium">
                  {t('batch.preview.title', {
                    count: items.length,
                    chars: totalChars.toLocaleString(),
                  })}
                </span>
                <Button size="sm" variant="ghost" onClick={() => setItems([])}>
                  {t('batch.preview.clear')}
                </Button>
              </div>
              <div className="max-h-[360px] overflow-y-auto divide-y divide-border/60">
                {items.map((it, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: items have no id before submit
                  <div key={i} className="flex gap-2 px-3 py-2 items-start">
                    <span className="text-xs tabular-nums text-muted-foreground w-8 pt-2">
                      {i + 1}
                    </span>
                    <div className="flex-1 min-w-0 space-y-1">
                      <p className="text-sm line-clamp-2">{it.text}</p>
                      <Input
                        value={it.filename}
                        onChange={(e) =>
                          setItems((xs) =>
                            xs.map((x, j) => (j === i ? { ...x, filename: e.target.value } : x)),
                          )
                        }
                        placeholder={t('batch.preview.filenamePlaceholder')}
                        className="h-7 text-xs"
                      />
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7"
                      aria-label={t('batch.preview.remove')}
                      onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-xl border p-4 space-y-4">
            <h2 className="text-sm font-semibold">{t('batch.voice.title')}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <span className="text-xs text-muted-foreground">{t('batch.voice.profile')}</span>
                <Select value={profileId} onValueChange={setProfileId}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('batch.voice.profilePlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    {profiles
                      ?.filter((p) => (p.voice_type as string) !== 'import')
                      .map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <span className="text-xs text-muted-foreground">{t('batch.voice.engine')}</span>
                <Select value={engineValue} onValueChange={setEngineValue}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {engineOptions.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <span className="text-xs text-muted-foreground">{t('batch.voice.language')}</span>
                <Select value={language} onValueChange={setLanguage}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {languageOptions.map((l) => (
                      <SelectItem key={l.value} value={l.value}>
                        {l.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <span className="text-xs text-muted-foreground">{t('batch.voice.name')}</span>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t('batch.voice.namePlaceholder')}
                />
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>{t('extras.speed.title')}</span>
                <span className="tabular-nums">
                  {t('extras.speed.value', { speed: effectiveSpeed.toFixed(2) })}
                </span>
              </div>
              <Slider
                value={[effectiveSpeed]}
                onValueChange={([v]) => setSpeed(v)}
                min={0.5}
                max={2}
                step={0.05}
                aria-label={t('extras.speed.title')}
              />
            </div>
            <div className="space-y-2">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>{t('studio.common.denoise')}</span>
                <span className="tabular-nums">
                  {denoise ? `${Math.round(denoise * 100)}%` : t('studio.common.off')}
                </span>
              </div>
              <Slider
                value={[denoise]}
                onValueChange={([v]) => setDenoise(v)}
                min={0}
                max={1}
                step={0.1}
                aria-label={t('studio.common.denoise')}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <span className="text-xs text-muted-foreground">{t('emotion.label')}</span>
                <Select value={emotion} onValueChange={(v) => setEmotion(v as EmotionId)}>
                  <SelectTrigger>
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
              </div>
              <div className="space-y-3">
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>{t('emotion.intensity')}</span>
                  <span className="tabular-nums">{Math.round(emotionIntensity * 100)}%</span>
                </div>
                <Slider
                  value={[emotionIntensity]}
                  onValueChange={([v]) => setEmotionIntensity(v)}
                  min={0}
                  max={1}
                  step={0.05}
                  disabled={emotion === 'neutral'}
                  aria-label={t('emotion.intensity')}
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {t(`emotion.support.${emotionSupport(engine)}`)}
            </p>
            <Button
              className="w-full"
              onClick={start}
              disabled={creating || !items.length || !profileId}
            >
              {creating ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Play className="h-4 w-4 mr-2" />
              )}
              {t('batch.start', { count: items.length })}
            </Button>
          </div>
        </section>

        {/* ── Right: progress & downloads ───────────────────────────── */}
        <section className="space-y-5">
          {active ? (
            <div className="rounded-xl border p-4 space-y-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold truncate">{active.name}</h2>
                  <p className="text-xs text-muted-foreground">
                    {t('batch.progress.summary', { done, failed, total: active.total })}
                  </p>
                </div>
                <Select value={active.id} onValueChange={setActiveId}>
                  <SelectTrigger className="w-[200px] h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {batches?.map((b) => (
                      <SelectItem key={b.id} value={b.id} className="text-xs">
                        {b.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full bg-accent transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                  <Checkbox
                    checked={selected.size > 0 && selected.size === active.items.length}
                    onCheckedChange={(v) =>
                      setSelected(v ? new Set(active.items.map((it) => it.index)) : new Set())
                    }
                  />
                  {t('studio.check.selectAll')}
                </label>
                <span className="flex-1" />
                {selected.size > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7"
                    disabled={regenerating}
                    onClick={() => regenerate([...selected].sort((a, b) => a - b))}
                  >
                    <RefreshCw className="h-3 w-3 mr-1" />
                    {t('studio.check.regenSelected', { count: selected.size })}
                  </Button>
                )}
                {failedItems.length > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 border-red-500/50 text-red-500"
                    disabled={regenerating}
                    onClick={() => regenerate(failedItems)}
                  >
                    <RefreshCw className="h-3 w-3 mr-1" />
                    {t('studio.check.regenFailed', { count: failedItems.length })}
                  </Button>
                )}
              </div>
              <div className="max-h-[420px] overflow-y-auto divide-y divide-border/60 rounded-md border">
                {active.items.map((it) => (
                  <div
                    key={it.index}
                    className={cn(
                      'px-3 py-1.5 text-sm space-y-1',
                      it.final?.level === 'red' && 'bg-red-500/5',
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Checkbox
                        checked={selected.has(it.index)}
                        onCheckedChange={(v) =>
                          setSelected((prev) => {
                            const next = new Set(prev);
                            if (v) next.add(it.index);
                            else next.delete(it.index);
                            return next;
                          })
                        }
                      />
                      <span className="text-xs tabular-nums text-muted-foreground w-8">
                        {it.index + 1}
                      </span>
                      {it.status === 'completed' ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />
                      ) : it.status === 'failed' || it.status === 'missing' ? (
                        <XCircle className="h-4 w-4 text-red-500 shrink-0" />
                      ) : (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />
                      )}
                      <span className="flex-1 truncate" title={it.error ?? it.text}>
                        {it.filename ? (
                          <span className="text-muted-foreground mr-1">[{it.filename}]</span>
                        ) : null}
                        {it.text}
                      </span>
                      {it.duration ? (
                        <span className="text-xs tabular-nums text-muted-foreground">
                          {it.duration.toFixed(1)}s
                        </span>
                      ) : null}
                      {it.final && <QualityBadge level={it.final.level} />}
                      {it.status === 'completed' && it.generation_id && (
                        <PlayButton
                          className="h-7 w-7"
                          url={apiClient.getAudioUrl(it.generation_id)}
                        />
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 px-2"
                        disabled={
                          regenerating ||
                          it.status === 'generating' ||
                          it.status === 'loading_model'
                        }
                        onClick={() => regenerate([it.index])}
                      >
                        <RefreshCw className="h-3 w-3 mr-1" />
                        {t('studio.check.regen')}
                      </Button>
                    </div>
                    {it.status === 'completed' && wordDiffOf(it.final).length > 0 && (
                      <div className="pl-16">
                        <WordDiffList ops={wordDiffOf(it.final)} heard={heardOf(it.final)} />
                      </div>
                    )}
                    {it.error && it.status !== 'completed' && (
                      <p className="pl-16 text-xs text-red-500">{it.error}</p>
                    )}
                  </div>
                ))}
              </div>

              <div className="space-y-3 pt-1">
                <h3 className="text-sm font-semibold">{t('batch.download.title')}</h3>
                <div className="flex flex-wrap items-center gap-4">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-muted-foreground">{t('batch.download.format')}</span>
                    <Select value={format} onValueChange={(v) => setFmt(v as ExportFormat)}>
                      <SelectTrigger className="w-[100px] h-8">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="wav">WAV</SelectItem>
                        <SelectItem value="mp3">MP3</SelectItem>
                        <SelectItem value="flac">FLAC</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <Checkbox
                      id="batchSrt"
                      checked={withSrt}
                      onCheckedChange={(v) => setWithSrt(!!v)}
                    />
                    <label htmlFor="batchSrt" className="cursor-pointer">
                      {t('batch.download.includeSrt')}
                    </label>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-muted-foreground">{t('batch.download.gap')}</span>
                    <Input
                      type="number"
                      min={0}
                      max={10000}
                      step={100}
                      value={gapMs}
                      onChange={(e) =>
                        setGapMs(Math.max(0, Math.min(10000, Number(e.target.value) || 0)))
                      }
                      className="w-[90px] h-8"
                    />
                    <span className="text-muted-foreground">ms</span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => download('files')} disabled={!done || !!downloading}>
                    {downloading === 'files' ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <Download className="h-4 w-4 mr-2" />
                    )}
                    {t('batch.download.files')}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => download('merged')}
                    disabled={!done || !!downloading}
                  >
                    {downloading === 'merged' ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <Download className="h-4 w-4 mr-2" />
                    )}
                    {t('batch.download.merged')}
                  </Button>
                  <Button
                    variant="ghost"
                    className={cn('ml-auto text-muted-foreground')}
                    onClick={() => removeBatch(active.id)}
                  >
                    <Trash2 className="h-4 w-4 mr-2" />
                    {t('batch.download.removeBatch')}
                  </Button>
                </div>
                {failed > 0 && (
                  <p className="text-xs text-amber-500">{t('batch.download.failedNote')}</p>
                )}
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
              {t('batch.progress.none')}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
