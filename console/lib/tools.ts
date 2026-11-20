import { tool } from 'ai';
import { z } from 'zod';
import {
  type OrchestratorClient,
  redactSecrets,
} from './orchestrator.js';


export function createConsoleTools(client: OrchestratorClient) {
  return {
    list_tickets: tool({
      description: 'List Superagent tickets from the live management API.',
      inputSchema: z.object({
        state: z
          .enum(['open', 'person', 'working', 'idle', 'ended'])
          .optional()
          .describe('Optional ticket filter'),
      }),
      execute: async ({ state }) => {
        const query = state ? `tickets?state=${encodeURIComponent(state)}` : 'tickets';
        return client.get(query);
      },
    }),

    get_ticket: tool({
      description: 'Get one ticket by id, including what it is waiting for.',
      inputSchema: z.object({
        id: z.string().min(1).describe('Ticket id'),
      }),
      execute: async ({ id }) => client.get(`tickets/${encodeURIComponent(id)}`),
    }),

    get_settings: tool({
      description:
        'Read Superagent settings. Values whose keys look like token, key, secret or password are redacted.',
      inputSchema: z.object({}),
      execute: async () => redactSecrets(await client.get('settings')),
    }),

    get_prompts: tool({
      description: 'Read the current Superagent prompts configuration.',
      inputSchema: z.object({}),
      execute: async () => client.get('prompts'),
    }),

    draft_issue: tool({
      description:
        'Draft a GitHub issue body in the repository issue format. Does not open the issue or write anywhere.',
      inputSchema: z.object({
        title: z.string().min(1).describe('Short imperative title'),
        what: z.string().min(1).describe('One paragraph: what to build and why'),
        acceptance: z
          .array(z.string().min(1))
          .min(1)
          .describe('Acceptance criteria lines'),
        dependsOn: z
          .array(z.string())
          .optional()
          .describe('Issue numbers this depends on, without #'),
        uat: z
          .array(
            z.object({
              name: z.string(),
              given: z.string(),
              when: z.string(),
              then: z.string(),
            }),
          )
          .optional()
          .describe('Optional UAT scenarios'),
      }),
      execute: async ({ title, what, acceptance, dependsOn, uat }) => {
        const lines = [
          `@forkboteu ${what}`,
          '',
          '## Acceptance criteria',
          ...acceptance.map((item) => `- [ ] ${item}`),
        ];
        if (uat?.length) {
          lines.push('', '## User Acceptance Tests', 'Environment: local', '');
          uat.forEach((scenario, index) => {
            lines.push(
              `### UAT-${index + 1}: ${scenario.name}`,
              `Given ${scenario.given}`,
              `When ${scenario.when}`,
              `Then ${scenario.then}`,
              '',
            );
          });
        }
        if (dependsOn?.length) {
          lines.push(`Depends on ${dependsOn.map((n) => `#${n.replace(/^#/, '')}`).join(', ')}`);
        }
        return { title, body: lines.join('\n').trim() + '\n' };
      },
    }),
  };
}
