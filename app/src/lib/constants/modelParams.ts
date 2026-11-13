/** Sampling knobs each TTS engine accepts. Mirrors MODEL_PARAMS in backend/services/extras.py. */
export interface ParamSpec {
  key: string;
  kind: 'float' | 'int' | 'bool';
  def: number | boolean;
  min?: number;
  max?: number;
  step?: number;
}

const QWEN: ParamSpec[] = [
  { key: 'do_sample', kind: 'bool', def: true },
  { key: 'temperature', kind: 'float', def: 0.9, min: 0.1, max: 2, step: 0.05 },
  { key: 'top_k', kind: 'int', def: 50, min: 1, max: 200, step: 1 },
  { key: 'top_p', kind: 'float', def: 1, min: 0.1, max: 1, step: 0.05 },
  { key: 'repetition_penalty', kind: 'float', def: 1.05, min: 1, max: 2, step: 0.01 },
  { key: 'subtalker_temperature', kind: 'float', def: 0.9, min: 0.1, max: 2, step: 0.05 },
  { key: 'subtalker_top_k', kind: 'int', def: 50, min: 1, max: 200, step: 1 },
  { key: 'subtalker_top_p', kind: 'float', def: 1, min: 0.1, max: 1, step: 0.05 },
  { key: 'max_new_tokens', kind: 'int', def: 2048, min: 256, max: 8192, step: 256 },
];

export const MODEL_PARAMS: Record<string, ParamSpec[]> = {
  omnivoice: [
    { key: 'guidance_scale', kind: 'float', def: 2, min: 0, max: 5, step: 0.1 },
    { key: 't_shift', kind: 'float', def: 0.1, min: 0.01, max: 1, step: 0.01 },
    { key: 'position_temperature', kind: 'float', def: 5, min: 0, max: 10, step: 0.5 },
    { key: 'class_temperature', kind: 'float', def: 0, min: 0, max: 2, step: 0.05 },
    { key: 'layer_penalty_factor', kind: 'float', def: 5, min: 0, max: 10, step: 0.5 },
    { key: 'denoise', kind: 'bool', def: true },
    { key: 'postprocess_output', kind: 'bool', def: true },
  ],
  qwen: QWEN,
  qwen_voice_design: QWEN,
};
