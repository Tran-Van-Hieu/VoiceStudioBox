import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { AppFrame } from '@/components/AppFrame/AppFrame';
import { CloneTab } from '@/components/Studio/CloneTab';
import { DubTab } from '@/components/Studio/DubTab';
import { HistoryTab } from '@/components/Studio/HistoryTab';
import { LibraryTab } from '@/components/Studio/LibraryTab';
import { ModelUpdates } from '@/components/Studio/ModelUpdates';
import { SpeakTab } from '@/components/Studio/SpeakTab';
import { StudioTab } from '@/components/Studio/StudioTab';
import { PronunciationPage } from '@/components/ServerTab/PronunciationPage';
import { VramPage } from '@/components/ServerTab/VramPage';
import { EffectsTab } from '@/components/EffectsTab/EffectsTab';
import { ModelsTab } from '@/components/ModelsTab/ModelsTab';
import { AboutPage } from '@/components/ServerTab/AboutPage';
import { CapturesPage } from '@/components/ServerTab/CapturesPage';
import { GeneralPage } from '@/components/ServerTab/GeneralPage';
import { GenerationPage } from '@/components/ServerTab/GenerationPage';
import { GpuPage } from '@/components/ServerTab/GpuPage';
import { LogsPage } from '@/components/ServerTab/LogsPage';
import { MCPPage } from '@/components/ServerTab/MCPPage';
import { SettingsLayout } from '@/components/ServerTab/ServerTab';
import { Sidebar } from '@/components/Sidebar';
import { Toaster } from '@/components/ui/toaster';
import { useGenerationProgress } from '@/lib/hooks/useGenerationProgress';
import { useModelDownloadToast } from '@/lib/hooks/useModelDownloadToast';
import { MODEL_DISPLAY_NAMES, useRestoreActiveTasks } from '@/lib/hooks/useRestoreActiveTasks';
import { usePlatform } from '@/platform/PlatformContext';

// Simple platform check that works in both web and Tauri
const isMacOS = () => navigator.platform.toLowerCase().includes('mac');

// Root layout component
function RootLayout() {
  // Monitor active downloads/generations and show toasts for them
  const activeDownloads = useRestoreActiveTasks();

  // Subscribe to SSE for pending generations — handles completion, auto-play, and history refresh
  useGenerationProgress();

  return (
    <AppFrame>
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <Sidebar isMacOS={isMacOS()} />

        <main className="flex-1 ml-52 overflow-hidden flex flex-col">
          <div className="w-full mx-auto px-6 max-w-[1800px] h-full overflow-hidden flex flex-col">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Show download toasts for any active downloads (from anywhere) */}
      {activeDownloads.map((download) => {
        const displayName = MODEL_DISPLAY_NAMES[download.model_name] || download.model_name;
        return (
          <DownloadToastRestorer
            key={download.model_name}
            modelName={download.model_name}
            displayName={displayName}
          />
        );
      })}

      <Toaster />
    </AppFrame>
  );
}

/**
 * Component that restores a download toast for a specific model.
 */
function DownloadToastRestorer({
  modelName,
  displayName,
}: {
  modelName: string;
  displayName: string;
}) {
  // Use the download toast hook to restore the toast
  useModelDownloadToast({
    modelName,
    displayName,
    enabled: true,
  });

  return null;
}

// Root route with layout
const rootRoute = createRootRoute({
  component: RootLayout,
});

// Index route (main/generate)
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: SpeakTab,
});

// Stories route
const storiesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/studio',
  component: StudioTab,
});

// Voices route
const voicesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/voices',
  component: LibraryTab,
});

// Captures route (prototype — will replace AudioTab once the new flow is ready)
const capturesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clone',
  component: CloneTab,
});

const dubRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/dub',
  component: DubTab,
});

const historyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/history',
  component: HistoryTab,
});

// Settings: four groups instead of eleven tabs. Each page already titles its
// own sections, so a group just stacks them.
function SettingsGroup({ pages }: { pages: ReactNode[] }) {
  return <div className="space-y-12">{pages}</div>;
}

function GeneralSettings() {
  const isTauri = usePlatform().metadata.isTauri;
  return (
    <SettingsGroup
      pages={[
        <GeneralPage key="general" />,
        isTauri && <LogsPage key="logs" />,
        <AboutPage key="about" />,
      ]}
    />
  );
}

function VoiceSettings() {
  return (
    <SettingsGroup
      pages={[
        <GenerationPage key="generation" />,
        <PronunciationPage key="pronunciation" />,
        <EffectsTab key="effects" />,
      ]}
    />
  );
}

function ModelSettings() {
  const isTauri = usePlatform().metadata.isTauri;
  return (
    <SettingsGroup
      pages={[
        <div key="models" className="space-y-6">
          <ModelUpdates />
          <ModelsTab />
        </div>,
        <VramPage key="vram" />,
        isTauri && <GpuPage key="gpu" />,
      ]}
    />
  );
}

function AdvancedSettings() {
  return <SettingsGroup pages={[<CapturesPage key="captures" />, <MCPPage key="mcp" />]} />;
}

// Settings layout route (parent for sub-tabs)
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  component: SettingsLayout,
});

const settingsGeneralRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: '/',
  component: GeneralSettings,
});

const settingsGenerationRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: '/generation',
  component: VoiceSettings,
});

const settingsModelsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: '/models',
  component: ModelSettings,
});

const settingsAdvancedRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: '/advanced',
  component: AdvancedSettings,
});

// Old one-page-per-tab links land on the group that now holds that page.
const OLD_SETTINGS_PATHS = {
  '/effects': '/settings/generation',
  '/pronunciation': '/settings/generation',
  '/vram': '/settings/models',
  '/gpu': '/settings/models',
  '/captures': '/settings/advanced',
  '/mcp': '/settings/advanced',
  '/logs': '/settings',
  '/about': '/settings',
} as const;

const oldSettingsRoutes = Object.entries(OLD_SETTINGS_PATHS).map(([path, to]) =>
  createRoute({
    getParentRoute: () => settingsRoute,
    path,
    beforeLoad: () => {
      throw redirect({ to });
    },
  }),
);

// Redirect old /server path to /settings
const serverRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/server',
  beforeLoad: () => {
    throw redirect({ to: '/settings' });
  },
});

// Route tree
const routeTree = rootRoute.addChildren([
  indexRoute,
  storiesRoute,
  capturesRoute,
  voicesRoute,
  dubRoute,
  historyRoute,
  settingsRoute.addChildren([
    settingsGeneralRoute,
    settingsGenerationRoute,
    settingsModelsRoute,
    settingsAdvancedRoute,
    ...oldSettingsRoutes,
  ]),
  serverRedirectRoute,
]);

// Create router
export const router = createRouter({ routeTree });

// Register router for type safety
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
