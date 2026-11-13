import type { RefObject } from 'react';
import type { UseFormReturn } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { EMOTIONS, type EmotionId, emotionSupport } from '@/lib/constants/emotions';
import type { GenerationFormValues } from '@/lib/hooks/useGenerationForm';

const TRIGGER =
  'h-8 text-xs bg-card border-border rounded-full hover:bg-background/50 transition-all';

interface EmotionControlsProps {
  form: UseFormReturn<GenerationFormValues>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

/** Emotion, intensity and inline-tag insertion for the generate box. */
export function EmotionControls({ form, textareaRef }: EmotionControlsProps) {
  const { t } = useTranslation();
  const emotion = form.watch('emotion') ?? 'neutral';
  const intensity = form.watch('emotionIntensity') ?? 0.6;
  const support = emotionSupport(form.watch('engine'));

  const insertTag = (tag: string) => {
    const text = form.getValues('text') ?? '';
    const el = textareaRef.current;
    const at = el?.selectionStart ?? text.length;
    const before = text.slice(0, at);
    const pad = before && !before.endsWith(' ') ? ' ' : '';
    const inserted = `${pad}[${tag}] `;
    form.setValue('text', before + inserted + text.slice(at), { shouldDirty: true });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(at + inserted.length, at + inserted.length);
    });
  };

  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex items-center gap-2">
        <div className="flex-1 min-w-0">
          <Select value={emotion} onValueChange={(v) => form.setValue('emotion', v as EmotionId)}>
            <SelectTrigger className={TRIGGER} aria-label={t('emotion.label')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent side="top">
              {EMOTIONS.map((e) => (
                <SelectItem key={e.id} value={e.id} className="text-xs">
                  {t(`emotion.names.${e.id}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1 min-w-0 flex items-center gap-2 px-2">
          <span className="text-xs text-muted-foreground shrink-0">{t('emotion.intensity')}</span>
          <Slider
            value={[intensity]}
            onValueChange={([v]) => form.setValue('emotionIntensity', v)}
            min={0}
            max={1}
            step={0.05}
            disabled={emotion === 'neutral'}
            aria-label={t('emotion.intensity')}
          />
          <span className="text-xs tabular-nums text-muted-foreground w-8 text-right">
            {Math.round(intensity * 100)}%
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <Select value="" onValueChange={insertTag}>
            <SelectTrigger className={TRIGGER} aria-label={t('emotion.insertTag')}>
              <SelectValue placeholder={t('emotion.insertTag')} />
            </SelectTrigger>
            <SelectContent side="top">
              {EMOTIONS.map((e) => (
                <SelectItem key={e.id} value={e.tag} className="text-xs">
                  [{e.tag}]
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <p className="text-[11px] leading-snug text-muted-foreground px-1">
        {t(`emotion.support.${support}`)}
      </p>
    </div>
  );
}
