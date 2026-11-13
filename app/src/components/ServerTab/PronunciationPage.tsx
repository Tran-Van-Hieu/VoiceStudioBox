import { Download, Plus, Save, Trash2, Upload, Wand2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Toggle } from '@/components/ui/toggle';
import { useToast } from '@/components/ui/use-toast';
import { type PronunciationRule, extrasApi } from '@/lib/api/extras';
import { useExtrasSettings } from '@/lib/hooks/useExtras';
import { usePlatform } from '@/platform/PlatformContext';
import { SettingRow, SettingSection } from './SettingRow';

const EMPTY_RULE: PronunciationRule = {
  from: '',
  to: '',
  case_sensitive: false,
  whole_word: true,
  enabled: true,
};

function parseRulesCsv(text: string): PronunciationRule[] {
  const rules: PronunciationRule[] = [];
  for (const raw of text.replace(/\r/g, '').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const sep = line.includes('\t') ? '\t' : line.includes(';') ? ';' : ',';
    const [from, ...rest] = line.split(sep);
    const to = rest.join(sep).trim().replace(/^"|"$/g, '');
    const src = from.trim().replace(/^"|"$/g, '');
    if (!src || ['from', 'tu', 'từ'].includes(src.toLowerCase())) continue;
    rules.push({ ...EMPTY_RULE, from: src, to });
  }
  return rules;
}

function rulesToCsv(rules: PronunciationRule[]): string {
  const esc = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  return ['from,to', ...rules.map((r) => `${esc(r.from)},${esc(r.to)}`)].join('\n');
}

