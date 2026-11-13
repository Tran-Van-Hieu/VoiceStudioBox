import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type ExtrasSettings, extrasApi } from '@/lib/api/extras';

export const EXTRAS_SETTINGS_KEY = ['extras', 'settings'] as const;

/** Extras settings with optimistic partial updates (same pattern as useGenerationSettings). */
export function useExtrasSettings() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: EXTRAS_SETTINGS_KEY,
    queryFn: () => extrasApi.getSettings(),
    staleTime: Infinity,
  });

  const mutation = useMutation({
    mutationFn: (patch: Partial<ExtrasSettings>) => extrasApi.updateSettings(patch),
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: EXTRAS_SETTINGS_KEY });
      const previous = queryClient.getQueryData<ExtrasSettings>(EXTRAS_SETTINGS_KEY);
      if (previous) {
        queryClient.setQueryData<ExtrasSettings>(EXTRAS_SETTINGS_KEY, { ...previous, ...patch });
      }
      return { previous };
    },
    onError: (_err, _patch, ctx) => {
      if (ctx?.previous) queryClient.setQueryData(EXTRAS_SETTINGS_KEY, ctx.previous);
    },
    onSettled: (data) => {
      if (data) queryClient.setQueryData(EXTRAS_SETTINGS_KEY, data);
      queryClient.invalidateQueries({ queryKey: ['vram'] });
    },
  });

  return {
    settings: query.data,
    isLoading: query.isLoading,
    update: mutation.mutate,
    updateAsync: mutation.mutateAsync,
    isSaving: mutation.isPending,
  };
}

/** Live VRAM status, polled while the page using it is mounted. */
export function useVramStatus(intervalMs = 3000) {
  return useQuery({
    queryKey: ['vram'],
    queryFn: () => extrasApi.getVram(),
    refetchInterval: intervalMs,
  });
}

// Characters Windows forbids in file names, plus ASCII control characters.
const UNSAFE_FILENAME_CHARS = new RegExp('[<>:"/\\\\|?*\\x00-\\x1f]+', 'g');

/** Turn arbitrary text into a safe file name, keeping Vietnamese letters. */
export function sanitizeFilename(text: string, fallback = 'audio'): string {
  const cleaned = text
    .normalize('NFC')
    .replace(UNSAFE_FILENAME_CHARS, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 40)
    .trim();
  return cleaned || fallback;
}
