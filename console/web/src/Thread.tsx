import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from '@assistant-ui/react';
import type { FC } from 'react';

export const Thread: FC = () => {
  return (
    <ThreadPrimitive.Root className="thread-root">
      <ThreadPrimitive.Viewport className="thread-viewport">
        <ThreadPrimitive.Empty>
          <p className="thread-empty">Ask about tickets, gates, or draft an issue.</p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages
          components={{
            UserMessage,
            AssistantMessage,
          }}
        />
      </ThreadPrimitive.Viewport>
      <ComposerPrimitive.Root className="composer-root">
        <ComposerPrimitive.Input
          className="composer-input"
          placeholder="Message Opus…"
          rows={1}
        />
        <ComposerPrimitive.Send className="composer-send">Send</ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
};

const UserMessage: FC = () => (
  <MessagePrimitive.Root className="message user">
    <MessagePrimitive.Content />
  </MessagePrimitive.Root>
);

const AssistantMessage: FC = () => (
  <MessagePrimitive.Root className="message assistant">
    <MessagePrimitive.Content />
  </MessagePrimitive.Root>
);
