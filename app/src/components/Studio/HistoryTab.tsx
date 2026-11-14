import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import {
  Download,
  Heart,
  Loader2,
  Pencil,
  Pin,
  Play,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ENGINE_OPTIONS } from '@/components/Generation/EngineModelSelector';
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
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import { type HistoryFilters, type HistoryItem, studioApi } from '@/lib/api/studio';
import { useProfiles } from '@/lib/hooks/useProfiles';
import { cn } from '@/lib/utils/cn';
import { usePlayerStore } from '@/stores/playerStore';
import {
  ErrorNote,
  formatSeconds,
  GenerationQuality,
  PageHeader,
  QualityBadge,
  useSaveBlob,
} from './common';
import { PREFILL_KEY } from './SpeakTab';

const ALL = '__all__';
const PAGE = 30;

function FilterSelect(props: {
  value?: string;
  onChange: (v: string | undefined) => void;
  all: string;
  options: { value: string; label: string }[];
}) {
  return (
    <Select
      value={props.value ?? ALL}
      onValueChange={(v) => props.onChange(v === ALL ? undefined : v)}
    >
      <SelectTrigger className="h-8 w-auto min-w-[130px] text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{props.all}</SelectItem>
        {props.options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function Row({
  item,
  selected,
  onSelect,
  refresh,
}: {
  item: HistoryItem;
  selected: boolean;
  onSelect: (v: boolean) => void;
  refresh: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { toast } = useToast();
  const navigate = useNavigate();
  const setAudio = usePlayerStore((s) => s.setAudioWithAutoPlay);
  const [open, setOpen] = useState(false);
  const handleError = (e: Error) => toast({ title: e.message, variant: 'destructive' });
  const date = item.created_at
    ? new Date(`${item.created_at}Z`).toLocaleString(i18n.language || undefined)
    : '';
  const ready = item.status === 'completed';

  const editTags = () => {
    const value = window.prompt(t('studio.history.tagsPrompt'), item.tags.join(', '));
    if (value !== null)
      studioApi
        .historyMeta(item.id, { tags: value.split(',') })
        .then(refresh)
        .catch(handleError);
  };
  const reuse = () => {
    sessionStorage.setItem(
      PREFILL_KEY,
      JSON.stringify({ text: item.text, profileId: item.profile_id, language: item.language }),
    );
    navigate({ to: '/' });
  };

  return (
    <div className="border-b px-3 py-2.5">
      <div className="flex items-start gap-3">
        <Checkbox
          className="mt-1"
          checked={selected}
          onCheckedChange={(v) => onSelect(!!v)}
          aria-label={t('studio.history.select')}
        />
        <Button
          size="icon"
          variant="outline"
          className="h-8 w-8 shrink-0"
          disabled={!ready}
          aria-label={t('studio.common.listen')}
          onClick={() =>
            setAudio(
              apiClient.getAudioUrl(item.id),
              item.id,
              item.profile_id,
              item.text.slice(0, 60),
            )
          }
        >
          {ready || item.status === 'failed' ? (
            <Play className="h-3.5 w-3.5" />
          ) : (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          )}
        </Button>
        <button type="button" className="flex-1 min-w-0 text-left" onClick={() => setOpen(!open)}>
          <p className="text-sm line-clamp-2">{item.text}</p>
          <p className="text-xs text-muted-foreground mt-0.5 flex flex-wrap gap-x-2">
            <span>{item.profile_name}</span>
            {item.engine && (
              <span>{ENGINE_OPTIONS.find((o) => o.engine === item.engine)?.label ?? item.engine}</span>
            )}
            <span>{item.language}</span>
            {item.duration ? <span>{formatSeconds(item.duration)}</span> : null}
            <span>{date}</span>
            {item.status === 'failed' && (
              <span className="text-red-500">{t('studio.speak.failed')}</span>
            )}
            {!ready && item.status !== 'failed' && (
              <span className="text-amber-500">{t('studio.speak.working')}</span>
            )}
            {item.tags.map((tag) => (
              <span key={tag} className="text-accent">
                #{tag}
              </span>
            ))}
          </p>
        </button>
        <QualityBadge level={item.level} className="shrink-0 mt-0.5" />
        <div className="flex gap-0.5 shrink-0">
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            aria-label={t('studio.history.pin')}
            onClick={() =>
              studioApi
                .historyMeta(item.id, { pinned: !item.pinned })
                .then(refresh)
                .catch(handleError)
            }
          >
            <Pin className={cn('h-3.5 w-3.5', item.pinned && 'text-accent')} />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            aria-label={t('studio.history.favorite')}
            onClick={() => apiClient.toggleFavorite(item.id).then(refresh).catch(handleError)}
          >
            <Heart
              className={cn('h-3.5 w-3.5', item.is_favorited && 'fill-current text-red-500')}
            />
          </Button>
        </div>
      </div>
      {open && (
        <div className="mt-2 ml-[4.25rem] space-y-2">
          {item.status === 'failed' && <ErrorNote>{item.error}</ErrorNote>}
          {ready && <GenerationQuality id={item.id} />}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" className="h-7" onClick={reuse}>
              <Pencil className="h-3 w-3 mr-1" />
              {t('studio.history.reuse')}
            </Button>
            {ready && (
              <Button
                size="sm"
                variant="outline"
                className="h-7"
                onClick={() =>
                  apiClient.regenerateGeneration(item.id).then(refresh).catch(handleError)
                }
              >
                <RefreshCw className="h-3 w-3 mr-1" />
                {t('studio.history.regenerate')}
              </Button>
            )}
            <Button size="sm" variant="outline" className="h-7" onClick={editTags}>
              {t('studio.history.tags')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function HistoryTab() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const saveBlob = useSaveBlob();
  const { data: profiles } = useProfiles();
  const [filters, setFilters] = useState<HistoryFilters>({});
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [format, setFormat] = useState('mp3');

  const patch = (next: Partial<HistoryFilters>) => {
    setFilters((f) => ({ ...f, ...next }));
    setPage(0);
    setSelected(new Set());
  };
  const { data, isFetching } = useQuery({
    queryKey: ['historySearch', filters, page],
    queryFn: () => studioApi.searchHistory({ ...filters, limit: PAGE, offset: page * PAGE }),
    placeholderData: keepPreviousData,
    refetchInterval: (q) =>
      q.state.data?.items.some((i) => ['generating', 'loading_model'].includes(i.status))
        ? 2000
        : false,
  });
  const refresh = () => client.invalidateQueries({ queryKey: ['historySearch'] });

  const remove = useMutation({
    mutationFn: async () => {
      for (const id of selected) await apiClient.deleteGeneration(id);
    },
    onSuccess: () => {
      setSelected(new Set());
      refresh();
    },
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });
  const exportSelected = async () => {
    try {
      await saveBlob('lich-su.zip', await studioApi.historyExport([...selected], format), 'zip');
    } catch (e) {
      toast({ title: (e as Error).message, variant: 'destructive' });
    }
  };

  const pages = Math.ceil((data?.total ?? 0) / PAGE);

  return (
    <div className="h-full flex flex-col min-h-0">
      <PageHeader title={t('studio.history.title')} subtitle={t('studio.history.subtitle')} />
      <div className="space-y-2 shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-8"
            placeholder={t('studio.history.search')}
            value={filters.q ?? ''}
            onChange={(e) => patch({ q: e.target.value })}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <FilterSelect
            value={filters.profile_id}
            onChange={(profile_id) => patch({ profile_id })}
            all={t('studio.history.allVoices')}
            options={(profiles ?? []).map((p) => ({ value: p.id, label: p.name }))}
          />
          <FilterSelect
            value={filters.level}
            onChange={(level) => patch({ level })}
            all={t('studio.history.allQuality')}
            options={(['green', 'yellow', 'red'] as const).map((l) => ({
              value: l,
              label: t(`studio.quality.level.${l}`),
            }))}
          />
          {!!data?.tags.length && (
            <FilterSelect
              value={filters.tag}
              onChange={(tag) => patch({ tag })}
              all={t('studio.history.allTags')}
              options={data.tags.map((x) => ({ value: x, label: `#${x}` }))}
            />
          )}
          <Input
            type="date"
            className="h-8 w-auto text-xs"
            aria-label={t('studio.history.from')}
            value={filters.date_from ?? ''}
            onChange={(e) => patch({ date_from: e.target.value || undefined })}
          />
          <Input
            type="date"
            className="h-8 w-auto text-xs"
            aria-label={t('studio.history.to')}
            value={filters.date_to ?? ''}
            onChange={(e) => patch({ date_to: e.target.value || undefined })}
          />
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <Checkbox
              checked={!!filters.favorite}
              onCheckedChange={(v) => patch({ favorite: !!v })}
            />
            {t('studio.history.favorites')}
          </label>
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <Checkbox checked={!!filters.pinned} onCheckedChange={(v) => patch({ pinned: !!v })} />
            {t('studio.history.pinned')}
          </label>
          <span className="ml-auto text-xs text-muted-foreground flex items-center gap-1.5">
            {isFetching && <Loader2 className="h-3 w-3 animate-spin" />}
            {t('studio.history.count', { count: data?.total ?? 0 })}
          </span>
        </div>
        {selected.size > 0 && (
          <div className="flex items-center gap-2 rounded-md bg-muted/50 px-3 py-1.5 text-xs">
            <span>{t('studio.history.selected', { count: selected.size })}</span>
            <Select value={format} onValueChange={setFormat}>
              <SelectTrigger className="h-7 w-[80px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="mp3">MP3</SelectItem>
                <SelectItem value="wav">WAV</SelectItem>
                <SelectItem value="flac">FLAC</SelectItem>
              </SelectContent>
            </Select>
            <Button size="sm" variant="outline" className="h-7" onClick={exportSelected}>
              <Download className="h-3 w-3 mr-1" />
              {t('studio.common.export')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7"
              disabled={remove.isPending}
              onClick={() =>
                window.confirm(t('studio.history.confirmDelete', { count: selected.size })) &&
                remove.mutate()
              }
            >
              <Trash2 className="h-3 w-3 mr-1" />
              {t('studio.history.delete')}
            </Button>
          </div>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto mt-3 pb-44">
        <div className="rounded-lg border">
          {data?.items.map((item) => (
            <Row
              key={item.id}
              item={item}
              refresh={refresh}
              selected={selected.has(item.id)}
              onSelect={(value) =>
                setSelected((s) => {
                  const next = new Set(s);
                  if (value) next.add(item.id);
                  else next.delete(item.id);
                  return next;
                })
              }
            />
          ))}
          {!data?.items.length && (
            <p className="p-10 text-center text-sm text-muted-foreground">
              {t('studio.history.empty')}
            </p>
          )}
        </div>
        {pages > 1 && (
          <div className="flex items-center justify-center gap-3 mt-3 text-xs">
            <Button
              size="sm"
              variant="outline"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              {t('studio.history.prev')}
            </Button>
            <span className="tabular-nums">
              {page + 1} / {pages}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={page + 1 >= pages}
              onClick={() => setPage(page + 1)}
            >
              {t('studio.history.next')}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
