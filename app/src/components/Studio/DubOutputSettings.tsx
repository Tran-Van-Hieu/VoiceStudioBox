import { useTranslation } from 'react-i18next';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { DubOutput, DubProject, DubSubtitle } from '@/lib/api/studio';
import { Field, SliderField } from './common';

export const SUBTITLE_DEFAULTS: DubSubtitle = {
  burn: false,
  which: 'target',
  font: 'Arial',
  size: 18,
  color: '#FFFFFF',
  outline_color: '#000000',
  outline: 2,
  bold: false,
  position: 'bottom',
  margin: 20,
};

export const DUB_OUTPUT_DEFAULTS: DubOutput = {
  original_volume: 0,
  music_volume: 1,
  subtitle: SUBTITLE_DEFAULTS,
};

export function dubOutputOf(project: DubProject): DubOutput {
  return {
    original_volume: project.original_volume ?? 0,
    music_volume: project.music_volume ?? 1,
    subtitle: { ...SUBTITLE_DEFAULTS, ...project.subtitle },
  };
}

const FONTS = ['Arial', 'Segoe UI', 'Tahoma', 'Verdana', 'Times New Roman', 'Roboto', 'Calibri'];
// libass lays subtitles out on a frame 288 units tall; the preview scales the same way.
const PREVIEW_HEIGHT = 162;
const unit = (v: number) => (v * PREVIEW_HEIGHT) / 288;

function SubtitlePreview({ style }: { style: DubSubtitle }) {
  const { t } = useTranslation();
  const place =
    style.position === 'top'
      ? { top: unit(style.margin) }
      : style.position === 'middle'
        ? { top: '50%', transform: 'translateY(-50%)' }
        : { bottom: unit(style.margin) };
  return (
    <div
      className="relative w-full max-w-[288px] overflow-hidden rounded-md bg-gradient-to-br from-slate-600 to-slate-900"
      style={{ height: PREVIEW_HEIGHT, aspectRatio: '16 / 9' }}
      role="img"
      aria-label={t('studio.dub.out.preview')}
    >
      <p
        className="absolute inset-x-2 text-center leading-tight"
        style={{
          ...place,
          fontFamily: style.font,
          fontSize: unit(style.size),
          fontWeight: style.bold ? 700 : 400,
          color: style.color,
          WebkitTextStroke: `${unit(style.outline) * 2}px ${style.outline_color}`,
          paintOrder: 'stroke fill',
        }}
      >
        Xin chào, đây là phụ đề mẫu.
      </p>
    </div>
  );
}

/** Voice match, the two levels under dubbed lines, and burned-in subtitles. */
export function DubOutputSettings({
  value,
  onChange,
}: {
  value: DubOutput;
  onChange: (next: DubOutput) => void;
}) {
  const { t } = useTranslation();
  const sub = value.subtitle;
  const setSub = (patch: Partial<DubSubtitle>) =>
    onChange({ ...value, subtitle: { ...sub, ...patch } });
  const pct = (v: number) => (v ? `${Math.round(v * 100)}%` : t('studio.dub.out.off'));

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <SliderField
          label={t('studio.dub.out.vocals')}
          value={value.original_volume}
          min={0}
          max={1}
          step={0.05}
          format={pct}
          onChange={(original_volume) => onChange({ ...value, original_volume })}
        />
        <SliderField
          label={t('studio.dub.out.music')}
          value={value.music_volume}
          min={0}
          max={1}
          step={0.05}
          format={pct}
          onChange={(music_volume) => onChange({ ...value, music_volume })}
        />
      </div>
      <p className="text-xs text-muted-foreground">{t('studio.dub.out.levelHint')}</p>

      <label className="flex items-center gap-2 text-sm cursor-pointer w-fit">
        <Checkbox checked={sub.burn} onCheckedChange={(v) => setSub({ burn: !!v })} />
        {t('studio.dub.out.burn')}
      </label>
      {sub.burn && (
        <div className="grid gap-3 2xl:grid-cols-[minmax(0,1fr)_288px] rounded-md border p-3">
          <div className="space-y-3 min-w-0">
            <p className="text-xs text-muted-foreground">{t('studio.dub.out.burnHint')}</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('studio.dub.out.which')}>
                <Select
                  value={sub.which}
                  onValueChange={(v) => setSub({ which: v as DubSubtitle['which'] })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="target">{t('studio.dub.out.whichTarget')}</SelectItem>
                    <SelectItem value="source">{t('studio.dub.out.whichSource')}</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label={t('studio.dub.out.font')} hint={t('studio.dub.out.fontHint')}>
                <Input
                  list="dub-fonts"
                  value={sub.font}
                  onChange={(e) => setSub({ font: e.target.value })}
                />
                <datalist id="dub-fonts">
                  {FONTS.map((f) => (
                    <option key={f} value={f} />
                  ))}
                </datalist>
              </Field>
              <Field label={t('studio.dub.out.position')}>
                <Select
                  value={sub.position}
                  onValueChange={(v) => setSub({ position: v as DubSubtitle['position'] })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="bottom">{t('studio.dub.out.bottom')}</SelectItem>
                    <SelectItem value="middle">{t('studio.dub.out.middle')}</SelectItem>
                    <SelectItem value="top">{t('studio.dub.out.top')}</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <div className="flex items-end gap-4">
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="color"
                    value={sub.color}
                    onChange={(e) => setSub({ color: e.target.value.toUpperCase() })}
                    className="h-8 w-10 cursor-pointer rounded border bg-transparent"
                  />
                  {t('studio.dub.out.color')}
                </label>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="color"
                    value={sub.outline_color}
                    onChange={(e) => setSub({ outline_color: e.target.value.toUpperCase() })}
                    className="h-8 w-10 cursor-pointer rounded border bg-transparent"
                  />
                  {t('studio.dub.out.outlineColor')}
                </label>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <SliderField
                label={t('studio.dub.out.size')}
                value={sub.size}
                min={6}
                max={60}
                step={1}
                format={(v) => String(v)}
                onChange={(size) => setSub({ size })}
              />
              <SliderField
                label={t('studio.dub.out.outline')}
                value={sub.outline}
                min={0}
                max={8}
                step={0.5}
                format={(v) => v.toFixed(1)}
                onChange={(outline) => setSub({ outline })}
              />
              <SliderField
                label={t('studio.dub.out.margin')}
                value={sub.margin}
                min={0}
                max={150}
                step={1}
                format={(v) => String(v)}
                onChange={(margin) => setSub({ margin })}
              />
            </div>
            <label className="flex items-center gap-2 text-xs cursor-pointer w-fit">
              <Checkbox checked={sub.bold} onCheckedChange={(v) => setSub({ bold: !!v })} />
              {t('studio.dub.out.bold')}
            </label>
          </div>
          <SubtitlePreview style={sub} />
        </div>
      )}
    </div>
  );
}
