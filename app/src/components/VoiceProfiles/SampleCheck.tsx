import { useQuery } from '@tanstack/react-query';
import { useEffect, useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Checkbox } from '@/components/ui/checkbox';
import { apiClient } from '@/lib/api/client';

/**
 * Scores a voice sample before it is saved and offers to remove its background
 * noise. A poor sample is only flagged: cloning it is still allowed.
 */
export function SampleCheck({
  file,
  clean,
  onCleanChange,
}: {
  file: File | null | undefined;
  clean: boolean;
  onCleanChange: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  const cleanId = useId();
  const { data, isFetching } = useQuery({
    queryKey: ['sample-check', file?.name, file?.size, file?.lastModified],
    queryFn: () => apiClient.checkProfileSample(file as File),
    enabled: !!file,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const noisy = !!data?.issues.includes('noisy');

  // Suggest cleaning for a noisy sample; the user can still untick it.
  useEffect(() => {
    if (data) onCleanChange(noisy);
  }, [data, noisy, onCleanChange]);

  if (!file) return null;
  return (
    <div className="rounded-md border p-3 space-y-2 text-xs">
      {isFetching || !data ? (
        <p className="text-muted-foreground">{t('sampleCheck.checking')}</p>
      ) : (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">{t('sampleCheck.title')}</span>
            <span className="tabular-nums text-muted-foreground">
              {t('sampleCheck.score', { score: data.score })} · SNR {data.snr_db.toFixed(0)} dB
            </span>
          </div>
          {data.issues.length > 0 ? (
            <>
              <ul className="text-amber-500 space-y-0.5">
                {data.issues.map((i) => (
                  <li key={i}>{t(`studio.clone.issue.${i}`)}</li>
                ))}
              </ul>
              <p className="text-muted-foreground">{t('sampleCheck.stillAllowed')}</p>
            </>
          ) : (
            <p className="text-emerald-500">{t('studio.clone.clipGood')}</p>
          )}
        </>
      )}
      <label htmlFor={cleanId} className="flex items-start gap-2 cursor-pointer">
        <Checkbox
          id={cleanId}
          checked={clean}
          onCheckedChange={(v) => onCleanChange(!!v)}
          className="mt-0.5"
        />
        <span>
          {t('sampleCheck.clean')}
          <span className="block text-muted-foreground">{t('sampleCheck.cleanHint')}</span>
        </span>
      </label>
    </div>
  );
}
