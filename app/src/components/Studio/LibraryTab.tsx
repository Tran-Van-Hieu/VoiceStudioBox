import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { AudioLines, Mic, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ENGINE_OPTIONS } from '@/components/Generation/EngineModelSelector';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useToast } from '@/components/ui/use-toast';
import { ProfileForm } from '@/components/VoiceProfiles/ProfileForm';
import { SampleList } from '@/components/VoiceProfiles/SampleList';
import { apiClient } from '@/lib/api/client';
import type { VoiceProfileResponse } from '@/lib/api/types';
import { ALL_LANGUAGES } from '@/lib/constants/languages';
import { useProfiles } from '@/lib/hooks/useProfiles';
import { cn } from '@/lib/utils/cn';
import { useUIStore } from '@/stores/uiStore';
import { AuditionPanel } from './AuditionPanel';
import { DesignTakesPanel } from './DesignTakesPanel';
import { PageHeader, Panel } from './common';
import { PREFILL_KEY } from './SpeakTab';

const KINDS = ['all', 'cloned', 'preset', 'designed'] as const;

function languageName(code: string) {
  return ALL_LANGUAGES[code as keyof typeof ALL_LANGUAGES] ?? code;
}

export function LibraryTab() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const client = useQueryClient();
  const navigate = useNavigate();
  const { data: profiles } = useProfiles();
  const setDialogOpen = useUIStore((s) => s.setProfileDialogOpen);
  const setEditingId = useUIStore((s) => s.setEditingProfileId);
  const [kind, setKind] = useState<(typeof KINDS)[number]>('all');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (profiles ?? []).filter(
      (p) =>
        (p.voice_type as string) !== 'import' &&
        (kind === 'all' || (p.voice_type || 'cloned') === kind) &&
        (!needle || p.name.toLowerCase().includes(needle)),
    );
  }, [profiles, kind, search]);
  const selected: VoiceProfileResponse | null =
    visible.find((p) => p.id === selectedId) ?? visible[0] ?? null;

  const remove = useMutation({
    mutationFn: (id: string) => apiClient.deleteProfile(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['profiles'] }),
    onError: (e: Error) => toast({ title: e.message, variant: 'destructive' }),
  });

  const useVoice = (p: VoiceProfileResponse) => {
    sessionStorage.setItem(PREFILL_KEY, JSON.stringify({ profileId: p.id, language: p.language }));
    navigate({ to: '/' });
  };

  return (
    <div className="h-full flex flex-col min-h-0">
      <PageHeader
        title={t('studio.library.title')}
        subtitle={t('studio.library.subtitle')}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setEditingId(null);
                setDialogOpen(true);
              }}
            >
              <Plus className="h-3.5 w-3.5 mr-1.5" />
              {t('studio.library.addPreset')}
            </Button>
            <Button size="sm" onClick={() => navigate({ to: '/clone' })}>
              <Mic className="h-3.5 w-3.5 mr-1.5" />
              {t('studio.library.clone')}
            </Button>
          </>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)] flex-1 min-h-0">
        <div className="flex flex-col min-h-0 gap-3">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              className="pl-8"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('studio.library.search')}
            />
          </div>
          <div className="flex gap-1">
            {KINDS.filter(
              // Built-in voices came with a model that was removed; old ones still show.
              (k) => k !== 'preset' || profiles?.some((p) => p.voice_type === 'preset'),
            ).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                className={cn(
                  'rounded-md px-2.5 py-1 text-xs',
                  kind === k
                    ? 'bg-accent/15 text-foreground'
                    : 'text-muted-foreground hover:bg-muted/60',
                )}
              >
                {t(`studio.library.kind.${k}`)}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto divide-y rounded-lg border pb-1">
            {visible.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setSelectedId(p.id)}
                className={cn(
                  'w-full text-left px-3 py-2.5 hover:bg-muted/40',
                  selected?.id === p.id && 'bg-accent/10',
                )}
              >
                <p className="text-sm font-medium truncate">{p.name}</p>
                <p className="text-xs text-muted-foreground truncate">
                  {t(`studio.library.kind.${p.voice_type || 'cloned'}`)} ·{' '}
                  {languageName(p.language)}
                  {p.default_engine
                    ? ` · ${ENGINE_OPTIONS.find((o) => o.engine === p.default_engine)?.label ?? p.default_engine}`
                    : ''}
                </p>
              </button>
            ))}
            {!visible.length && (
              <p className="p-6 text-center text-sm text-muted-foreground">
                {t('studio.library.empty')}
              </p>
            )}
          </div>
        </div>
        <div className="overflow-y-auto pb-44 space-y-4 min-w-0">
          {selected && (
            <>
              <Panel>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="font-semibold truncate">{selected.name}</h2>
                    <p className="text-xs text-muted-foreground">
                      {languageName(selected.language)} ·{' '}
                      {t('studio.library.generated', { count: selected.generation_count })}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => useVoice(selected)}>
                      <AudioLines className="h-3.5 w-3.5 mr-1.5" />
                      {t('studio.library.use')}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setEditingId(selected.id);
                        setDialogOpen(true);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5 mr-1.5" />
                      {t('studio.library.edit')}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={t('studio.library.delete')}
                      onClick={() => {
                        if (
                          window.confirm(t('studio.library.confirmDelete', { name: selected.name }))
                        )
                          remove.mutate(selected.id);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </Panel>
              {(selected.voice_type || 'cloned') === 'cloned' && (
                <>
                  <Panel title={t('studio.library.samples')}>
                    <SampleList profileId={selected.id} />
                  </Panel>
                  <AuditionPanel profileId={selected.id} language={selected.language} />
                </>
              )}
              {selected.voice_type === 'designed' && (
                <DesignTakesPanel
                  key={selected.id}
                  profileId={selected.id}
                  language={selected.language}
                />
              )}
            </>
          )}
        </div>
      </div>
      <ProfileForm />
    </div>
  );
}
