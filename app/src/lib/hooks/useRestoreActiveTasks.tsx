import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '@/lib/api/client';
import type { ActiveDownloadTask } from '@/lib/api/types';

// Polling interval in milliseconds
const POLL_INTERVAL = 30000;

/**
 * Hook to monitor active tasks (downloads and generations).
 * Polls the server periodically to catch downloads triggered from anywhere
 * (transcription, generation, explicit download, etc.).
 *
 * Returns the active downloads so components can render download toasts.
 */
export function useRestoreActiveTasks() {
  const [activeDownloads, setActiveDownloads] = useState<ActiveDownloadTask[]>([]);

  // Track which downloads we've seen to detect new ones
  const seenDownloadsRef = useRef<Set<string>>(new Set());

  const fetchActiveTasks = useCallback(async () => {
    try {
      // Queued generations are not tracked here: one status stream each used up
      // the browser's six connections to the server and froze every other request.
      // The batch, script and history views poll their own progress.
      const tasks = await apiClient.getActiveTasks();

      // Update active downloads
      // Keep track of all active downloads (including new ones)
      const currentDownloadNames = new Set(tasks.downloads.map((d) => d.model_name));

      // Remove completed downloads from our seen set
      for (const name of seenDownloadsRef.current) {
        if (!currentDownloadNames.has(name)) {
          seenDownloadsRef.current.delete(name);
        }
      }

      // Add new downloads to seen set
      for (const download of tasks.downloads) {
        seenDownloadsRef.current.add(download.model_name);
      }

      setActiveDownloads(tasks.downloads);
    } catch (error) {
      // Silently fail - server might be temporarily unavailable
      console.debug('Failed to fetch active tasks:', error);
    }
  }, []);

  useEffect(() => {
    // Fetch immediately on mount
    fetchActiveTasks();

    // Poll for active tasks
    const interval = setInterval(fetchActiveTasks, POLL_INTERVAL);

    return () => clearInterval(interval);
  }, [fetchActiveTasks]);

  return activeDownloads;
}

/**
 * Map model names to display names for download toasts.
 */
export const MODEL_DISPLAY_NAMES: Record<string, string> = {
  'qwen-tts-1.7B': 'Qwen TTS 1.7B',
  'qwen-voice-design-1.7B': 'Qwen VoiceDesign 1.7B',
  omnivoice: 'OmniVoice',
  'whisper-turbo': 'Whisper Turbo',
  'qwen3-4b': 'Qwen3 4B',
};
