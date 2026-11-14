import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  Download,
  FileText,
  FileVideo,
  Languages,
  Loader2,
  Play,
  Square,
  Trash2,
  Upload,
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
import { type DubOutput, type DubProject, type DubSegment, studioApi } from '@/lib/api/studio';
import { cn } from '@/lib/utils/cn';
import {
  ErrorNote,
  engineSupports,
  Field,
  formatSeconds,
  LanguagePicker,
  ModelPicker,
  PageHeader,
  Panel,
  parseEngine,
  QualityBadge,
  useSaveBlob,
  VoicePicker,
} from './common';
import { DUB_OUTPUT_DEFAULTS, DubOutputSettings, dubOutputOf } from './DubOutputSettings';

const ACTIVE = new Set([
  'queued',
  'extracting',
  'separating',
  'transcribing',
  'speakers',
  'translating',
  'synthesizing',
  'mixing',
]);

function NewDub({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [source, setSource] = useState('auto');
  const [target, setTarget] = useState('vi');
  const [model, setModel] = useState<string | null>('omnivoice');
  const [mode, setMode] = useState<'dub' | 'transcribe'>('dub');
  const [auto, setAuto] = useState<boolean | null>(null);
  const autoRun = auto ?? files.length > 1;
  const [speakers, setSpeakers] = useState('0');
  const [translator, setTranslator] = useState('4B');
  const [glossary, setGlossary] = useState('');
  const [output, setOutput] = useState<DubOutput>(DUB_OUTPUT_DEFAULTS);
  const srtInput = useRef<HTMLInputElement>(null);
  const [srt, setSrt] = useState<File | null>(null);
  const [srtRole, setSrtRole] = useState<'source' | 'target'>('source');
  const parsed = parseEngine(model ?? '');
  const canRun =
    files.length > 0 &&
    !(srt && files.length > 1) &&
    (mode === 'transcribe' || (!!model && engineSupports(parsed.engine, target)));

  const create = useMutation({
    mutationFn: () => {
      const form = new FormData();
      for (const f of files) form.append('files', f);
      form.append('target_language', target);
      if (source !== 'auto') form.append('source_language', source);
      form.append('engine', parsed.engine);
      form.append('model_size', parsed.modelSize ?? '1.7B');
      form.append('mode', mode);
      form.append('auto_continue', String(autoRun));
      form.append('speaker_count', speakers);
      form.append('glossary', glossary);
      form.append('original_volume', String(output.original_volume));
      form.append('music_volume', String(output.music_volume));
      form.append('voice_match', output.voice_match);
      form.append('subtitle', JSON.stringify(output.subtitle));
      if (srt) {
        form.append('srt', srt);
        form.append('srt_role', srtRole);
      }
      form.append('llm_size', translator);
      return studioApi.createDubs(form);
    },
    onSuccess: (created) => {
      setFiles([]);
      if (created[0]) onCreated(created[0].id);
    },
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });

  return (
    <Panel title={t('studio.dub.new')}>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          setFiles(Array.from(e.dataTransfer.files));
        }}
        className="w-full rounded-lg border border-dashed p-6 text-center hover:bg-muted/40 transition-colors"
      >
        {files.length ? (
          <span className="flex items-center justify-center gap-2 text-sm">
            <FileVideo className="h-4 w-4" />
            {files.length === 1
              ? files[0].name
              : t('studio.dub.filesChosen', { count: files.length })}
          </span>
        ) : (
          <span className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
            <Upload className="h-5 w-5" />
            {t('studio.dub.drop')}
          </span>
        )}
      </button>
      <input
        ref={input}
        type="file"
        multiple
        accept="audio/*,video/*"
        className="hidden"
        onChange={(e) => {
          setFiles(Array.from(e.target.files ?? []));
          e.target.value = '';
        }}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t('studio.dub.mode')}>
          <Select value={mode} onValueChange={(v) => setMode(v as 'dub' | 'transcribe')}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="dub">{t('studio.dub.modeDub')}</SelectItem>
              <SelectItem value="transcribe">{t('studio.dub.modeTranscribe')}</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        <Field label={t('studio.dub.source')}>
          <LanguagePicker
            value={source}
            onChange={setSource}
            auto={t('studio.common.autoDetect')}
          />
        </Field>
        {mode === 'dub' && (
          <>
            <Field label={t('studio.dub.target')}>
              <LanguagePicker value={target} onChange={setTarget} />
            </Field>
            <Field label={t('studio.common.model')}>
              <ModelPicker value={model} onChange={setModel} language={target} />
            </Field>
            <Field
              label={t('studio.dub.translator')}
              hint={t(`studio.dub.translatorHint.${translator}`)}
            >
              <Select value={translator} onValueChange={setTranslator}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="4B">{t('studio.dub.translatorName.4B')}</SelectItem>
                  <SelectItem value="custom">{t('studio.dub.translatorName.custom')}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </>
        )}
        <Field label={t('studio.dub.speakers')}>
          <Input
            type="number"
            min={0}
            max={12}
            value={speakers}
            onChange={(e) => setSpeakers(e.target.value)}
          />
        </Field>
      </div>
      <Field label={t('studio.dub.out.srt')} hint={t('studio.dub.out.srtHint')}>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={files.length > 1}
            onClick={() => srtInput.current?.click()}
          >
            <FileText className="h-3.5 w-3.5 mr-1.5" />
            {srt ? srt.name : t('studio.dub.out.srtPick')}
          </Button>
          {srt && (
            <Button size="sm" variant="ghost" onClick={() => setSrt(null)}>
              {t('studio.dub.out.srtClear')}
            </Button>
          )}
          {srt && mode === 'dub' && (
            <Select value={srtRole} onValueChange={(v) => setSrtRole(v as 'source' | 'target')}>
              <SelectTrigger className="h-8 w-auto min-w-[220px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="source">{t('studio.dub.out.srtSource')}</SelectItem>
                <SelectItem value="target">{t('studio.dub.out.srtTarget')}</SelectItem>
              </SelectContent>
            </Select>
          )}
        </div>
        <input
          ref={srtInput}
          type="file"
          accept=".srt"
          className="hidden"
          onChange={(e) => {
            setSrt(e.target.files?.[0] ?? null);
            e.target.value = '';
          }}
        />
      </Field>
      {mode === 'dub' && (
        <>
          <DubOutputSettings value={output} onChange={setOutput} />
          <Field label={t('studio.dub.glossary')} hint={t('studio.dub.glossaryHint')}>
            <Textarea value={glossary} onChange={(e) => setGlossary(e.target.value)} rows={2} />
          </Field>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <Checkbox checked={autoRun} onCheckedChange={(v) => setAuto(!!v)} />
            {t('studio.dub.autoContinue')}
          </label>
        </>
      )}
      <Button
        className="w-full"
        disabled={!canRun || create.isPending}
        onClick={() => create.mutate()}
      >
        {create.isPending ? (
          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
        ) : (
          <Languages className="h-4 w-4 mr-2" />
        )}
        {t('studio.dub.start', { count: files.length || 1 })}
      </Button>
    </Panel>
  );
}

