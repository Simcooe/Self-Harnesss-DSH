/**
 * ShopSimulator tool plugin for DeepSeek Harness.
 *
 * Registers the model-facing shopping tools over the ShopSimulator HTTP API
 * (http://127.0.0.1:5700/api/shop_agent). One dsh process owns exactly one
 * leased environment slot (env_idx): the runner resets the environment first
 * and hands the lease + base URL to this plugin through config or environment.
 *
 * The environment action language is `search[query]` / `click[value]` /
 * `finish[reason]`; this plugin translates each tool call into that language
 * and returns the resulting observation text (plus a terminal summary when the
 * episode ends).
 *
 * This file intentionally depends on nothing from the dsh npm packages at
 * import time: it hand-writes the `ToolDefinition` objects and receives `ctx`
 * from the Cordis loader. `ctx.tools.register(def)` accepts a plain object.
 *
 * @module @self-harness-dsh/shop-tools
 */

export const name = 'shop-tools'
export const inject = ['tools']

/** Click targets that carry no argument: the button label is the click value. */
const CLICK_FIXED = {
  view_description: 'Description',
  view_features: 'Features',
  view_reviews: 'Reviews',
  view_attributes: 'Attributes',
  next_page: 'Next >',
  prev_page: '< Prev',
  back_to_search: 'Back to Search',
  buy_now: 'Buy Now',
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
  if (name === 'search_products') return `search[${args.query}]`
  if (name === 'finish_without_purchase') return `finish[${args.reason}]`
  if (name === 'open_product') return `click[${args.asin}]`
  if (name === 'select_option') return `click[${args.value}]`
  if (name in CLICK_FIXED) return `click[${CLICK_FIXED[name]}]`
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

function renderObservation(result) {
  let text = typeof result.instruction === 'string' ? result.instruction : ''
  if (result.done) {
    const parts = []
    if (result.reward !== undefined) parts.push(`reward=${result.reward}`)
    if (result.reward_valid !== undefined) parts.push(`reward_valid=${result.reward_valid}`)
    if (result.termination_reason) parts.push(`termination=${result.termination_reason}`)
    if (result.purchase && Object.keys(result.purchase).length > 0) {
      parts.push(`purchase=${JSON.stringify(result.purchase)}`)
    }
    text += `\n\n[TERMINAL] ${parts.join('; ')}`
  }
  return text
}

/**
 * Build a plain ToolDefinition bound to one environment lease. `parameters`
 * is a standard JSON-Schema object; the canonical output is a string rendered
 * as a single text block.
 */
function shopTool(cfg, name, description, parameters, actionOf) {
  return {
    name,
    description,
    parameters,
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const action = actionOf(args)
      if (action === null) throw new Error(`shop-tools: unknown tool ${name}`)
      const result = await interact(cfg, action, exec.signal)
      return renderObservation(result)
    },
  }
}

function noArg() {
  return {}
}

export function apply(ctx, config) {
  const cfg = resolveConfig(config)

  const tools = [
    shopTool(
      cfg,
      'search_products',
      'Search for products. Only when the latest observation says search is available. Keep the query concise: category plus the most distinguishing brand, model, feature, or spec. Do not repeat the same query or mechanically copy the whole request.',
      {
        type: 'object',
        properties: { query: { type: 'string', description: 'search keywords' } },
        required: ['query'],
        additionalProperties: false,
      },
      (args) => `search[${args.query}]`,
    ),
    shopTool(
      cfg,
      'open_product',
      'Open a listed candidate product to verify price, features, and specs. asin must come verbatim from the latest observation.',
      {
        type: 'object',
        properties: { asin: { type: 'string', description: 'product ASIN from the current page' } },
        required: ['asin'],
        additionalProperties: false,
      },
      (args) => `click[${args.asin}]`,
    ),
    shopTool(
      cfg,
      'select_option',
      'Select one option value to complete a purchasable variant; value must come from the latest observation. After selecting, re-check the price against budget.',
      {
        type: 'object',
        properties: { value: { type: 'string', description: 'option value from the latest observation' } },
        required: ['value'],
        additionalProperties: false,
      },
      (args) => `click[${args.value}]`,
    ),
    shopTool(cfg, 'view_description', 'Open the Description sub-page when the page shows it and more verification is needed.', noArg(), () => 'click[Description]'),
    shopTool(cfg, 'view_features', 'Open the Features sub-page when the page shows it and core features need checking.', noArg(), () => 'click[Features]'),
    shopTool(cfg, 'view_reviews', 'Open the Reviews sub-page when the page shows it and more judgment is needed.', noArg(), () => 'click[Reviews]'),
    shopTool(cfg, 'view_attributes', 'Open the Attributes sub-page when the page shows it and key attributes need checking.', noArg(), () => 'click[Attributes]'),
    shopTool(cfg, 'next_page', 'Go to the next page only when the page shows "Next >" and no suitable candidate is on the current page.', noArg(), () => 'click[Next >]'),
    shopTool(cfg, 'prev_page', 'Go back one page only when the page shows "< Prev".', noArg(), () => 'click[< Prev]'),
    shopTool(cfg, 'back_to_search', 'Return to the search page only when the page shows "Back to Search".', noArg(), () => 'click[Back to Search]'),
    shopTool(
      cfg,
      'buy_now',
      'Irreversible terminal action. Only when the page shows "Buy Now", the category is correct, the full-variant price is within budget, and this is the best verified candidate.',
      noArg(),
      () => 'click[Buy Now]',
    ),
    shopTool(
      cfg,
      'finish_without_purchase',
      'End without buying (not a success). Use only after several genuinely different searches and candidate checks still find no acceptable product.',
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
