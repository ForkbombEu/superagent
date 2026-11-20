import { AssistantChatTransport, useChatRuntime } from '@assistant-ui/ai-sdk';
import { createLocalStorageAdapter } from '@assistant-ui/core/react';
import {
  AssistantRuntimeProvider,
  useRemoteThreadListRuntime,
} from '@assistant-ui/react';
import { useEffect, useState, type FC } from 'react';
import { browserLocalStorage, CONSOLE_THREAD_PREFIX } from './localStorage';
import { Thread } from './Thread';

type Config = { configured: boolean; model: string };

const threadAdapter = createLocalStorageAdapter({
  storage: browserLocalStorage,
  prefix: CONSOLE_THREAD_PREFIX,
});

export const App: FC = () => {
  const [config, setConfig] = useState<Config | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch('/console/api/config')
      .then((res) => res.json())
      .then((data: Config) => {
        if (!cancelled) setConfig(data);
      })
      .catch(() => {
        if (!cancelled) setConfig({ configured: false, model: '' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!config) {
    return (
      <div className="console-shell">
        <p className="console-status">Loading…</p>
      </div>
    );
  }

  if (!config.configured) {
    return (
      <div className="console-shell">
        <header className="console-header">
          <h1>Console</h1>
          <p>Chat is not configured: set ANTHROPIC_API_KEY</p>
        </header>
      </div>
    );
  }

  return <ConfiguredChat />;
};

function useConsoleRuntime() {
  return useRemoteThreadListRuntime({
    adapter: threadAdapter,
    runtimeHook: () =>
      useChatRuntime({
        transport: new AssistantChatTransport({
          api: '/console/api/chat',
        }),
      }),
  });
}

const ConfiguredChat: FC = () => {
  const runtime = useConsoleRuntime();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="console-shell">
        <header className="console-header">
          <h1>Console</h1>
          <p>Chat with Opus about this deployment&apos;s design.</p>
        </header>
        <main className="console-main">
          <Thread />
        </main>
      </div>
    </AssistantRuntimeProvider>
  );
};
