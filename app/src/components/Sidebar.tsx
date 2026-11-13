import { Link, useMatchRoute } from '@tanstack/react-router';
import { AudioLines, History, Languages, Library, type LucideIcon, Settings } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import voiceboxLogo from '@/assets/logo.png';
import { cn } from '@/lib/utils/cn';
import { usePlatform } from '@/platform/PlatformContext';
import type { UpdateStatus } from '@/platform/types';
import { version } from '../../package.json';

interface SidebarProps {
  isMacOS?: boolean;
}

// One entry per step of the work: pick a voice, make audio, dub, find past results, settings.
// `also` lists pages reached from inside a section, so its entry stays highlighted there.
const tabs: Array<{
  id: string;
  path: string;
  also?: string[];
  icon: LucideIcon;
  labelKey: string;
}> = [
  {
    id: 'voices',
    path: '/voices',
    also: ['/clone'],
    icon: Library,
    labelKey: 'studio.nav.library',
  },
  { id: 'speak', path: '/', also: ['/studio'], icon: AudioLines, labelKey: 'studio.nav.speak' },
  { id: 'dub', path: '/dub', icon: Languages, labelKey: 'studio.nav.dub' },
  { id: 'history', path: '/history', icon: History, labelKey: 'studio.nav.history' },
  { id: 'settings', path: '/settings', icon: Settings, labelKey: 'studio.nav.settings' },
];

export function Sidebar({ isMacOS }: SidebarProps) {
  const { t } = useTranslation();
  const matchRoute = useMatchRoute();
  const platform = usePlatform();
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>(platform.updater.getStatus());
  useEffect(() => platform.updater.subscribe(setUpdateStatus), [platform.updater]);

  return (
    <nav
      className={cn(
        'fixed left-0 top-0 bottom-8 w-52 bg-sidebar border-r border-border flex flex-col px-3 pt-10 pb-3 gap-1',
        isMacOS && 'pt-14',
      )}
    >
      <div className="flex items-center gap-2.5 px-2 mb-4">
        <img src={voiceboxLogo} alt="" className="sidebar-logo w-8 h-8 object-contain" />
        <span className="font-semibold">Voice Studio Box</span>
      </div>
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const active =
          (tab.path === '/'
            ? matchRoute({ to: '/', fuzzy: false })
            : matchRoute({ to: tab.path, fuzzy: true })) ||
          tab.also?.some((p) => matchRoute({ to: p, fuzzy: true }));
        return (
          <Link
            key={tab.id}
            to={tab.path}
            className={cn(
              'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
              active
                ? 'bg-accent/15 text-foreground font-medium'
                : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
            )}
          >
            <Icon className={cn('h-4 w-4 shrink-0', active && 'text-accent')} />
            {t(tab.labelKey)}
          </Link>
        );
      })}
      <div className="mt-auto px-3 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span>v{version}</span>
        {updateStatus.available && (
          <Link to="/settings" className="text-accent hover:underline">
            {t('nav.updateBadge')}
          </Link>
        )}
      </div>
    </nav>
  );
}
