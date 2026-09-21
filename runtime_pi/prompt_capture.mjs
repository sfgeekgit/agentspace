// Record actual provider system messages without credentials or model thinking.
// Session JSONL already records user/assistant text. This fills its system gap.
import {appendFileSync} from 'node:fs';
import {join} from 'node:path';

export default function (pi) {
  pi.on('before_provider_request', (event, ctx) => {
    const messages = event.payload?.messages?.filter(m => m.role === 'system' || m.role === 'developer');
    if (!messages?.length) return;
    appendFileSync(join(ctx.cwd, 'prompt_log.jsonl'), JSON.stringify({
      kind: 'provider_system', timestamp: new Date().toISOString(),
      session: ctx.sessionManager.getSessionFile(), messages,
    }) + '\n', {mode: 0o600});
  });
}
