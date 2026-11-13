import { useQuery } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LOUDNESS_PRESETS } from '@/components/ServerTab/OutputSettingsSection';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { Toggle } from '@/components/ui/toggle';
import { apiClient } from '@/lib/api/client';
import type { EffectConfig, EffectPresetResponse } from '@/lib/api/types';
import { MODEL_PARAMS, type ParamSpec } from '@/lib/constants/modelParams';
import { useExtrasSettings } from '@/lib/hooks/useExtras';
import { cn } from '@/lib/utils/cn';
import { Field, Panel, SliderField } from './common';

/** 'voice' = the voice's own effects, 'none' = no effects, else an effect preset id. */
export type EffectsChoice = string;

export function useEffectPresets() {
  return useQuery({ queryKey: ['effectPresets'], queryFn: () => apiClient.listEffectPresets() });
}

/** The effects_chain to send: undefined keeps the voice's own chain. */
export function effectsChainFor(
  choice: EffectsChoice,
  presets: EffectPresetResponse[] | undefined,
): EffectConfig[] | undefined {
  if (choice === 'voice') return undefined;
  if (choice === 'none') return [];
  return presets?.find((p) => p.id === choice)?.effects_chain;
}

function ParamSlider({
  label,
  hint,
  value,
  def,
  min,
  max,
  step,
  onCommit,
}: {
  label: string;
  hint: string;
  value: number;
  def: number;
  min: number;
  max: number;
  step: number;
  onCommit: (v: number) => void;
}) {
  // Dragging only moves the thumb; the server is written once on release.
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const decimals = step >= 1 ? 0 : (String(step).split('.')[1]?.length ?? 2);
  return (
    <div className="space-y-2" title={hint}>
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span className={cn('tabular-nums', v !== def && 'text-accent font-semibold')}>
          {v.toFixed(decimals)}
        </span>
      </div>
      <Slider
        value={[v]}
        min={min}
        max={max}
        step={step}
        onValueChange={([x]) => setV(x)}
        onValueCommit={([x]) => onCommit(x)}
        aria-label={label}
      />
    </div>
  );
}

/**
 * Settings that change with the model, then the ones every model has:
 * noise removal and post-processing. Model settings are saved on the server
 * per model, so every mode (single, script, batch, multi-voice) uses them.
 */
export function VoiceTuning({
  engine,
  denoise,
  onDenoise,
  effects,
  onEffects,
}: {
  engine: string;
  denoise: number;
  onDenoise: (v: number) => void;
  effects: EffectsChoice;
  onEffects: (v: EffectsChoice) => void;
}) {
  const { t } = useTranslation();
  const { settings: extras, update } = useExtrasSettings();
  const { data: presets } = useEffectPresets();
  const specs: ParamSpec[] = MODEL_PARAMS[engine] ?? [];
  const saved = extras?.model_params?.[engine] ?? {};
  const steps = extras?.omnivoice_steps ?? 32;
  const changed = Object.keys(saved).length > 0 || (engine === 'omnivoice' && steps !== 32);

  const setParam = (key: string, value: number | boolean) =>
    update({
      model_params: { ...extras?.model_params, [engine]: { ...saved, [key]: value } },
    });
  const reset = () =>
    update({
      model_params: { ...extras?.model_params, [engine]: {} },
      ...(engine === 'omnivoice' ? { omnivoice_steps: 32 } : {}),
    });
  const loudness =
    extras?.loudness_target_lufs == null ? 'off' : String(extras.loudness_target_lufs);

  return (
    <>
      {specs.length > 0 && (
        <Panel>
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">{t('studio.tune.model')}</h2>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              disabled={!changed}
              onClick={reset}
            >
              <RotateCcw className="h-3 w-3 mr-1" />
              {t('studio.tune.reset')}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t('studio.tune.modelHint')}</p>
          {engine === 'omnivoice' && (
            <ParamSlider
              label={t('studio.tune.p.num_step')}
              hint={t('studio.tune.h.num_step')}
              value={steps}
              def={32}
              min={16}
              max={64}
              step={1}
              onCommit={(v) => update({ omnivoice_steps: v })}
            />
          )}
          {specs.map((s) =>
            s.kind === 'bool' ? (
              <label
                key={s.key}
                htmlFor={`tune-${s.key}`}
                className="flex items-center justify-between gap-2 text-xs text-muted-foreground"
                title={t(`studio.tune.h.${s.key}`)}
              >
                <span>{t(`studio.tune.p.${s.key}`)}</span>
                <Toggle
                  id={`tune-${s.key}`}
                  checked={(saved[s.key] as boolean | undefined) ?? (s.def as boolean)}
                  onCheckedChange={(v) => setParam(s.key, v)}
                />
              </label>
            ) : (
              <ParamSlider
                key={s.key}
                label={t(`studio.tune.p.${s.key}`)}
                hint={t(`studio.tune.h.${s.key}`)}
                value={(saved[s.key] as number | undefined) ?? (s.def as number)}
                def={s.def as number}
                min={s.min as number}
                max={s.max as number}
                step={s.step as number}
                onCommit={(v) => setParam(s.key, v)}
              />
            ),
          )}
        </Panel>
      )}
      <Panel title={t('studio.tune.post')}>
        <SliderField
          label={t('studio.common.denoise')}
          value={denoise}
          min={0}
          max={1}
          step={0.1}
          format={(v) => (v ? `${Math.round(v * 100)}%` : t('studio.common.off'))}
          onChange={onDenoise}
        />
        <Field label={t('studio.tune.effects')} hint={t('studio.tune.effectsHint')}>
          <Select value={effects} onValueChange={onEffects}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="voice">{t('studio.tune.effectsVoice')}</SelectItem>
              <SelectItem value="none">{t('studio.tune.effectsNone')}</SelectItem>
              {presets?.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label={t('extras.export.loudness.title')} hint={t('studio.tune.loudnessHint')}>
          <Select
            value={loudness}
            onValueChange={(v) => update({ loudness_target_lufs: v === 'off' ? null : Number(v) })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LOUDNESS_PRESETS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {t(`extras.export.loudness.presets.${p.key}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </Panel>
    </>
  );
}
