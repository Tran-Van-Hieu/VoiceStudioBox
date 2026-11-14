import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { CheckCircle2, FileAudio, Loader2, Scissors, Sparkles, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
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
import { type ClipAnalysis, type PrepareJob, studioApi } from '@/lib/api/studio';
import { cn } from '@/lib/utils/cn';
import { AuditionPanel } from './AuditionPanel';
import {
  ErrorNote,
  Field,
  LanguagePicker,
  ModelPicker,
  PageHeader,
  Panel,
  PlayButton,
  parseEngine,
  VoicePicker,
} from './common';
import { WavePicker } from './WavePicker';

type Choice = 'auto' | 'on' | 'off';

const OMNIVOICE_MAX_SECONDS = 20;
const MAX_CLIP_SECONDS = 30;
const POLLED_UNTIL = ['ready', 'done', 'failed'];

function Option({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Choice;
  onChange: (v: Choice) => void;
}) {
  const { t } = useTranslation();
  return (
    <Field label={label}>
      <Select value={value} onValueChange={(v) => onChange(v as Choice)}>
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="auto">{t('studio.clone.auto')}</SelectItem>
          <SelectItem value="on">{t('studio.clone.on')}</SelectItem>
          <SelectItem value="off">{t('studio.common.off')}</SelectItem>
        </SelectContent>
      </Select>
    </Field>
  );
}

function ClipCard(props: {
  title: string;
  url: string;
  analysis: ClipAnalysis;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const { analysis: a } = props;
  return (
    <button
      type="button"
      onClick={props.onSelect}
      className={cn(
        'rounded-lg border p-3 text-left space-y-2 transition-colors',
        props.selected ? 'border-accent bg-accent/10' : 'hover:bg-muted/40',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{props.title}</span>
        <span className="text-lg font-semibold tabular-nums">{a.score}</span>
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span onClickCapture={(e) => e.stopPropagation()}>
          <PlayButton url={props.url} />
        </span>
        <span>{a.duration.toFixed(1)} s</span>
        <span>SNR {a.snr_db.toFixed(0)} dB</span>
      </div>
      {a.issues.length > 0 ? (
        <ul className="text-xs text-amber-500 space-y-0.5">
          {a.issues.map((i) => (
            <li key={i}>{t(`studio.clone.issue.${i}`)}</li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-emerald-500">{t('studio.clone.clipGood')}</p>
      )}
    </button>
  );
}

export function CloneTab() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState('');
  const [language, setLanguage] = useState('auto');
  const [isolate, setIsolate] = useState<Choice>('auto');
  const [denoise, setDenoise] = useState<Choice>('auto');
  const [jobId, setJobId] = useState<string | null>(null);
  const [range, setRange] = useState<[number, number] | null>(null);
  const [name, setName] = useState('');
  const [which, setWhich] = useState<'raw' | 'clean'>('clean');
  const [transcript, setTranscript] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>('omnivoice');
  const [voiceLanguage, setVoiceLanguage] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append('file', file as File);
      form.append('isolate', isolate);
      form.append('denoise', denoise);
      return studioApi.prepare(form);
    },
    onSuccess: (job) => {
      setCreatedId(null);
      setTranscript(null);
      setVoiceLanguage(null);
      setRange(null);
      setJobId(job.id);
    },
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });

  const jobKey = ['labJob', jobId];
  const { data: job } = useQuery({
    queryKey: jobKey,
    queryFn: () => studioApi.labJob<PrepareJob>(jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => (POLLED_UNTIL.includes(q.state.data?.stage ?? '') ? false : 1000),
  });
  const source = job?.source ?? null;
  const result = job?.stage === 'done' ? job.result : null;
  const currentText = transcript ?? result?.transcript ?? '';
  const currentLanguage = voiceLanguage ?? result?.language ?? 'vi';
  const preparing =
    start.isPending || (!!jobId && !job) || job?.stage === 'queued' || job?.stage === 'analyzing';

  useEffect(() => {
    if (source && !range) setRange([0, Math.min(source.source_seconds, MAX_CLIP_SECONDS)]);
  }, [source, range]);

  const select = useMutation({
    mutationFn: () =>
      studioApi.selectClip(jobId as string, {
        start: (range as [number, number])[0],
        end: (range as [number, number])[1],
        language: language === 'auto' ? undefined : language,
        reference_text: text,
      }),
    onSuccess: (j) => {
      setTranscript(null);
      setVoiceLanguage(null);
      client.setQueryData(jobKey, j);
    },
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });
  const selecting = select.isPending || job?.stage === 'transcribing';

  const save = useMutation({
    mutationFn: () =>
      studioApi.createVoice(jobId as string, {
        which,
        name,
        language: currentLanguage,
        reference_text: currentText,
        default_engine: model ? parseEngine(model).engine : undefined,
        profile_id: target ?? undefined,
      }),
    onSuccess: (r) => {
      setCreatedId(r.profile_id);
      client.invalidateQueries({ queryKey: ['profiles'] });
      toast({ title: t('studio.clone.saved') });
    },
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });

  const length = range ? range[1] - range[0] : 0;

  return (
    <div className="h-full overflow-y-auto pb-44">
      <PageHeader title={t('studio.clone.title')} subtitle={t('studio.clone.subtitle')} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title={t('studio.clone.step1')}>
          <button
            type="button"
            onClick={() => input.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              setFile(e.dataTransfer.files[0] ?? null);
            }}
            className="w-full rounded-lg border border-dashed p-8 text-center hover:bg-muted/40 transition-colors"
          >
            {file ? (
              <span className="flex items-center justify-center gap-2 text-sm">
                <FileAudio className="h-4 w-4" />
                {file.name}
              </span>
            ) : (
              <span className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
                <Upload className="h-5 w-5" />
                {t('studio.clone.drop')}
              </span>
            )}
          </button>
          <input
            ref={input}
            type="file"
            accept="audio/*,video/*"
            className="hidden"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              e.target.value = '';
            }}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <Option label={t('studio.clone.isolate')} value={isolate} onChange={setIsolate} />
            <Option label={t('studio.common.denoise')} value={denoise} onChange={setDenoise} />
          </div>
          <Button className="w-full" disabled={!file || preparing} onClick={() => start.mutate()}>
            {preparing ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4 mr-2" />
            )}
            {preparing && job ? t(`studio.clone.stage.${job.stage}`) : t('studio.clone.analyze')}
          </Button>
          {job?.stage === 'failed' && <ErrorNote>{job.error}</ErrorNote>}
        </Panel>

        {source && jobId && range && (
          <Panel title={t('studio.clone.step2')}>
            <p className="text-xs text-muted-foreground">{t('studio.clone.pickHint')}</p>
            <WavePicker
              peaks={source.peaks}
              duration={source.source_seconds}
              url={studioApi.labClipUrl(jobId, 'full_clean')}
              start={range[0]}
              end={range[1]}
              onChange={(a, b) => setRange([a, b])}
            />
            {length > OMNIVOICE_MAX_SECONDS && (
              <p className="text-xs text-amber-500">{t('studio.clone.longForOmni')}</p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('studio.common.language')}>
                <LanguagePicker
                  value={language}
                  onChange={setLanguage}
                  auto={t('studio.common.autoDetect')}
                />
              </Field>
            </div>
            <Field label={t('studio.clone.reference')} hint={t('studio.clone.referenceHint')}>
              <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} />
            </Field>
            <Button
              className="w-full"
              disabled={selecting || length > MAX_CLIP_SECONDS}
              onClick={() => select.mutate()}
            >
              {selecting ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Scissors className="h-4 w-4 mr-2" />
              )}
              {selecting ? t('studio.clone.stage.transcribing') : t('studio.clone.useSelection')}
            </Button>
            {job?.error && job.stage === 'ready' && <ErrorNote>{job.error}</ErrorNote>}
          </Panel>
        )}

        {result && jobId && (
          <Panel title={t('studio.clone.step3')} className="xl:col-span-2">
            <p className="text-xs text-muted-foreground">
              {t('studio.clone.window', {
                from: result.window[0].toFixed(1),
                to: result.window[1].toFixed(1),
              })}
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <ClipCard
                title={t('studio.clone.clean')}
                url={studioApi.labClipUrl(jobId, 'clean')}
                analysis={result.clean}
                selected={which === 'clean'}
                onSelect={() => setWhich('clean')}
              />
              <ClipCard
                title={t('studio.clone.raw')}
                url={studioApi.labClipUrl(jobId, 'raw')}
                analysis={result.raw}
                selected={which === 'raw'}
                onSelect={() => setWhich('raw')}
              />
            </div>
            <Field label={t('studio.clone.transcript')} hint={t('studio.clone.transcriptHint')}>
              <Textarea
                value={currentText}
                onChange={(e) => setTranscript(e.target.value)}
                rows={3}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('studio.common.language')}>
                <LanguagePicker value={currentLanguage} onChange={setVoiceLanguage} />
              </Field>
              <Field label={t('studio.clone.defaultModel')}>
                <ModelPicker value={model} onChange={setModel} language={currentLanguage} />
              </Field>
              <Field label={t('studio.clone.name')}>
                <Input value={name} onChange={(e) => setName(e.target.value)} disabled={!!target} />
              </Field>
              <Field label={t('studio.clone.addTo')}>
                <VoicePicker
                  value={target}
                  onChange={setTarget}
                  emptyLabel={t('studio.clone.newVoice')}
                />
              </Field>
            </div>
            <Button
              className="w-full"
              disabled={save.isPending || !currentText.trim() || (!target && !name.trim())}
              onClick={() => save.mutate()}
            >
              {save.isPending ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4 mr-2" />
              )}
              {t('studio.clone.save')}
            </Button>
          </Panel>
        )}

        {createdId && (
          <div className="xl:col-span-2 space-y-3">
            <AuditionPanel
              profileId={createdId}
              language={currentLanguage}
              autoRun={parseEngine(model ?? 'omnivoice').engine}
            />
            <Button variant="outline" onClick={() => navigate({ to: '/voices' })}>
              {t('studio.clone.toLibrary')}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
