import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import { extrasApi } from '@/lib/api/extras';
import type { GenerationExportFormat, HistoryQuery } from '@/lib/api/types';
import { sanitizeFilename } from '@/lib/hooks/useExtras';
import { usePlatform } from '@/platform/PlatformContext';

/** Download .srt subtitles timed to a generation's audio. */
export function useExportGenerationSrt() {
  const platform = usePlatform();

  return useMutation({
    mutationFn: async ({ generationId, text }: { generationId: string; text: string }) => {
      const blob = await extrasApi.exportSrt(generationId);
      const filename = `${sanitizeFilename(text, 'subtitles')}-${generationId.substring(0, 8)}.srt`;
      await platform.filesystem.saveFile(filename, blob, [
        { name: 'SubRip Subtitles', extensions: ['srt'] },
      ]);
      return blob;
    },
  });
}

export function useHistory(query?: HistoryQuery) {
  return useQuery({
    queryKey: ['history', query],
    queryFn: () => apiClient.listHistory(query),
  });
}

export function useGenerationDetail(generationId: string) {
  return useQuery({
    queryKey: ['history', generationId],
    queryFn: () => apiClient.getGeneration(generationId),
    enabled: !!generationId,
  });
}

export function useDeleteGeneration() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (generationId: string) => apiClient.deleteGeneration(generationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['history'] });
    },
  });
}

export function useClearFailedGenerations() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => apiClient.clearFailedGenerations(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['history'] });
    },
  });
}

export function useExportGeneration() {
  const platform = usePlatform();

  return useMutation({
    mutationFn: async ({ generationId, text }: { generationId: string; text: string }) => {
      const blob = await apiClient.exportGeneration(generationId);

      // Create safe filename from text. Append a short id so exports of
      // similarly-worded generations don't collide on the same filename
      // (the first 30 chars are frequently identical).
      const safeText = text
        .substring(0, 30)
        .replace(/[^a-z0-9]/gi, '-')
        .toLowerCase();
      const filename = `generation-${safeText}-${generationId.substring(0, 8)}.vsbox.zip`;

      await platform.filesystem.saveFile(filename, blob, [
        {
          name: 'Voice Studio Box Generation',
          extensions: ['zip'],
        },
      ]);

      return blob;
    },
  });
}

export function useExportGenerationAudio() {
  const platform = usePlatform();

  return useMutation({
    mutationFn: async ({
      generationId,
      text,
      format = 'wav',
    }: {
      generationId: string;
      text: string;
      format?: GenerationExportFormat;
    }) => {
      // /export-file handles WAV, FLAC and MP3 (with the saved bitrate) and
      // falls back to the built-in MP3 encoder when ffmpeg is not installed.
      const blob = await extrasApi.exportFile(generationId, format);

      // Keep Vietnamese (and other non-ASCII) letters in the filename. Append
      // a short id so exports of similarly-worded generations don't collide.
      const filename = `${sanitizeFilename(text, 'audio')}-${generationId.substring(0, 8)}.${format}`;

      await platform.filesystem.saveFile(filename, blob, [
        {
          name: 'Audio File',
          extensions: [format],
        },
      ]);

      return blob;
    },
  });
}

export function useImportGeneration() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (file: File) => apiClient.importGeneration(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['history'] });
    },
  });
}
