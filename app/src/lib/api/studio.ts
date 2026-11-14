/** API client for the redesigned workspaces. */
import { useServerStore } from '@/stores/serverStore';
import type { EffectConfig } from './types';

export function serverUrl(path: string): string {
  return `${useServerStore.getState().serverUrl}${path}`;
}

async function fail(res: Response): Promise<Error> {
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  return new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail));
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(serverUrl(path), {
    ...init,
    headers: init?.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw await fail(res);
  return res.json();
}

const post = <T>(path: string, body?: unknown) =>
  json<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
const put = <T>(path: string, body: unknown) =>
  json<T>(path, { method: 'PUT', body: JSON.stringify(body) });

export async function fetchBlob(path: string, init?: RequestInit): Promise<Blob> {
  const res = await fetch(serverUrl(path), init);
  if (!res.ok) throw await fail(res);
  return res.blob();
}

// ── Status ─────────────────────────────────────────────────────────────────

export interface SystemStatus {
  cuda_available: boolean;
  total_mb: number | null;
  used_mb: number | null;
  ram_total_mb?: number;
  ram_used_mb?: number;
  app_ram_mb?: number;
  queue_length?: number;
  release_pending?: boolean;
  job?: {
    running: string | null;
    checking: boolean;
    stopping: boolean;
    queued: number;
    cooldown_seconds?: number;
  };
  loaded_models: { model_name: string; display_name: string; engine: string; in_use: boolean }[];
}

// ── Quality ────────────────────────────────────────────────────────────────

export type QualityLevel = 'green' | 'yellow' | 'red';

/** One word-level difference between the text and what Whisper heard. */
export interface WordDiffOp {
  op: 'missing' | 'extra' | 'wrong';
  expected: string;
  heard: string;
  at: number;
}

export interface QualityIssue {
  kind: 'words' | 'voice' | 'pause' | 'clipping' | 'clicks' | 'noise' | 'chunks';
  severity: 'yellow' | 'red';
  start?: number;
  end?: number;
  value?: number;
  text?: string;
  heard?: string;
  segments?: number[];
  diff?: WordDiffOp[];
}

export interface FinalCheck {
  level: QualityLevel;
  score: number | null;
  speaker: number | null;
  metrics: {
    peak: number;
    clip_ratio: number;
    noise_floor_db: number;
    clicks_per_min: number;
    lufs: number | null;
  };
  issues: QualityIssue[];
  match?: 'exact' | 'tolerant';
}

export interface QualityResponse {
  qc: {
    checked: number;
    retried: number;
    failed: { text: string; score: number; heard: string; problem?: string }[];
    lowest_similarity?: number | null;
  } | null;
  final: FinalCheck | null;
}

// ── Voice lab ──────────────────────────────────────────────────────────────

export interface ClipAnalysis {
  duration: number;
  score: number;
  issues: string[];
  snr_db: number;
  lufs: number | null;
  clip_ratio: number;
  speaker_consistency: number | null;
}

export interface PrepareResult {
  window: [number, number];
  raw: ClipAnalysis;
  clean: ClipAnalysis;
  transcript: string;
  auto_transcript: string;
  language: string | null;
}

export interface PreparedSource {
  steps: string[];
  music: boolean;
  source_seconds: number;
  peaks: number[];
}

export interface PrepareJob {
  id: string;
  stage: 'queued' | 'analyzing' | 'ready' | 'transcribing' | 'done' | 'failed';
  source: PreparedSource | null;
  result: PrepareResult | null;
  error: string | null;
}

export interface AuditionOption {
  key: string;
  engine: string;
  model_size: string;
  name: string;
  unavailable: 'language' | 'not_downloaded' | null;
}

export interface AuditionResult {
  similarity: number;
  text_score: number;
  seed: number;
  tested_at: number;
  language: string;
  name: string;
}

export interface AuditionJob {
  id: string;
  stage: 'running' | 'done';
  total: number;
  current: string | null;
  results: Record<string, AuditionResult>;
  errors: Record<string, string>;
}

// ── Studio ─────────────────────────────────────────────────────────────────

export interface StudioSegment {
  id: string;
  text: string;
  speaker: string | null;
  profile_id: string | null;
  engine: string | null;
  model_size: string | null;
  language: string | null;
  emotion: string | null;
  emotion_intensity: number;
  speed: number | null;
  seed: number | null;
  pause_after_ms: number;
  filename: string;
  takes: string[];
  active: string | null;
  status?: string;
  duration?: number | null;
  error?: string | null;
  quality?: QualityLevel | null;
  final?: FinalCheck | null;
}

export interface StudioDefaults {
  profile_id?: string;
  engine?: string;
  model_size?: string;
  speed?: number;
  denoise?: number;
  sentence_pause_ms?: number;
  effects_chain?: EffectConfig[] | null;
  emotion?: string | null;
  emotion_intensity?: number;
  /** The picker choice behind effects_chain: 'voice', 'none' or a preset id. */
  effects?: string;
}

export interface StudioProject {
  id: string;
  name: string;
  language: string;
  defaults: StudioDefaults;
  segments: StudioSegment[];
  music: { name: string; speech_pct: number; pause_pct: number } | null;
  quality?: QualityLevel;
  pending?: boolean;
}

export interface StudioSummary {
  id: string;
  name: string;
  updated_at: number | null;
  segments: number;
}

// ── Dubbing ────────────────────────────────────────────────────────────────

export interface DubSegment {
  id: string;
  start: number;
  end: number;
  speaker: string;
  text: string;
  translation: string;
  status: string;
  fit: number | null;
  overflow: boolean;
  warning?: 'empty' | 'untranslated' | 'numbers' | null;
  score?: number;
  error?: string;
}

export interface DubSubtitle {
  burn: boolean;
  which: 'target' | 'source';
  font: string;
  size: number;
  color: string;
  outline_color: string;
  outline: number;
  bold: boolean;
  position: 'bottom' | 'middle' | 'top';
  margin: number;
}

/** Settings that change how a dub sounds and looks, editable before and after it runs. */
export interface DubOutput {
  original_volume: number;
  music_volume: number;
  subtitle: DubSubtitle;
}

export interface DubSpeaker {
  label: string;
  /** 'clone' only in dubs made before original-voice cloning was removed. */
  mode: 'clone' | 'profile';
  profile_id: string | null;
}

export interface DubProblem {
  id?: string;
  kind: string;
  severity: 'yellow' | 'red';
  value?: number;
  heard?: string;
}

export interface DubProject {
  id: string;
  name: string;
  stage: string;
  progress: number;
  error: string | null;
  target_language: string;
  source_language: string | null;
  engine: string;
  model_size: string;
  mode: 'dub' | 'transcribe';
  has_video?: boolean;
  glossary?: string;
  segments: DubSegment[];
  speakers: Record<string, DubSpeaker>;
  qc: { level: QualityLevel; problems: DubProblem[] } | null;
  original_volume?: number;
  music_volume?: number;
  subtitle?: DubSubtitle;
  srt_role?: 'source' | 'target';
}

export type DubSummary = Pick<
  DubProject,
  'id' | 'name' | 'stage' | 'progress' | 'error' | 'target_language' | 'mode'
>;

// ── History ────────────────────────────────────────────────────────────────

export interface HistoryItem {
  id: string;
  text: string;
  profile_id: string;
  profile_name: string;
  engine: string | null;
  model_size: string | null;
  language: string;
  duration: number | null;
  status: string;
  error: string | null;
  created_at: string | null;
  is_favorited: boolean;
  seed: number | null;
  pinned: boolean;
  tags: string[];
  level: QualityLevel | null;
  final: FinalCheck | null;
}

export interface HistoryFilters {
  q?: string;
  profile_id?: string;
  engine?: string;
  language?: string;
  date_from?: string;
  date_to?: string;
  favorite?: boolean;
  pinned?: boolean;
  level?: string;
  tag?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

// ── Model updates ──────────────────────────────────────────────────────────

export interface ModelVersionRow {
  commit: string;
  present: boolean;
  active: boolean;
  original: boolean;
  status: string;
  note: string | null;
  installed_at: number | null;
}

export interface ModelUpdateInfo {
  available: boolean;
  latest?: string;
  date?: string | null;
  title?: string | null;
  changed_files?: string[];
  download_bytes?: number;
  license?: string | null;
  license_before?: string | null;
  error?: string;
}

/** Takes of a designed voice's fixed clip; every generation is cloned from the chosen one. */
export interface DesignTakeGroup {
  id: string;
  language: string;
  emotion: string;
  instruct: string;
  text: string;
  takes: { seed: number; words: number; mos: number | null; seconds: number }[];
  chosen: number;
  picked_by: 'auto' | 'user';
  created_at: number;
}

export interface ManagedModel {
  repo: string;
  names: string[];
  active: string;
  original: string;
  versions: ModelVersionRow[];
  update: ModelUpdateInfo | null;
  checked_at: number | null;
  job: { stage: string; message: string; commit: string } | null;
}

function query(params: object): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '' && v !== false) q.set(k, String(v));
  }
  return q.toString();
}

