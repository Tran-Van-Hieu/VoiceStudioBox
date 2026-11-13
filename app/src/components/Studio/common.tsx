import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Loader2, Pause, Play, Wrench, XCircle } from 'lucide-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ENGINE_OPTIONS,
  isProfileCompatibleWithEngine,
} from '@/components/Generation/EngineModelSelector';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { useToast } from '@/components/ui/use-toast';
import type { VoiceProfileResponse } from '@/lib/api/types';
import { type FinalCheck, type QualityLevel, studioApi } from '@/lib/api/studio';
import { ALL_LANGUAGES, ENGINE_LANGUAGES, LANGUAGE_OPTIONS } from '@/lib/constants/languages';
import { useProfiles } from '@/lib/hooks/useProfiles';
import { cn } from '@/lib/utils/cn';
import { usePlatform } from '@/platform/PlatformContext';

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 pt-5 pb-4 shrink-0">
      <div className="min-w-0">
        {title && <h1 className="text-xl font-semibold">{title}</h1>}
        {subtitle && <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  children,
  className,
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('rounded-lg border bg-card p-4 space-y-3', className)}>
      {title && <h2 className="text-sm font-semibold">{title}</h2>}
      {children}
    </section>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="space-y-1.5 min-w-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function SliderField(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{props.label}</span>
        <span className="tabular-nums">{props.format(props.value)}</span>
      </div>
      <Slider
        value={[props.value]}
        min={props.min}
        max={props.max}
        step={props.step}
        onValueChange={([v]) => props.onChange(v)}
        aria-label={props.label}
      />
    </div>
  );
}

const NONE = '__none__';

