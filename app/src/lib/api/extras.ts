/**
 * API client for the vi-upgrades features: extras settings (speed,
 * pronunciation dictionary, export, refine prompt, VRAM), VRAM status,
 * multi-format / SRT export, and batch generation.
 */
import { useServerStore } from '@/stores/serverStore';

export type ExportFormat = 'wav' | 'mp3' | 'flac';

export interface PronunciationRule {
  from: string;
  to: string;
  case_sensitive: boolean;
  whole_word: boolean;
  enabled: boolean;
}

export interface ExtrasSettings {
  speed: number;
  pronunciation_enabled: boolean;
  pronunciation: PronunciationRule[];
  loudness_target_lufs: number | null;
  export_format: ExportFormat;
  mp3_bitrate: number;
  refinement_custom_prompt: string;
  refinement_prompt_mode: 'append' | 'replace';
  vram_idle_unload_minutes: number;
  vram_exclusive_engine: boolean;
  qc_mode: 'off' | 'omnivoice' | 'all';
  qc_max_retries: number;
  qc_threshold: number;
  omnivoice_steps: number;
  queue_cooldown_seconds: number;
  sample_emotions: Record<string, string>;
}

export interface QualityReport {
  checked: number;
  retried: number;
  failed: { text: string; score: number; heard: string }[];
}

export interface VramStatus {
  cuda_available: boolean;
  device_name: string | null;
  total_mb: number | null;
  free_mb: number | null;
  used_mb: number | null;
  torch_allocated_mb: number | null;
  torch_reserved_mb: number | null;
  loaded_models: { model_name: string; display_name: string; engine: string; in_use: boolean }[];
  idle_seconds: number;
  idle_unload_minutes: number;
  exclusive_engine: boolean;
}

export interface BatchItemInput {
  text: string;
  filename: string;
}

export interface BatchCreateRequest {
  name?: string;
  profile_id: string;
  language: string;
  engine?: string | null;
  model_size?: string | null;
  instruct?: string | null;
  speed?: number | null;
  emotion?: string | null;
  emotion_intensity?: number;
  denoise?: number;
  sentence_pause_ms?: number;
  items: BatchItemInput[];
}

export type BatchItemStatus = 'generating' | 'loading_model' | 'completed' | 'failed' | 'missing';

export interface BatchDetail {
  id: string;
  name: string;
  created_at: string;
  settings: Record<string, unknown>;
  total: number;
  counts: Partial<Record<BatchItemStatus, number>>;
  items: {
    index: number;
    text: string;
    filename: string;
    generation_id: string | null;
    status: BatchItemStatus;
    duration: number | null;
    error: string | null;
  }[];
}

export type BatchSummary = Omit<BatchDetail, 'items'>;

function base(): string {
  return useServerStore.getState().serverUrl;
}

async function errorFrom(res: Response): Promise<Error> {
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
  return new Error(detail || `HTTP ${res.status}`);
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${base()}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

async function blob(path: string): Promise<Blob> {
  const res = await fetch(`${base()}${path}`);
  if (!res.ok) throw await errorFrom(res);
  return res.blob();
}

export const extrasApi = {
  getSettings: () => json<ExtrasSettings>('/extras/settings'),
  updateSettings: (patch: Partial<ExtrasSettings>) =>
    json<ExtrasSettings>('/extras/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  testPronunciation: (text: string) =>
    json<{ original: string; result: string }>('/extras/pronunciation/test', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  getVram: () => json<VramStatus>('/vram'),
  unloadVram: (includeAll = false) =>
    json<{ unloaded: string[] }>(`/vram/unload?include_all=${includeAll}`, { method: 'POST' }),

  exportFile: (generationId: string, format?: ExportFormat, bitrate?: number) => {
    const q = new URLSearchParams();
    if (format) q.set('format', format);
    if (bitrate) q.set('bitrate', String(bitrate));
    return blob(`/history/${generationId}/export-file?${q.toString()}`);
  },
  exportSrt: (generationId: string) => blob(`/history/${generationId}/export-srt`),
  getQuality: (generationId: string) =>
    json<{ qc: QualityReport | null }>(`/history/${generationId}/quality`),

  parseBatchFile: async (file: File, split: 'paragraph' | 'line') => {
    const form = new FormData();
    form.append('file', file);
    form.append('split', split);
    const res = await fetch(`${base()}/batch/parse`, { method: 'POST', body: form });
    if (!res.ok) throw await errorFrom(res);
    return (await res.json()) as { items: BatchItemInput[]; count: number };
  },
  createBatch: (data: BatchCreateRequest) =>
    json<BatchDetail>('/batch', { method: 'POST', body: JSON.stringify(data) }),
  listBatches: () => json<BatchSummary[]>('/batch'),
  getBatch: (id: string) => json<BatchDetail>(`/batch/${id}`),
  deleteBatch: (id: string) => json<{ deleted: boolean }>(`/batch/${id}`, { method: 'DELETE' }),
  downloadBatch: (
    id: string,
    opts: {
      mode: 'files' | 'merged';
      format: ExportFormat;
      bitrate: number;
      srt: boolean;
      gapMs: number;
    },
  ) => {
    const q = new URLSearchParams({
      mode: opts.mode,
      format: opts.format,
      bitrate: String(opts.bitrate),
      srt: String(opts.srt),
      gap_ms: String(opts.gapMs),
    });
    return blob(`/batch/${id}/download?${q.toString()}`);
  },
};
