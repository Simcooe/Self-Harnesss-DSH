/**
 * prompt_instruction 族的购物指令插件。
 *
 * 通过 dsh 的 system-prompt section 机制注入四个「可编辑面」的指令文本：
 *   bootstrap / execution / verification / failure-recovery。
 *
 * 每个指令是一个 prompt_text 面（对应 Self-Harness 的
 * build_bootstrap/execution/verification/failure_recovery_instruction）。
 * 进化器只改这里的字符串，不改插件结构。
 *
 * @module @self-harness-dsh/instructions
 */

export const name = 'instructions'
export const inject = ['systemPrompt']

const DEFAULT = {
  bootstrap:
    'Start by reading the user\'s shopping need, then plan a search. Pick the category plus the most distinguishing brand, model, feature, or spec as the first query.',
  execution:
    'Keep moving: after searching, open a candidate and verify its attributes before deciding. Do not loop over the same search or the same product; if you are not making progress, change the query or pick a different candidate.',
  verification:
    'Before buying, verify the candidate against every requirement in the latest observation: category, brand, model, features, options, and the final price after selecting the variant.',
  'failure-recovery':
    'If a click or search does nothing useful, do not repeat the same action. Re-read the clickable buttons and try a different target or a different query.',
}

/** 每个指令面的 section order（100-199 是工具引导段） */
const ORDER = {
  bootstrap: 110,
  execution: 120,
  verification: 130,
  'failure-recovery': 140,
}

export function apply(ctx, config) {
  const cfg = { ...DEFAULT, ...(config ?? {}) }
  for (const [key, order] of Object.entries(ORDER)) {
    const text = cfg[key]
    if (typeof text !== 'string' || text.trim() === '') continue
    ctx.systemPrompt.section({ name: `shopping-${key}`, order, text })
  }
}
