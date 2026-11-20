import { AssistantChatTransport, useChatRuntime } from '@assistant-ui/ai-sdk';
import { AssistantRuntimeProvider } from '@assistant-ui/react';
import { Thread } from './Thread';

export function App() {
  const runtime = useChatRuntime({
    transport: new AssistantChatTransport({
      api: '/console/api/chat',
    }),
  });

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
}