export function PronunciationPage() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const platform = usePlatform();
  const { settings, update, updateAsync, isSaving } = useExtrasSettings();
  const [rules, setRules] = useState<PronunciationRule[]>([]);
  const [dirty, setDirty] = useState(false);
  const [sample, setSample] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (settings && !dirty) setRules(settings.pronunciation ?? []);
  }, [settings, dirty]);

  const duplicates = useMemo(() => {
    const seen = new Map<string, number>();
    for (const r of rules) {
      const k = r.case_sensitive ? r.from : r.from.toLowerCase();
      if (k) seen.set(k, (seen.get(k) ?? 0) + 1);
    }
    return seen;
  }, [rules]);

  const edit = (i: number, patch: Partial<PronunciationRule>) => {
    setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    setDirty(true);
  };

  const save = async () => {
    try {
      await updateAsync({ pronunciation: rules.filter((r) => r.from.trim()) });
      setDirty(false);
      toast({ title: t('extras.pronunciation.saved') });
    } catch (e) {
      toast({
        title: t('extras.pronunciation.saveFailed'),
        description: (e as Error).message,
        variant: 'destructive',
      });
    }
  };

  const runPreview = async () => {
    if (dirty) await save();
    try {
      const r = await extrasApi.testPronunciation(sample);
      setPreview(r.result);
    } catch (e) {
      setPreview(null);
      toast({ title: (e as Error).message, variant: 'destructive' });
    }
  };

  const onImport = async (file: File) => {
    const imported = parseRulesCsv(await file.text());
    if (!imported.length) {
      toast({ title: t('extras.pronunciation.importEmpty'), variant: 'destructive' });
      return;
    }
    setRules((rs) => [...rs, ...imported]);
    setDirty(true);
    toast({ title: t('extras.pronunciation.imported', { count: imported.length }) });
  };

  const onExport = async () => {
    const blob = new Blob([`﻿${rulesToCsv(rules)}`], { type: 'text/csv' });
    await platform.filesystem.saveFile('tu-dien-phat-am.csv', blob, [
      { name: 'CSV', extensions: ['csv'] },
    ]);
  };

  return (
    <div className="max-w-4xl space-y-8">
      <SettingSection
        title={t('extras.pronunciation.title')}
        description={t('extras.pronunciation.description')}
      >
        <SettingRow
          title={t('extras.pronunciation.enable.title')}
          description={t('extras.pronunciation.enable.description')}
          htmlFor="pronunciationEnabled"
          action={
            <Toggle
              id="pronunciationEnabled"
              checked={settings?.pronunciation_enabled ?? true}
              onCheckedChange={(v) => update({ pronunciation_enabled: v })}
            />
          }
        />
      </SettingSection>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setRules((rs) => [...rs, { ...EMPTY_RULE }]);
              setDirty(true);
            }}
          >
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            {t('extras.pronunciation.add')}
          </Button>
          <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
            <Upload className="h-3.5 w-3.5 mr-1.5" />
            {t('extras.pronunciation.import')}
          </Button>
          <Button size="sm" variant="outline" onClick={onExport} disabled={!rules.length}>
            <Download className="h-3.5 w-3.5 mr-1.5" />
            {t('extras.pronunciation.export')}
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.txt,.tsv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onImport(f);
              e.target.value = '';
            }}
          />
          <div className="flex-1" />
          {dirty && (
            <span className="text-xs text-amber-500">{t('extras.pronunciation.unsaved')}</span>
          )}
          <Button size="sm" onClick={save} disabled={!dirty || isSaving}>
            <Save className="h-3.5 w-3.5 mr-1.5" />
            {t('extras.pronunciation.save')}
          </Button>
        </div>

        <div className="rounded-lg border overflow-hidden">
          <div className="grid grid-cols-[1fr_1.4fr_auto_auto_auto_auto] gap-2 px-3 py-2 bg-muted/40 text-xs font-medium text-muted-foreground">
            <span>{t('extras.pronunciation.columns.from')}</span>
            <span>{t('extras.pronunciation.columns.to')}</span>
            <span className="text-center w-16">{t('extras.pronunciation.columns.wholeWord')}</span>
            <span className="text-center w-16">
              {t('extras.pronunciation.columns.caseSensitive')}
            </span>
            <span className="text-center w-12">{t('extras.pronunciation.columns.enabled')}</span>
            <span className="w-8" />
          </div>
          {rules.length === 0 && (
            <p className="px-3 py-6 text-sm text-muted-foreground text-center">
              {t('extras.pronunciation.empty')}
            </p>
          )}
          <div className="divide-y divide-border/60 max-h-[420px] overflow-y-auto">
            {rules.map((r, i) => {
              const key = r.case_sensitive ? r.from : r.from.toLowerCase();
              const dup = !!key && (duplicates.get(key) ?? 0) > 1;
              return (
                <div
                  // biome-ignore lint/suspicious/noArrayIndexKey: rows have no stable id
                  key={i}
                  className="grid grid-cols-[1fr_1.4fr_auto_auto_auto_auto] gap-2 px-3 py-1.5 items-center"
                >
                  <Input
                    value={r.from}
                    placeholder="TP.HCM"
                    onChange={(e) => edit(i, { from: e.target.value })}
                    className={dup ? 'border-amber-500' : undefined}
                    title={dup ? t('extras.pronunciation.duplicate') : undefined}
                  />
                  <Input
                    value={r.to}
                    placeholder={t('extras.pronunciation.toPlaceholder')}
                    onChange={(e) => edit(i, { to: e.target.value })}
                  />
                  <div className="w-16 flex justify-center">
                    <Checkbox
                      checked={r.whole_word}
                      onCheckedChange={(v) => edit(i, { whole_word: !!v })}
                    />
                  </div>
                  <div className="w-16 flex justify-center">
                    <Checkbox
                      checked={r.case_sensitive}
                      onCheckedChange={(v) => edit(i, { case_sensitive: !!v })}
                    />
                  </div>
                  <div className="w-12 flex justify-center">
                    <Checkbox
                      checked={r.enabled}
                      onCheckedChange={(v) => edit(i, { enabled: !!v })}
                    />
                  </div>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8"
                    aria-label={t('extras.pronunciation.remove')}
                    onClick={() => {
                      setRules((rs) => rs.filter((_, j) => j !== i));
                      setDirty(true);
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              );
            })}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{t('extras.pronunciation.importHint')}</p>
      </div>

      <SettingSection title={t('extras.pronunciation.test.title')}>
        <div className="py-3 space-y-3">
          <Textarea
            value={sample}
            onChange={(e) => setSample(e.target.value)}
            placeholder={t('extras.pronunciation.test.placeholder')}
            rows={3}
          />
          <Button size="sm" variant="outline" onClick={runPreview} disabled={!sample.trim()}>
            <Wand2 className="h-3.5 w-3.5 mr-1.5" />
            {t('extras.pronunciation.test.run')}
          </Button>
          {preview !== null && (
            <div className="rounded-md border bg-muted/30 p-3 text-sm whitespace-pre-wrap">
              {preview}
            </div>
          )}
        </div>
      </SettingSection>
    </div>
  );
}
