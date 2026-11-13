import { Cpu, Loader2, Power } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Toggle } from '@/components/ui/toggle';
import { useToast } from '@/components/ui/use-toast';
import { extrasApi } from '@/lib/api/extras';
import { useExtrasSettings, useVramStatus } from '@/lib/hooks/useExtras';
import { cn } from '@/lib/utils/cn';
import { SettingRow, SettingSection } from './SettingRow';

const IDLE_OPTIONS = [0, 2, 5, 10, 15, 30, 60];

function formatMb(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return '—';
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb} MB`;
}

export function VramPage() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const { data: vram, refetch, isLoading } = useVramStatus(2000);
  const { settings, update } = useExtrasSettings();
  const [unloading, setUnloading] = useState(false);

  const pct =
    vram?.total_mb && vram.used_mb !== null
      ? Math.min(100, (vram.used_mb / vram.total_mb) * 100)
      : 0;
  const barColor = pct > 90 ? 'bg-red-500' : pct > 75 ? 'bg-amber-500' : 'bg-accent';

  const unloadAll = async (includeAll: boolean) => {
    setUnloading(true);
    try {
      const r = await extrasApi.unloadVram(includeAll);
      toast({
        title: r.unloaded.length
          ? t('extras.vram.unloaded', { models: r.unloaded.join(', ') })
          : t('extras.vram.nothingToUnload'),
      });
      refetch();
    } catch (e) {
      toast({ title: (e as Error).message, variant: 'destructive' });
    } finally {
      setUnloading(false);
    }
  };

  return (
    <div className="max-w-3xl space-y-8">
      <SettingSection title={t('extras.vram.title')} description={t('extras.vram.description')}>
        <div className="py-4 space-y-3">
          {isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : vram?.cuda_available ? (
            <>
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm font-medium flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-accent" />
                  {vram.device_name}
                </span>
                <span className="text-sm tabular-nums text-muted-foreground">
                  {formatMb(vram.used_mb)} / {formatMb(vram.total_mb)}
                </span>
              </div>
              <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className={cn('h-full transition-all', barColor)}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="grid grid-cols-3 gap-3 text-xs text-muted-foreground">
                <div>
                  {t('extras.vram.free')}:{' '}
                  <span className="text-foreground">{formatMb(vram.free_mb)}</span>
                </div>
                <div>
                  {t('extras.vram.allocated')}:{' '}
                  <span className="text-foreground">{formatMb(vram.torch_allocated_mb)}</span>
                </div>
                <div>
                  {t('extras.vram.reserved')}:{' '}
                  <span className="text-foreground">{formatMb(vram.torch_reserved_mb)}</span>
                </div>
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">{t('extras.vram.noCuda')}</p>
          )}
        </div>

        <div className="py-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">{t('extras.vram.loadedModels')}</span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={unloading}
                onClick={() => unloadAll(false)}
              >
                <Power className="h-3.5 w-3.5 mr-1.5" />
                {t('extras.vram.unloadTts')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={unloading}
                onClick={() => unloadAll(true)}
              >
                {t('extras.vram.unloadAll')}
              </Button>
            </div>
          </div>
          {vram?.loaded_models.length ? (
            <ul className="space-y-1">
              {vram.loaded_models.map((m) => (
                <li
                  key={m.model_name}
                  className="flex items-center justify-between rounded-md border px-3 py-1.5 text-sm"
                >
                  <span>{m.display_name}</span>
                  {m.in_use && (
                    <span className="text-xs text-accent">{t('extras.vram.inUse')}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">{t('extras.vram.noneLoaded')}</p>
          )}
        </div>
      </SettingSection>

      <SettingSection title={t('extras.vram.autoTitle')}>
        <SettingRow
          title={t('extras.vram.idle.title')}
          description={t('extras.vram.idle.description')}
          action={
            <Select
              value={String(settings?.vram_idle_unload_minutes ?? 0)}
              onValueChange={(v) => update({ vram_idle_unload_minutes: Number(v) })}
            >
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {IDLE_OPTIONS.map((m) => (
                  <SelectItem key={m} value={String(m)}>
                    {m === 0
                      ? t('extras.vram.idle.never')
                      : t('extras.vram.idle.minutes', { count: m })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          }
        />
        <SettingRow
          title={t('extras.vram.exclusive.title')}
          description={t('extras.vram.exclusive.description')}
          htmlFor="vramExclusive"
          action={
            <Toggle
              id="vramExclusive"
              checked={settings?.vram_exclusive_engine ?? true}
              onCheckedChange={(v) => update({ vram_exclusive_engine: v })}
            />
          }
        />
      </SettingSection>
    </div>
  );
}