function ReviewRow({
  segment,
  speakers,
  onChange,
}: {
  segment: DubSegment;
  speakers: string[];
  onChange: (patch: Partial<DubSegment>) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-2 border-b px-3 py-2 md:grid-cols-[80px_80px_minmax(0,1fr)_minmax(0,1fr)]">
      <span className="text-xs tabular-nums text-muted-foreground pt-2">
        {formatSeconds(segment.start)}–{formatSeconds(segment.end)}
      </span>
      <Select value={segment.speaker} onValueChange={(v) => onChange({ speaker: v })}>
        <SelectTrigger className="h-8 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {speakers.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground pt-1.5">
        {segment.text}
        {segment.reference && (
          <span className="ml-1.5 rounded bg-muted px-1 py-0.5 text-[10px]">
            {t(segment.reference === 'line' ? 'studio.dub.out.refLine' : 'studio.dub.out.refSpeaker')}
          </span>
        )}
      </p>
      <Textarea
        rows={2}
        value={segment.translation}
        onChange={(e) => onChange({ translation: e.target.value })}
        className={cn(
          'min-h-0 text-sm',
          (segment.overflow || segment.warning) && 'border-amber-500',
        )}
        title={segment.warning ? t(`studio.dub.lineWarning.${segment.warning}`) : undefined}
      />
    </div>
  );
}