export const studioApi = {
  status: () => json<SystemStatus>('/vram'),
  release: () => post<{ unloaded: string[]; deferred: boolean }>('/vram/release'),
  stopJobs: () => post<{ stopped: number; cleared: number }>('/jobs/stop'),
  clearQueue: () => post<{ cleared: number }>('/jobs/clear-queue'),
  skipCheck: () => post<{ skipping: boolean }>('/jobs/skip-check'),
  textPreview: (text: string, language: string) =>
    post<{ result: string }>('/extras/text-preview', { text, language }),

  quality: (id: string) => json<QualityResponse>(`/history/${id}/quality`),
  repair: (id: string, index: number) =>
    post<{ final: FinalCheck; kept_previous?: boolean }>(`/quality/${id}/repair/${index}`),

  prepare: (form: FormData) =>
    json<PrepareJob>('/voicelab/prepare', { method: 'POST', body: form }),
  labJob: <T>(id: string) => json<T>(`/voicelab/jobs/${id}`),
  selectClip: (
    id: string,
    body: { start: number; end: number; language?: string; reference_text: string },
  ) => post<PrepareJob>(`/voicelab/jobs/${id}/select`, body),
  labClipUrl: (id: string, which: 'raw' | 'clean' | 'full_clean') =>
    serverUrl(`/voicelab/jobs/${id}/audio/${which}`),
  createVoice: (id: string, body: unknown) =>
    post<{ profile_id: string; sample_id: string }>(`/voicelab/jobs/${id}/create-voice`, body),
  auditionOptions: (language: string) =>
    json<AuditionOption[]>(`/voicelab/models?language=${language}`),
  startAudition: (profile_id: string, language: string, keys?: string[]) =>
    post<AuditionJob>('/voicelab/audition', { profile_id, language, keys }),
  voiceModels: (profileId: string) =>
    json<Record<string, AuditionResult>>(`/voicelab/profiles/${profileId}/models`),
  auditionUrl: (profileId: string, key: string) =>
    serverUrl(`/voicelab/profiles/${profileId}/models/${encodeURIComponent(key)}/audio`),

  designTakes: (profileId: string) =>
    json<DesignTakeGroup[]>(`/profiles/${profileId}/design-takes`),
  createDesignTakes: (profileId: string, language: string) => {
    const form = new FormData();
    form.append('language', language);
    return json<DesignTakeGroup>(`/profiles/${profileId}/design-takes`, {
      method: 'POST',
      body: form,
    });
  },
  redoDesignTakes: (profileId: string, groupId: string) =>
    json<DesignTakeGroup>(`/profiles/${profileId}/design-takes/${groupId}/redo`, {
      method: 'POST',
    }),
  chooseDesignTake: (profileId: string, groupId: string, chosen: number) => {
    const form = new FormData();
    form.append('chosen', String(chosen));
    return json<DesignTakeGroup>(`/profiles/${profileId}/design-takes/${groupId}`, {
      method: 'PUT',
      body: form,
    });
  },
  designTakeUrl: (profileId: string, groupId: string, index: number) =>
    serverUrl(`/profiles/${profileId}/design-takes/${groupId}/${index}`),

  listStudio: () => json<StudioSummary[]>('/studio'),
  createStudio: (body: { name: string; language: string; defaults: StudioDefaults }) =>
    post<StudioProject>('/studio', body),
  getStudio: (id: string) => json<StudioProject>(`/studio/${id}`),
  updateStudio: (
    id: string,
    body: Partial<StudioProject> & { music_speech_pct?: number; music_pause_pct?: number },
  ) => put<StudioProject>(`/studio/${id}`, body),
  splitStudio: (id: string, text: string, max_chars: number) =>
    post<StudioProject>(`/studio/${id}/split`, { text, max_chars }),
  stopStudio: (id: string) => post<{ stopped: number }>(`/studio/${id}/stop`),
  deleteStudio: (id: string) => json<unknown>(`/studio/${id}`, { method: 'DELETE' }),
  importStudio: (id: string, form: FormData) =>
    json<StudioProject>(`/studio/${id}/import`, { method: 'POST', body: form }),
  renderStudio: (id: string, segment_ids?: string[]) =>
    post<{ started: number; skipped: string[] }>(`/studio/${id}/render`, { segment_ids }),
  setMusic: (id: string, form: FormData) =>
    json<StudioProject>(`/studio/${id}/music`, { method: 'POST', body: form }),
  removeMusic: (id: string) => json<StudioProject>(`/studio/${id}/music`, { method: 'DELETE' }),
  exportStudio: (id: string, mode: string, format: string, srt: boolean) =>
    fetchBlob(`/studio/${id}/export?${query({ mode, format, srt: srt ? 'true' : 'false' })}`),

  listDubs: () => json<DubSummary[]>('/dubbing'),
  createDubs: (form: FormData) => json<DubProject[]>('/dubbing', { method: 'POST', body: form }),
  getDub: (id: string) => json<DubProject>(`/dubbing/${id}`),
  updateDub: (id: string, patch: unknown) => put<DubProject>(`/dubbing/${id}`, patch),
  runDub: (id: string) => post<DubProject>(`/dubbing/${id}/run`),
  stopDub: (id: string) => post<unknown>(`/dubbing/${id}/stop`),
  retranslateDub: (id: string) => post<DubProject>(`/dubbing/${id}/retranslate`),
  deleteDub: (id: string) => json<unknown>(`/dubbing/${id}`, { method: 'DELETE' }),
  dubFileUrl: (id: string, which: string) => serverUrl(`/dubbing/${id}/file/${which}`),
  dubFile: (id: string, which: string) => fetchBlob(`/dubbing/${id}/file/${which}`),
  dubSubtitles: (id: string, which: string, fmt: string) =>
    fetchBlob(`/dubbing/${id}/subtitles?${query({ which, fmt })}`),

  // Dates are picked in local time but stored in UTC: send the exact instants.
  searchHistory: ({ date_from, date_to, ...filters }: HistoryFilters) =>
    json<{ items: HistoryItem[]; total: number; tags: string[] }>(
      `/library/history?${query({
        ...filters,
        date_from: date_from && new Date(`${date_from}T00:00:00`).toISOString(),
        date_to: date_to && new Date(`${date_to}T23:59:59.999`).toISOString(),
      })}`,
    ),
  historyMeta: (id: string, meta: { pinned?: boolean; tags?: string[] }) =>
    put<unknown>(`/library/history/${id}/meta`, meta),
  historyExport: (ids: string[], format: string) =>
    fetchBlob('/library/history/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, format }),
    }),

  models: () => json<ManagedModel[]>('/model-updates'),
  checkModels: () => post<ManagedModel[]>('/model-updates/check'),
  installModel: (repo: string, commit: string) =>
    post<unknown>('/model-updates/install', { repo, commit }),
  activateModel: (repo: string, commit: string) =>
    post<ManagedModel[]>('/model-updates/activate', { repo, commit }),
  deleteModelVersion: (repo: string, commit: string) =>
    post<ManagedModel[]>('/model-updates/delete', { repo, commit }),
};