export function VoicePicker({
  value,
  onChange,
  emptyLabel,
  className,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  emptyLabel?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const { data: profiles } = useProfiles();
  return (
    <Select value={value ?? NONE} onValueChange={(v) => onChange(v === NONE ? null : v)}>
      <SelectTrigger className={className}>
        <SelectValue placeholder={t('studio.common.chooseVoice')} />
      </SelectTrigger>
      <SelectContent>
        {emptyLabel !== undefined && <SelectItem value={NONE}>{emptyLabel}</SelectItem>}
        {profiles
          ?.filter((p) => (p.voice_type as string) !== 'import')
          .map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.name}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  );
}

export function LanguagePicker({
  value,
  onChange,
  className,
  auto,
}: {
  value: string;
  onChange: (v: string) => void;
  className?: string;
  auto?: string;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className={className}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {auto !== undefined && <SelectItem value="auto">{auto}</SelectItem>}
        {LANGUAGE_OPTIONS.map((l) => (
          <SelectItem key={l.value} value={l.value}>
            {l.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function engineSupports(engine: string, language: string): boolean {
  return (ENGINE_LANGUAGES[engine] as readonly string[] | undefined)?.includes(language) ?? false;
}

export function parseEngine(value: string): { engine: string; modelSize: string | null } {
  const [engine, size] = value.split(':');
  return { engine, modelSize: size ?? null };
}

/** Models a voice can use. One that cannot read the language is shown disabled, never switched silently. */
export function ModelPicker({
  value,
  onChange,
  language,
  profile,
  className,
  emptyLabel,
}: {
  value: string | null;
  onChange: (v: string | null) => void;
  language: string;
  profile?: VoiceProfileResponse | null;
  className?: string;
  emptyLabel?: string;
}) {
  const { t } = useTranslation();
  const options = ENGINE_OPTIONS.filter(
    (o) => !profile || isProfileCompatibleWithEngine(profile, o.engine),
  );
  const languageName = ALL_LANGUAGES[language as keyof typeof ALL_LANGUAGES] ?? language;
  return (
    <Select value={value ?? NONE} onValueChange={(v) => onChange(v === NONE ? null : v)}>
      <SelectTrigger className={className}>
        <SelectValue placeholder={t('studio.common.chooseModel')} />
      </SelectTrigger>
      <SelectContent>
        {emptyLabel !== undefined && <SelectItem value={NONE}>{emptyLabel}</SelectItem>}
        {options.map((o) => {
          const ok = engineSupports(o.engine, language);
          return (
            <SelectItem key={o.value} value={o.value} disabled={!ok}>
              {o.label}
              {!ok && ` · ${t('studio.common.noLanguage', { language: languageName })}`}
            </SelectItem>
          );
        })}
      </SelectContent>
    </Select>
  );
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '';
  const whole = Math.max(0, Math.round(seconds));
  const m = Math.floor(whole / 60);
  return `${m}:${String(whole % 60).padStart(2, '0')}`;
}

export function QualityBadge({
  level,
  className,
}: {
  level: QualityLevel | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!level) return null;
  const Icon = level === 'green' ? CheckCircle2 : level === 'yellow' ? AlertTriangle : XCircle;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 text-xs font-medium',
        level === 'green' && 'text-emerald-500',
        level === 'yellow' && 'text-amber-500',
        level === 'red' && 'text-red-500',
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {t(`studio.quality.level.${level}`)}
    </span>
  );
}

export function FinalCheckView({
  final,
  generationId,
  onRepaired,
}: {
  final: FinalCheck;
  generationId?: string;
  onRepaired?: () => void;
}) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const repair = useMutation({
    mutationFn: (index: number) => studioApi.repair(generationId as string, index),
    onSuccess: (result) => {
      if (result.kept_previous) toast({ title: t('studio.quality.keptPrevious') });
      onRepaired?.();
    },
    onError: (e: Error) =>
      toast({
        title: t('studio.quality.repairFailed'),
        description: e.message,
        variant: 'destructive',
      }),
  });
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <QualityBadge level={final.level} />
        {final.score !== null && (
          <span>{t('studio.quality.words', { value: Math.round(final.score * 100) })}</span>
        )}
        {final.speaker !== null && (
          <span>{t('studio.quality.voice', { value: Math.round(final.speaker * 100) })}</span>
        )}
        {final.metrics.lufs !== null && <span>{final.metrics.lufs.toFixed(1)} LUFS</span>}
      </div>
      {final.issues.length > 0 && (
        <ul className="space-y-1.5">
          {final.issues.map((issue, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: issues have no id
            <li key={i} className="rounded-md border p-2 text-xs space-y-1">
              <div className="flex items-center justify-between gap-2">
                <span className={issue.severity === 'red' ? 'text-red-500' : 'text-amber-500'}>
                  {issue.start !== undefined && `${formatSeconds(issue.start)} · `}
                  {t(`studio.quality.issue.${issue.kind}`, { value: issue.value })}
                </span>
                {generationId && issue.segments && issue.segments.length > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7"
                    disabled={repair.isPending}
                    onClick={async () => {
                      for (const index of [...(issue.segments as number[])].reverse()) {
                        await repair.mutateAsync(index);
                      }
                    }}
                  >
                    {repair.isPending ? (
                      <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                    ) : (
                      <Wrench className="h-3 w-3 mr-1" />
                    )}
                    {t('studio.quality.repair')}
                  </Button>
                )}
              </div>
              {issue.heard && (
                <p className="text-muted-foreground">
                  {t('studio.quality.heard')}: {issue.heard}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{t('studio.quality.limit')}</p>
    </div>
  );
}

export function GenerationQuality({ id, refreshKey }: { id: string; refreshKey?: unknown }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const { data } = useQuery({
    queryKey: ['quality', id, refreshKey],
    queryFn: () => studioApi.quality(id),
  });
  if (!data) return null;
  if (!data.final)
    return <p className="text-xs text-muted-foreground">{t('studio.quality.notChecked')}</p>;
  return (
    <FinalCheckView
      final={data.final}
      generationId={id}
      onRepaired={() => {
        client.invalidateQueries({ queryKey: ['quality', id] });
        client.invalidateQueries({ queryKey: ['generation', id] });
        client.invalidateQueries({ queryKey: ['historySearch'] });
      }}
    />
  );
}

/** A small play/pause control for one URL; only one plays at a time. */
let stopCurrent: (() => void) | null = null;

export function PlayButton({
  url,
  className,
  label,
}: {
  url: string;
  className?: string;
  label?: string;
}) {
  const { t } = useTranslation();
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  useEffect(() => () => audio.current?.pause(), []);
  const toggle = () => {
    if (playing) {
      audio.current?.pause();
      setPlaying(false);
      return;
    }
    stopCurrent?.();
    const element = new Audio(`${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`);
    element.onended = () => setPlaying(false);
    audio.current = element;
    stopCurrent = () => {
      element.pause();
      setPlaying(false);
    };
    element.play();
    setPlaying(true);
  };
  const Icon = playing ? Pause : Play;
  return (
    <Button
      type="button"
      size={label ? 'sm' : 'icon'}
      variant="outline"
      className={cn(!label && 'h-8 w-8', className)}
      onClick={toggle}
      aria-label={label ?? t('studio.common.play')}
    >
      <Icon className={cn('h-3.5 w-3.5', label && 'mr-1.5')} />
      {label}
    </Button>
  );
}

export function useSaveBlob() {
  const platform = usePlatform();
  return (name: string, blob: Blob, extension: string) =>
    platform.filesystem.saveFile(name, blob, [
      { name: extension.toUpperCase(), extensions: [extension] },
    ]);
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <p className="text-xs text-red-500 whitespace-pre-wrap">{children}</p>;
}
