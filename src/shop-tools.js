/**
 * ShopSimulator tool plugin for DeepSeek Harness.
 *
 * Registers the three native ShopSimulator actions as model-facing tools:
 *   - search  -> search[keywords]
 *   - click   -> click[value]     (value: an entry of the current page's
 *                                   "可点击的按钮" list — an ASIN, a navigation
 *                                   button, an option, or "Buy Now")
 *   - finish  -> finish[reason]   (abandon without purchase)
 *
 * Model-visible surface vs verifier evidence are kept strictly separate, per
 * Self-Harness: the model only ever sees the observation text (and therefore
 * the clickable-button list). Reward, termination reason, structured
 * observation state, and progress are returned through `presentationMeta`,
 * which is persisted to the session log but is NOT model-visible. The goal and
 * answer-bearing fields are never forwarded.
 *
 * One dsh process owns exactly one leased environment slot (env_idx): the
 * runner resets the environment first and hands the lease + base URL through
 * config or environment.
 *
 * @module @self-harness-dsh/shop-tools
 */

export const name = 'shop-tools'
export const inject = ['tools']

/** Structured fields persisted for the verifier, never shown to the model. */
function evidenceState(result) {
  const rewardDetail = result.reward_detail
  return {
    observation_state: result.observation_state ?? null,
    progress: result.progress ?? null,
    done: result.done ?? false,
    termination_reason: result.termination_reason ?? null,
    reward: result.reward ?? null,
    reward_valid: result.reward_valid ?? null,
    reward_type: rewardDetail && typeof rewardDetail === 'object'
      ? rewardDetail.reward_type ?? null
      : null,
    purchase_success: rewardDetail && typeof rewardDetail === 'object'
      ? rewardDetail.purchase_success ?? null
      : null,
    purchase: result.purchase ?? null,
  }
}

function resolveConfig(config) {
  const baseUrl =
    config?.baseUrl ??
    process.env.SHOPSIM_BASE_URL ??
    'http://127.0.0.1:5700'
  const rawIdx = config?.envIdx ?? process.env.SHOPSIM_ENV_IDX
  const envIdx =
    rawIdx === undefined || rawIdx === null || rawIdx === '' ? undefined : Number(rawIdx)
  const timeoutMs =
    config?.timeoutMs ?? Number(process.env.SHOPSIM_TIMEOUT_MS ?? 60_000)
  return { baseUrl, envIdx, timeoutMs }
}

/** Convert one tool call into the environment action string, or null if unknown. */
function toAction(name, args) {
  if (name === 'search') return `search[${args.keywords}]`
  if (name === 'click') return `click[${args.value}]`
  if (name === 'finish') return `finish[${args.reason}]`
  return null
}

async function interact(cfg, action, signal) {
  if (cfg.envIdx === undefined) {
    throw new Error(
      'shop-tools: SHOPSIM_ENV_IDX is not set; reset the environment before running the agent',
    )
  }
  const response = await fetch(`${cfg.baseUrl}/api/shop_agent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'interact', env_idx: cfg.envIdx, response: action }),
    signal,
  })
  if (!response.ok) {
    throw new Error(`shop-tools: ShopSimulator HTTP ${response.status}`)
  }
  const payload = await response.json()
  const result = payload?.result
  if (!result || typeof result !== 'object') {
    throw new Error('shop-tools: ShopSimulator response missing result object')
  }
  if (result.error) {
    throw new Error(`shop-tools: ${result.error}`)
  }
  return result
}

/**
 * The model-visible observation text. When the episode has ended (`done`), the
 * environment renders a terminal page that leaks the reward and the goal
 * answer. Per Self-Harness, the verifier signal must not be fed back to the
 * executing agent, so a terminal episode is replaced with a neutral
 * end-of-episode notice; the reward and answer stay only in `evidenceState`.
 */
function modelVisibleText(result) {
  if (result.done) {
    return 'Episode finished.\n\n搜索功能是否可用: False\n\n可点击的按钮: []'
  }
  return typeof result.instruction === 'string' ? result.instruction : ''
}

/**
 * Build one native tool. `execute` returns a canonical object whose `text` is
 * the model-visible observation and whose `state` is the verifier evidence;
 * `render` shows only `text`, `presentationMeta` persists only `state`.
 */
function shopTool(cfg, name, description, parameters, actionOf) {
  return {
    name,
    description,
    parameters,
    async execute(args, exec) {
      const action = actionOf(args)
      if (action === null) throw new Error(`shop-tools: unknown tool ${name}`)
      const result = await interact(cfg, action, exec.signal)
      return {
        text: modelVisibleText(result),
        state: evidenceState(result),
        raw: result,
      }
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          text: { type: 'string' },
          state: { type: 'object' },
          raw: { type: 'object' },
        },
        required: ['text', 'state', 'raw'],
        additionalProperties: false,
      },
      render: (_args, value) => [{ type: 'text', text: value.text }],
      presentationMeta: (_args, value) => ({ state: value.state, raw: value.raw }),
    },
  }
}

export function apply(ctx, config) {
  const cfg = resolveConfig(config)

  const tools = [
    shopTool(
      cfg,
      'search',
      'Search the shop for products. Use only when the latest observation says 搜索功能是否可用: True. Keep the query concise: category plus the most distinguishing brand, model, feature, or spec. Do not repeat the same query or copy the whole request verbatim.',
      {
        type: 'object',
        properties: { keywords: { type: 'string', description: 'search keywords' } },
        required: ['keywords'],
        additionalProperties: false,
      },
      (args) => `search[${args.keywords}]`,
    ),
    shopTool(
      cfg,
      'click',
      'Click a value on the current page. The value MUST be taken verbatim from the latest observation\'s 可点击的按钮 list: a product ASIN to open it, a navigation button (Next >, < Prev, Back to Search, Description, Features, Reviews, Attributes), a product option to select a variant, or Buy Now to purchase. Clicking a value not in the current list is a wasted step.',
      {
        type: 'object',
        properties: { value: { type: 'string', description: 'click target from the current 可点击的按钮 list' } },
        required: ['value'],
        additionalProperties: false,
      },
      (args) => `click[${args.value}]`,
    ),
    shopTool(
      cfg,
      'finish',
      'End the episode without purchasing (this is not a success). Use only after several genuinely different searches and candidate checks still find no acceptable product matching the user\'s constraints.',
      {
        type: 'object',
        properties: {
          reason: { type: 'string', enum: ['no_suitable_product'], description: 'termination reason' },
        },
        required: ['reason'],
        additionalProperties: false,
      },
      (args) => `finish[${args.reason}]`,
    ),
  ]

  for (const tool of tools) {
    ctx.tools.register(tool)
  }
}