function DubDetail({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const saveBlob = useSaveBlob();
  const key = ['dub', id];
  const { data: project } = useQuery({
    queryKey: key,
    queryFn: () => studioApi.getDub(id),
    refetchInterval: (q) => (ACTIVE.has(q.state.data?.stage ?? '') ? 1500 : false),
  });
  const [edits, setEdits] = useState<Record<string, Partial<DubSegment>>>({});
  useEffect(() => setEdits({}), [id]);
  // Output settings are edited here and sent with Approve / Apply.
  const [output, setOutput] = useState<DubOutput | null>(null);
  const loaded = !!project;
  // biome-ignore lint/correctness/useExhaustiveDependencies: reseed only when another dub opens
  useEffect(() => setOutput(project ? dubOutputOf(project) : null), [id, loaded]);
  const handleError = (e: Error) => toast({ title: e.message, variant: 'destructive' });
  const refresh = (p?: DubProject) => {
    if (p) client.setQueryData(key, p);
    else client.invalidateQueries({ queryKey: key });
    client.invalidateQueries({ queryKey: ['dubs'] });
  };

  const continueDub = useMutation({
    mutationFn: async () => {
      const segments = Object.entries(edits).map(([segmentId, patch]) => ({
        id: segmentId,
        ...patch,
      }));
      await studioApi.updateDub(id, { segments, approved: true, ...output });
      setEdits({});
      return studioApi.runDub(id);
    },
    onSuccess: () => refresh(),
    onError: handleError,
  });

  if (!project) return null;
  const running = ACTIVE.has(project.stage);
  const speakerNames = Object.keys(project.speakers);
  const segments = project.segments.map((s) => ({ ...s, ...edits[s.id] }));
  const canReview =
    ['review', 'done', 'failed', 'stopped'].includes(project.stage) && project.mode === 'dub';
  const unassigned = speakerNames.some(
    (s) => project.speakers[s].mode === 'profile' && !project.speakers[s].profile_id,
  );

  const download = async (name: string, blob: Blob, ext: string) =>
    saveBlob(`${project.name}-${name}.${ext}`, blob, ext);
  const file = (which: string, ext: string) =>
    studioApi
      .dubFile(id, which)
      .then((b) => download(which, b, ext))
      .catch(handleError);
  const subs = (which: string, fmt: string) =>
    studioApi
      .dubSubtitles(id, which, fmt)
      .then((b) => download(which, b, fmt))
      .catch(handleError);

  return (
    <div className="space-y-4">
      <Panel>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <h2 className="font-semibold truncate">{project.name}</h2>
            <p className="text-xs text-muted-foreground">
              {t(`studio.dub.stage.${project.stage}`)}
              {project.source_language
                ? ` · ${project.source_language} → ${project.target_language}`
                : ''}
            </p>
          </div>
          <div className="flex gap-2">
            {running ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  studioApi
                    .stopDub(id)
                    .then(() => refresh())
                    .catch(handleError)
                }
              >
                <Square className="h-3.5 w-3.5 mr-1.5" />
                {t('studio.dub.stop')}
              </Button>
            ) : (
              ['stopped', 'failed'].includes(project.stage) && (
                <Button
                  size="sm"
                  onClick={() =>
                    studioApi
                      .runDub(id)
                      .then(() => refresh())
                      .catch(handleError)
                  }
                >
                  <Play className="h-3.5 w-3.5 mr-1.5" />
                  {t('studio.dub.resume')}
                </Button>
              )
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={running}
              aria-label={t('studio.dub.delete')}
              onClick={async () => {
                if (!window.confirm(t('studio.dub.confirmDelete', { name: project.name }))) return;
                await studioApi.deleteDub(id).catch(handleError);
                onDeleted();
              }}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        {running && (
          <div className="h-2 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${project.progress * 100}%` }}
            />
          </div>
        )}
        {project.error && <ErrorNote>{project.error}</ErrorNote>}
      </Panel>

      {canReview && speakerNames.length > 0 && (
        <Panel title={t('studio.dub.voices')}>
          {speakerNames.map((name) => {
            const voice = project.speakers[name];
            return (
              <div
                key={name}
                className="grid gap-2 sm:grid-cols-[100px_1fr_1fr] items-center text-sm"
              >
                <span>{voice.label}</span>
                <Select
                  value={voice.mode}
                  onValueChange={(mode) =>
                    studioApi
                      .updateDub(id, {
                        speakers: {
                          [name]: { mode, profile_id: mode === 'clone' ? null : voice.profile_id },
                        },
                      })
                      .then(refresh)
                      .catch(handleError)
                  }
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="clone">{t('studio.dub.keepVoice')}</SelectItem>
                    <SelectItem value="profile">{t('studio.dub.useVoice')}</SelectItem>
                  </SelectContent>
                </Select>
                {voice.mode === 'profile' && (
                  <VoicePicker
                    className="h-8 text-xs"
                    value={voice.profile_id}
                    onChange={(profile_id) =>
                      studioApi
                        .updateDub(id, { speakers: { [name]: { profile_id } } })
                        .then(refresh)
                        .catch(handleError)
                    }
                  />
                )}
              </div>
            );
          })}
        </Panel>
      )}

      {canReview && output && (
        <Panel title={t('studio.dub.out.title')}>
          <DubOutputSettings value={output} onChange={setOutput} />
          <p className="text-xs text-muted-foreground">{t('studio.dub.out.applyHint')}</p>
        </Panel>
      )}

      {segments.length > 0 && (
        <Panel title={project.mode === 'dub' ? t('studio.dub.review') : t('studio.dub.transcript')}>
          <div className="rounded-md border max-h-[420px] overflow-y-auto">
            {project.mode === 'dub'
              ? segments.map((s) => (
                  <ReviewRow
                    key={s.id}
                    segment={s}
                    speakers={speakerNames}
                    onChange={(patch) =>
                      setEdits((e) => ({ ...e, [s.id]: { ...e[s.id], ...patch } }))
                    }
                  />
                ))
              : segments.map((s) => (
                  <p key={s.id} className="border-b px-3 py-2 text-sm">
                    <span className="text-xs tabular-nums text-muted-foreground mr-2">
                      {formatSeconds(s.start)} · {s.speaker}
                    </span>
                    {s.text}
                  </p>
                ))}
          </div>
          {canReview && (
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={continueDub.isPending || running || unassigned}
                onClick={() => continueDub.mutate()}
              >
                <CheckCircle2 className="h-4 w-4 mr-2" />
                {project.stage === 'done' ? t('studio.dub.apply') : t('studio.dub.approve')}
              </Button>
              <Button
                variant="outline"
                disabled={running}
                onClick={() =>
                  studioApi
                    .retranslateDub(id)
                    .then(() => refresh())
                    .catch(handleError)
                }
              >
                {t('studio.dub.retranslate')}
              </Button>
            </div>
          )}
        </Panel>
      )}

      {project.stage === 'done' && (
        <Panel title={t('studio.dub.output')}>
          {project.qc && (
            <div className="space-y-1">
              <QualityBadge level={project.qc.level} />
              {project.qc.problems.length > 0 && (
                <ul className="text-xs text-muted-foreground space-y-0.5">
                  {project.qc.problems.map((p, i) => {
                    const row = project.segments.find((s) => s.id === p.id);
                    return (
                      // biome-ignore lint/suspicious/noArrayIndexKey: problems have no id
                      <li key={i}>
                        {row ? `${formatSeconds(row.start)} · ` : ''}
                        {t(`studio.dub.problem.${p.kind}`, { value: p.value })}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
          {project.mode === 'dub' && project.has_video && (
            // biome-ignore lint/a11y/useMediaCaption: the dubbed subtitles are offered as files
            <video
              src={studioApi.dubFileUrl(id, 'video')}
              controls
              className="w-full max-h-[360px] rounded-md bg-black"
            />
          )}
          <div className="flex flex-wrap gap-2">
            {project.mode === 'dub' && (
              <>
                {project.has_video && (
                  <Button size="sm" onClick={() => file('video', 'mp4')}>
                    <Download className="h-3.5 w-3.5 mr-1.5" />
                    {t('studio.dub.file.video')}
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={() => file('audio', 'wav')}>
                  {t('studio.dub.file.audio')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => file('voices', 'wav')}>
                  {t('studio.dub.file.voices')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => file('background', 'wav')}>
                  {t('studio.dub.file.background')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => subs('target', 'srt')}>
                  {t('studio.dub.subs.targetSrt')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => subs('target', 'vtt')}>
                  {t('studio.dub.subs.targetVtt')}
                </Button>
              </>
            )}
            <Button size="sm" variant="outline" onClick={() => subs('source', 'srt')}>
              {t('studio.dub.subs.sourceSrt')}
            </Button>
            <Button size="sm" variant="outline" onClick={() => subs('source', 'txt')}>
              {t('studio.dub.subs.sourceTxt')}
            </Button>
          </div>
        </Panel>
      )}
    </div>
  );
}

export function DubTab() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(true);
  const { data: projects } = useQuery({
    queryKey: ['dubs'],
    queryFn: () => studioApi.listDubs(),
    refetchInterval: (q) => (q.state.data?.some((p) => ACTIVE.has(p.stage)) ? 2000 : false),
  });

  return (
    <div className="h-full flex flex-col min-h-0">
      <PageHeader
        title={t('studio.dub.title')}
        subtitle={t('studio.dub.subtitle')}
        actions={
          <>
            {projects?.some((p) => ACTIVE.has(p.stage)) && (
              <Button
                size="sm"
                variant="destructive"
                onClick={() =>
                  studioApi.stopJobs().then(() => client.invalidateQueries({ queryKey: ['dubs'] }))
                }
              >
                <Square className="h-3.5 w-3.5 mr-1.5" />
                {t('studio.dub.stopAll')}
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => {
                setShowNew(true);
                setActiveId(null);
              }}
            >
              {t('studio.dub.new')}
            </Button>
          </>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)] flex-1 min-h-0">
        <div className="overflow-y-auto divide-y rounded-lg border self-start max-h-full">
          {projects?.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => {
                setActiveId(p.id);
                setShowNew(false);
              }}
              className={cn(
                'w-full text-left px-3 py-2.5 hover:bg-muted/40',
                activeId === p.id && 'bg-accent/10',
              )}
            >
              <p className="text-sm font-medium truncate">{p.name}</p>
              <p className="text-xs text-muted-foreground">
                {t(`studio.dub.stage.${p.stage}`)}
                {ACTIVE.has(p.stage) ? ` · ${Math.round(p.progress * 100)}%` : ''}
              </p>
              {ACTIVE.has(p.stage) && (
                <div className="mt-1.5 h-1 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full bg-accent transition-all"
                    style={{ width: `${p.progress * 100}%` }}
                  />
                </div>
              )}
            </button>
          ))}
          {!projects?.length && (
            <p className="p-6 text-center text-sm text-muted-foreground">{t('studio.dub.none')}</p>
          )}
        </div>
        <div className="overflow-y-auto pb-44 min-w-0">
          {showNew || !activeId ? (
            <NewDub
              onCreated={(id) => {
                client.invalidateQueries({ queryKey: ['dubs'] });
                setActiveId(id);
                setShowNew(false);
              }}
            />
          ) : (
            <DubDetail
              id={activeId}
              onDeleted={() => {
                setActiveId(null);
                setShowNew(true);
                client.invalidateQueries({ queryKey: ['dubs'] });
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
