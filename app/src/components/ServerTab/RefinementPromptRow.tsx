import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useExtrasSettings } from '@/lib/hooks/useExtras';
import { SettingRow } from './SettingRow';

const EXAMPLE_VI = `Giữ nguyên tiếng Việt có dấu, không dịch sang tiếng Anh.
Viết hoa tên riêng và địa danh Việt Nam.
Đổi số đọc bằng chữ thành chữ số (ví dụ "hai mươi ba" → "23").`;

/** Custom system prompt for the refinement LLM (issue #913). */
export function RefinementPromptRow({ disabled }: { disabled?: boolean }) {
  const { t } = useTranslation();
  const { settings, update } = useExtrasSettings();
  const saved = settings?.refinement_custom_prompt ?? '';
  const mode = settings?.refinement_prompt_mode ?? 'append';
  const [draft, setDraft] = useState(saved);
  useEffect(() => setDraft(saved), [saved]);
  const dirty = draft !== saved;

  return (
    <SettingRow
      title={t('extras.refinePrompt.title')}
      description={t('extras.refinePrompt.description')}
      action={
        <Select
          value={mode}
          onValueChange={(v) => update({ refinement_prompt_mode: v as 'append' | 'replace' })}
          disabled={disabled}
        >
          <SelectTrigger className="w-[200px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="append">{t('extras.refinePrompt.modeAppend')}</SelectItem>
            <SelectItem value="replace">{t('extras.refinePrompt.modeReplace')}</SelectItem>
          </SelectContent>
        </Select>
      }
    >
      <div className="space-y-2">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={EXAMPLE_VI}
          rows={5}
          disabled={disabled}
          spellCheck={false}
        />
        {mode === 'replace' && (
          <p className="text-xs text-amber-500">{t('extras.refinePrompt.replaceWarning')}</p>
        )}
        <div className="flex gap-2">
          <Button
            size="sm"
            onClick={() => update({ refinement_custom_prompt: draft })}
            disabled={!dirty || disabled}
          >
            {t('extras.refinePrompt.save')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setDraft('');
              update({ refinement_custom_prompt: '' });
            }}
            disabled={!saved || disabled}
          >
            {t('extras.refinePrompt.clear')}
          </Button>
          {!draft && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDraft(EXAMPLE_VI)}
              disabled={disabled}
            >
              {t('extras.refinePrompt.useExample')}
            </Button>
          )}
        </div>
      </div>
    </SettingRow>
  );
}
