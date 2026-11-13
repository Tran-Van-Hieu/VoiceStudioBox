import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { Toggle } from '@/components/ui/toggle';
import type { ExportFormat } from '@/lib/api/extras';
import { useExtrasSettings } from '@/lib/hooks/useExtras';
import { SettingRow, SettingSection } from './SettingRow';

const BITRATES = [96, 128, 160, 192, 256, 320];
export const LOUDNESS_PRESETS: { value: string; key: string }[] = [
  { value: 'off', key: 'off' },
  { value: '-14', key: 'streaming' },
  { value: '-16', key: 'podcast' },
  { value: '-19', key: 'mono' },
  { value: '-23', key: 'broadcast' },
];

/** Speed + export/loudness defaults, shown on the Generation settings page. */
export function OutputSettingsSection() {
  const { t } = useTranslation();
  const { settings, update } = useExtrasSettings();
  const persistedSpeed = settings?.speed ?? 1;
  const [speed, setSpeed] = useState(persistedSpeed);
  useEffect(() => setSpeed(persistedSpeed), [persistedSpeed]);

  const format = settings?.export_format ?? 'wav';
  const bitrate = settings?.mp3_bitrate ?? 192;
  const loudness =
    settings?.loudness_target_lufs === null || settings?.loudness_target_lufs === undefined
      ? 'off'
      : String(settings.loudness_target_lufs);

  return (
    <>
      <SettingSection
        title={t('extras.speed.sectionTitle')}
        description={t('extras.speed.sectionDescription')}
      >
        <SettingRow
          title={t('extras.speed.title')}
          description={t('extras.speed.description')}
          action={
            <span className="text-sm tabular-nums text-muted-foreground">
              {t('extras.speed.value', { speed: speed.toFixed(2) })}
            </span>
          }
        >
          <Slider
            value={[speed]}
            onValueChange={([v]) => setSpeed(v)}
            onValueCommit={([v]) => update({ speed: v })}
            min={0.5}
            max={2}
            step={0.05}
            aria-label={t('extras.speed.title')}
          />
          <div className="flex justify-between text-xs text-muted-foreground mt-1.5">
            <span>0.5×</span>
            <button
              type="button"
              className="hover:text-foreground underline-offset-2 hover:underline"
              onClick={() => update({ speed: 1 })}
            >
              {t('extras.speed.reset')}
            </button>
            <span>2.0×</span>
          </div>
        </SettingRow>
      </SettingSection>

      <SettingSection
        title={t('extras.export.sectionTitle')}
        description={t('extras.export.sectionDescription')}
      >
        <SettingRow
          title={t('extras.export.format.title')}
          description={t('extras.export.format.description')}
          action={
            <Select
              value={format}
              onValueChange={(v) => update({ export_format: v as ExportFormat })}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="wav">WAV</SelectItem>
                <SelectItem value="mp3">MP3</SelectItem>
                <SelectItem value="flac">FLAC</SelectItem>
              </SelectContent>
            </Select>
          }
        />
        <SettingRow
          title={t('extras.export.bitrate.title')}
          description={t('extras.export.bitrate.description')}
          action={
            <Select
              value={String(bitrate)}
              onValueChange={(v) => update({ mp3_bitrate: Number(v) })}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BITRATES.map((b) => (
                  <SelectItem key={b} value={String(b)}>
                    {b} kbps
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          }
        />
        <SettingRow
          title={t('extras.export.loudness.title')}
          description={t('extras.export.loudness.description')}
          action={
            <Select
              value={loudness}
              onValueChange={(v) =>
                update({ loudness_target_lufs: v === 'off' ? null : Number(v) })
              }
            >
              <SelectTrigger className="w-[220px]">
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
          }
        />
      </SettingSection>

      <SettingSection
        title={t('extras.qc.sectionTitle')}
        description={t('extras.qc.sectionDescription')}
      >
        <SettingRow
          title={t('extras.qc.mode.title')}
          description={t('extras.qc.mode.description')}
          action={
            <Select
              value={settings?.qc_mode ?? 'omnivoice'}
              onValueChange={(v) => update({ qc_mode: v as 'off' | 'omnivoice' | 'all' })}
            >
              <SelectTrigger className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="omnivoice">{t('extras.qc.mode.omnivoice')}</SelectItem>
                <SelectItem value="all">{t('extras.qc.mode.all')}</SelectItem>
                <SelectItem value="off">{t('extras.qc.mode.off')}</SelectItem>
              </SelectContent>
            </Select>
          }
        />
        <SettingRow
          title={t('extras.qc.steps.title')}
          description={t('extras.qc.steps.description')}
          action={
            <Select
              value={String(settings?.omnivoice_steps ?? 32)}
              onValueChange={(v) => update({ omnivoice_steps: Number(v) })}
            >
              <SelectTrigger className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="16">{t('extras.qc.steps.fast')}</SelectItem>
                <SelectItem value="32">{t('extras.qc.steps.standard')}</SelectItem>
                <SelectItem value="48">{t('extras.qc.steps.best')}</SelectItem>
              </SelectContent>
            </Select>
          }
        />
        <SettingRow
          title={t('extras.qc.cooldown.title')}
          description={t('extras.qc.cooldown.description')}
          action={
            <Select
              value={String(settings?.queue_cooldown_seconds ?? 120)}
              onValueChange={(v) => update({ queue_cooldown_seconds: Number(v) })}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">{t('extras.qc.cooldown.off')}</SelectItem>
                {[30, 60, 120, 300, 600].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {t('extras.qc.cooldown.seconds', { count: n })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          }
        />
        <SettingRow
          title={t('extras.qc.retries.title')}
          description={t('extras.qc.retries.description')}
          action={
            <Select
              value={String(settings?.qc_max_retries ?? 0)}
              onValueChange={(v) => update({ qc_max_retries: Number(v) })}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">{t('extras.qc.retries.none')}</SelectItem>
                {[1, 2, 3, 4, 5].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          }
        />
        <SettingRow
          title={t('extras.qc.match.title')}
          description={t('extras.qc.match.description')}
          action={
            <Select
              value={settings?.qc_match ?? 'exact'}
              onValueChange={(v) => update({ qc_match: v as 'exact' | 'tolerant' })}
            >
              <SelectTrigger className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="exact">{t('extras.qc.match.exact')}</SelectItem>
                <SelectItem value="tolerant">{t('extras.qc.match.tolerant')}</SelectItem>
              </SelectContent>
            </Select>
          }
        />
        {settings?.qc_match === 'tolerant' && (
          <SettingRow
            title={t('extras.qc.threshold.title', {
              value: Math.round((settings?.qc_threshold ?? 0.75) * 100),
            })}
            description={t('extras.qc.threshold.description')}
            action={
              <Slider
                className="w-[220px]"
                min={0.4}
                max={0.95}
                step={0.05}
                value={[settings?.qc_threshold ?? 0.75]}
                onValueChange={([v]) => update({ qc_threshold: v })}
              />
            }
          />
        )}
        <SettingRow
          title={t('extras.qc.tones.title')}
          description={t('extras.qc.tones.description')}
          action={
            <Toggle
              checked={settings?.qc_tones ?? false}
              onCheckedChange={(v) => update({ qc_tones: v })}
            />
          }
        />
      </SettingSection>
    </>
  );
}
