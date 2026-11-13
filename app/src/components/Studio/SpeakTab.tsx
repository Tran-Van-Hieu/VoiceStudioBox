import { useMutation, useQuery } from '@tanstack/react-query';
import { Download, Eye, FileUp, Loader2, Play } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BatchTab } from '@/components/BatchTab/BatchTab';
import { ScriptTab } from './ScriptTab';
import { StudioTab } from './StudioTab';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import { extrasApi, type ExportFormat } from '@/lib/api/extras';
import { studioApi } from '@/lib/api/studio';
import type { GenerationRequest } from '@/lib/api/types';
import { EMOTIONS, type EmotionId, emotionSupport } from '@/lib/constants/emotions';
import { useProfiles } from '@/lib/hooks/useProfiles';
import { usePlayerStore } from '@/stores/playerStore';
import {
  engineSupports,
  ErrorNote,
  Field,
  GenerationQuality,
  LanguagePicker,
  ModelPicker,
  PageHeader,
  Panel,
  parseEngine,
  SliderField,
  useSaveBlob,
  VoicePicker,
} from './common';

export const PREFILL_KEY = 'voicebox.prefill';

export function SpeakTab() {
  const { t } = useTranslation();
  return (
    <div className="h-full flex flex-col min-h-0">
      <PageHeader title={t('studio.speak.title')} subtitle={t('studio.speak.subtitle')} />
      <Tabs defaultValue="single" className="flex-1 min-h-0 flex flex-col">
        <TabsList className="self-start">
          <TabsTrigger value="single">{t('studio.speak.single')}</TabsTrigger>
          <TabsTrigger value="script">{t('studio.script.tab')}</TabsTrigger>
          <TabsTrigger value="batch">{t('studio.speak.batch')}</TabsTrigger>
          <TabsTrigger value="multi">{t('studio.speak.multi')}</TabsTrigger>
        </TabsList>
        <TabsContent value="single" className="flex-1 min-h-0 overflow-y-auto">
          <SingleSpeak />
        </TabsContent>
        <TabsContent value="script" className="flex-1 min-h-0 overflow-y-auto">
          <ScriptTab />
        </TabsContent>
        <TabsContent value="batch" className="flex-1 min-h-0 overflow-hidden">
          <BatchTab />
        </TabsContent>
        <TabsContent value="multi" className="flex-1 min-h-0 overflow-hidden">
          <StudioTab embedded />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function SingleSpeak() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const saveBlob = useSaveBlob();
  const setAudio = usePlayerStore((s) => s.setAudioWithAutoPlay);
  const { data: profiles } = useProfiles();
  const fileRef = useRef<HTMLInputElement>(null);

  const [text, setText] = useState('');
  const [profileId, setProfileId] = useState<string | null>(null);
  const profile = useMemo(
    () => profiles?.find((p) => p.id === profileId) ?? null,
    [profiles, profileId],
  );
  const [language, setLanguage] = useState('vi');
  const [model, setModel] = useState<string | null>('omnivoice');
  const [emotion, setEmotion] = useState<EmotionId>('neutral');
  const [intensity, setIntensity] = useState(0.6);
  const [speed, setSpeed] = useState(1);
  const [denoise, setDenoise] = useState(0);
  const [pause, setPause] = useState(250);
  const [preview, setPreview] = useState<string | null>(null);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [fmt, setFmt] = useState<ExportFormat>('mp3');

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(PREFILL_KEY);
      if (!raw) return;
      sessionStorage.removeItem(PREFILL_KEY);
      const prefill = JSON.parse(raw);
      if (prefill.text) setText(prefill.text);
      if (prefill.profileId) setProfileId(prefill.profileId);
      if (prefill.language) setLanguage(prefill.language);
    } catch {
      // A bad prefill only means an empty form.
    }
  }, []);

  useEffect(() => {
    if (!profileId && profiles?.length) {
      const first = profiles.find((p) => (p.voice_type as string) !== 'import');
      if (first) setProfileId(first.id);
    }
  }, [profiles, profileId]);

  useEffect(() => {
    if (!profile) return;
    setLanguage(profile.language);
    const engine = profile.default_engine ?? profile.preset_engine;
    if (engine)
      setModel(engine === 'qwen' || engine === 'qwen_custom_voice' ? `${engine}:1.7B` : engine);
  }, [profile]);

  const { engine, modelSize } = parseEngine(model ?? '');
  const languageOk = !!model && engineSupports(engine, language);
  const { data: voiceModels } = useQuery({
    queryKey: ['voiceModels', profileId],
    queryFn: () => studioApi.voiceModels(profileId as string),
    enabled: !!profileId,
  });
  const bestSeed = voiceModels?.[`${engine}:${modelSize ?? 'default'}`]?.seed;

  const { data: generation } = useQuery({
    queryKey: ['generation', currentId],
    queryFn: () => apiClient.getGeneration(currentId as string),
    enabled: !!currentId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === 'completed' || s === 'failed' ? false : 1000;
    },
  });

  const generate = useMutation({
    mutationFn: () =>
      apiClient.generateSpeech({
        profile_id: profileId as string,
        text,
        language,
        engine: engine as GenerationRequest['engine'],
        model_size: (modelSize ?? undefined) as GenerationRequest['model_size'],
        seed: bestSeed,
        speed,
        emotion: emotion === 'neutral' ? undefined : emotion,
        emotion_intensity: intensity,
        denoise: denoise > 0 ? denoise : undefined,
        sentence_pause_ms: pause,
      } as GenerationRequest),
    onSuccess: (g) => setCurrentId(g.id),
    onError: (e: Error) =>
      toast({ title: t('studio.speak.failed'), description: e.message, variant: 'destructive' }),
  });

  const showPreview = async () => {
    try {
      setPreview((await studioApi.textPreview(text, language)).result);
    } catch (e) {
      toast({ title: (e as Error).message, variant: 'destructive' });
    }
  };

  const download = async (kind: ExportFormat | 'srt') => {
    if (!generation) return;
    const blob =
      kind === 'srt'
        ? await extrasApi.exportSrt(generation.id)
        : await extrasApi.exportFile(generation.id, kind);
    await saveBlob(`vsbox-${generation.id.slice(0, 8)}.${kind}`, blob, kind);
  };

  const busy =
    generate.isPending ||
    (!!generation && !['completed', 'failed'].includes(generation.status ?? 'completed'));

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px] pb-44 pt-2">
      <div className="space-y-4 min-w-0">
        <Panel>
          <div className="flex items-center justify-between gap-2">
            <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
              <FileUp className="h-3.5 w-3.5 mr-1.5" />
              {t('studio.speak.openFile')}
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept=".txt,text/plain"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) setText(await file.text());
                e.target.value = '';
              }}
            />
            <span className="text-xs text-muted-foreground tabular-nums">
              {t('studio.speak.chars', { count: text.length })}
            </span>
          </div>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t('studio.speak.placeholder')}
            className="min-h-[300px] resize-y text-[15px] leading-relaxed"
          />
          <p className="text-xs text-muted-foreground">{t('studio.speak.tagsHint')}</p>
          <div className="flex items-center justify-between gap-2">
            <Button variant="outline" size="sm" disabled={!text.trim()} onClick={showPreview}>
              <Eye className="h-3.5 w-3.5 mr-1.5" />
              {t('studio.speak.preview')}
            </Button>
            <Button
              disabled={busy || !text.trim() || !profileId || !languageOk}
              onClick={() => generate.mutate()}
            >
              {busy ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Play className="h-4 w-4 mr-2" />
              )}
              {t('studio.speak.generate')}
            </Button>
          </div>
          {preview && (
            <p className="rounded-md bg-muted/50 p-3 text-sm whitespace-pre-wrap">{preview}</p>
          )}
        </Panel>

        {generation && (
          <Panel title={t('studio.speak.result')}>
            {generation.status === 'failed' ? (
              <ErrorNote>{generation.error}</ErrorNote>
            ) : generation.status !== 'completed' ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t('studio.speak.working')}
              </p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() =>
                      setAudio(
                        apiClient.getAudioUrl(generation.id),
                        generation.id,
                        generation.profile_id,
                        text.slice(0, 60),
                      )
                    }
                  >
                    <Play className="h-3.5 w-3.5 mr-1.5" />
                    {t('studio.common.listen')}
                  </Button>
                  <Select value={fmt} onValueChange={(v) => setFmt(v as ExportFormat)}>
                    <SelectTrigger className="w-[90px] h-8">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="mp3">MP3</SelectItem>
                      <SelectItem value="wav">WAV</SelectItem>
                      <SelectItem value="flac">FLAC</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button size="sm" variant="outline" onClick={() => download(fmt)}>
                    <Download className="h-3.5 w-3.5 mr-1.5" />
                    {t('studio.common.export')}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => download('srt')}>
                    SRT
                  </Button>
                </div>
                <GenerationQuality id={generation.id} refreshKey={generation.audio_path} />
              </>
            )}
          </Panel>
        )}
      </div>

      <div className="space-y-4">
        <Panel title={t('studio.speak.voice')}>
          <Field label={t('studio.common.voice')}>
            <VoicePicker value={profileId} onChange={setProfileId} />
          </Field>
          <Field label={t('studio.common.language')}>
            <LanguagePicker value={language} onChange={setLanguage} />
          </Field>
          <Field label={t('studio.common.model')}>
            <ModelPicker value={model} onChange={setModel} language={language} profile={profile} />
          </Field>
          {!languageOk && <ErrorNote>{t('studio.speak.modelLanguage')}</ErrorNote>}
          {bestSeed !== undefined && (
            <p className="text-xs text-muted-foreground">{t('studio.speak.bestSeed')}</p>
          )}
        </Panel>
        <Panel title={t('studio.speak.delivery')}>
          <Field label={t('emotion.label')} hint={t(`emotion.support.${emotionSupport(engine)}`)}>
            <Select value={emotion} onValueChange={(v) => setEmotion(v as EmotionId)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EMOTIONS.map((e) => (
                  <SelectItem key={e.id} value={e.id}>
                    {t(`emotion.names.${e.id}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {emotion !== 'neutral' && (
            <SliderField
              label={t('emotion.intensity')}
              value={intensity}
              min={0}
              max={1}
              step={0.05}
              format={(v) => `${Math.round(v * 100)}%`}
              onChange={setIntensity}
            />
          )}
          <SliderField
            label={t('studio.common.speed')}
            value={speed}
            min={0.5}
            max={2}
            step={0.05}
            format={(v) => `${v.toFixed(2)}×`}
            onChange={setSpeed}
          />
          <SliderField
            label={t('studio.common.denoise')}
            value={denoise}
            min={0}
            max={1}
            step={0.1}
            format={(v) => (v ? `${Math.round(v * 100)}%` : t('studio.common.off'))}
            onChange={setDenoise}
          />
          <SliderField
            label={t('studio.speak.pause')}
            value={pause}
            min={0}
            max={1000}
            step={50}
            format={(v) => `${v} ms`}
            onChange={setPause}
          />
        </Panel>
      </div>
    </div>
  );
}
