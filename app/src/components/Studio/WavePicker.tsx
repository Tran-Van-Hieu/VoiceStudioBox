import { Pause, Play } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils/cn';

const BARS = 400;
const MIN_LENGTH = 0.5;

type Handle = 'start' | 'end' | 'move';

/** The whole recording as a waveform; drag to mark the part to clone. */
export function WavePicker(props: {
  peaks: number[];
  duration: number;
  url: string;
  start: number;
  end: number;
  onChange: (start: number, end: number) => void;
}) {
  const { t } = useTranslation();
  const { peaks, duration, url, start, end, onChange } = props;
  const box = useRef<HTMLDivElement>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);
  const drag = useRef<{ handle: Handle; anchor: number; start: number; end: number } | null>(null);

  const bars = useMemo(() => {
    const step = Math.max(1, Math.floor(peaks.length / BARS));
    const out: number[] = [];
    for (let i = 0; i < peaks.length; i += step) out.push(Math.max(...peaks.slice(i, i + step)));
    return out;
  }, [peaks]);

  useEffect(() => () => audio.current?.pause(), []);

  const timeAt = (clientX: number) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return 0;
    return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)) * duration;
  };

  const begin = (handle: Handle) => (e: React.PointerEvent) => {
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { handle, anchor: timeAt(e.clientX), start, end };
  };

  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const now = timeAt(e.clientX);
    if (d.handle === 'start') onChange(Math.min(now, d.end - MIN_LENGTH), d.end);
    else if (d.handle === 'end') onChange(d.start, Math.max(now, d.start + MIN_LENGTH));
    else {
      const length = d.end - d.start;
      const from = Math.min(duration - length, Math.max(0, d.start + now - d.anchor));
      onChange(from, from + length);
    }
  };

  const toggle = () => {
    if (playing) {
      audio.current?.pause();
      setPlaying(false);
      return;
    }
    const element = audio.current ?? new Audio(url);
    audio.current = element;
    element.currentTime = start;
    element.ontimeupdate = () => {
      setCursor(element.currentTime);
      if (element.currentTime >= end) {
        element.pause();
        setPlaying(false);
        setCursor(null);
      }
    };
    element.play();
    setPlaying(true);
  };

  const percent = (v: number) => `${(v / Math.max(duration, 0.01)) * 100}%`;
  const number = (value: number, set: (v: number) => void, label: string) => (
    <Input
      type="number"
      className="h-8 w-24 text-xs tabular-nums"
      aria-label={label}
      title={label}
      min={0}
      max={duration}
      step={0.1}
      value={value.toFixed(1)}
      onChange={(e) => set(Number(e.target.value) || 0)}
    />
  );

  return (
    <div className="space-y-2">
      <div
        ref={box}
        className="relative h-24 rounded-md bg-muted/40 select-none touch-none overflow-hidden"
        onPointerMove={move}
        onPointerUp={() => {
          drag.current = null;
        }}
      >
        <div className="absolute inset-0 flex items-center gap-px" aria-hidden="true">
          {bars.map((v, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: bars never reorder
            <span
              key={i}
              className="flex-1 bg-muted-foreground/50"
              style={{ height: `${Math.max(2, v * 100)}%` }}
            />
          ))}
        </div>
        <div
          className="absolute inset-y-0 bg-accent/25 border-x border-accent cursor-grab"
          style={{ left: percent(start), width: percent(end - start) }}
          onPointerDown={begin('move')}
          onPointerMove={move}
          onPointerUp={() => {
            drag.current = null;
          }}
        >
          {(['start', 'end'] as const).map((h) => (
            <span
              key={h}
              role="slider"
              tabIndex={0}
              aria-label={t(`studio.clone.${h}`)}
              aria-valuenow={h === 'start' ? start : end}
              onPointerDown={begin(h)}
              onPointerMove={move}
              onPointerUp={() => {
                drag.current = null;
              }}
              className={cn(
                'absolute inset-y-0 w-3 bg-accent cursor-ew-resize',
                h === 'start' ? '-left-1.5' : '-right-1.5',
              )}
            />
          ))}
        </div>
        {cursor !== null && (
          <div
            className="absolute inset-y-0 w-px bg-foreground"
            style={{ left: percent(cursor) }}
          />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Button type="button" size="sm" variant="outline" className="h-8" onClick={toggle}>
          {playing ? (
            <Pause className="h-3.5 w-3.5 mr-1.5" />
          ) : (
            <Play className="h-3.5 w-3.5 mr-1.5" />
          )}
          {t('studio.clone.listenSelection')}
        </Button>
        {number(
          start,
          (v) => onChange(Math.min(Math.max(0, v), end - MIN_LENGTH), end),
          t('studio.clone.start'),
        )}
        <span>–</span>
        {number(
          end,
          (v) => onChange(start, Math.max(Math.min(duration, v), start + MIN_LENGTH)),
          t('studio.clone.end'),
        )}
        <span className="tabular-nums">
          {t('studio.clone.selected', {
            value: (end - start).toFixed(1),
            total: duration.toFixed(1),
          })}
        </span>
      </div>
    </div>
  );
}
