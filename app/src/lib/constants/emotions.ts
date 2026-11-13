/** Delivery emotions; keep the ids in sync with EMOTIONS in backend/utils/expressive.py. */
export const EMOTIONS = [
  { id: 'neutral', tag: 'bình thường' },
  { id: 'happy', tag: 'vui' },
  { id: 'sad', tag: 'buồn' },
  { id: 'angry', tag: 'giận' },
  { id: 'excited', tag: 'hào hứng' },
  { id: 'calm', tag: 'nhẹ nhàng' },
  { id: 'serious', tag: 'nghiêm túc' },
  { id: 'fearful', tag: 'sợ hãi' },
  { id: 'whisper', tag: 'thì thầm' },
  { id: 'storytelling', tag: 'kể chuyện' },
] as const;

export type EmotionId = (typeof EMOTIONS)[number]['id'];

export const EMOTION_IDS = EMOTIONS.map((e) => e.id) as [EmotionId, ...EmotionId[]];

/** How strongly an engine can act on the selected emotion. */
export type EmotionSupport = 'direct' | 'intensity' | 'reference' | 'prosody';

export function emotionSupport(engine: string | undefined): EmotionSupport {
  switch (engine) {
    case 'qwen_custom_voice':
    case 'qwen_voice_design':
      return 'direct';
    case 'chatterbox':
      return 'intensity';
    default:
      return 'reference';
  }
}
