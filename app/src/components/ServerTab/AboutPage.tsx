import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import voiceboxLogo from '@/assets/logo.png';
import { usePlatform } from '@/platform/PlatformContext';

function FadeIn({ delay = 0, children }: { delay?: number; children: ReactNode }) {
  return (
    <div
      className="animate-[fadeInUp_0.5s_ease_both]"
      style={{ animationDelay: `${delay}ms` } as CSSProperties}
    >
      {children}
    </div>
  );
}

export function AboutPage() {
  const { t } = useTranslation();
  const platform = usePlatform();
  const [version, setVersion] = useState('');

  useEffect(() => {
    platform.metadata
      .getVersion()
      .then(setVersion)
      .catch(() => setVersion(''));
  }, [platform]);

  return (
    <>
      <style>{`
        @keyframes fadeInUp {
          from {
            opacity: 0;
            transform: translateY(8px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
      <div className="max-w-md mx-auto h-full flex items-center">
        <div className="flex flex-col items-center text-center space-y-5">
          <FadeIn delay={0}>
            <img src={voiceboxLogo} alt="Voice Studio Box" className="w-20 h-20 object-contain" />
          </FadeIn>

          <FadeIn delay={80}>
            <div className="space-y-1.5">
              <h1 className="text-lg font-semibold">Voice Studio Box</h1>
              <p className="text-xs text-muted-foreground/60 h-4">
                {version ? `v${version}` : '\u00A0'}
              </p>
            </div>
          </FadeIn>

          <FadeIn delay={160}>
            <p className="text-sm text-muted-foreground leading-relaxed max-w-sm">
              {t('settings.about.tagline')}
            </p>
          </FadeIn>

          <FadeIn delay={240}>
            <p className="text-xs text-muted-foreground/40 pt-4">{t('settings.about.license')}</p>
          </FadeIn>
        </div>
      </div>
    </>
  );
}
