/**
 * middleware 族的购物核验中间件。
 *
 * 默认不注入任何东西（对应 Self-Harness 里 middleware 初始为空）。
 * 进化器可以给 instruction 填入一段触发式提示、并用 predicate 控制触发条件。
 *
 * 当前版本：空实现，仅暴露面。后续进化可在这里加「买前强制核验」等
 * 触发式提示（对应 Self-Harness 的 middleware_policy 复合 hook）。
 *
 * @module @self-harness-dsh/verify-middleware
 */

export const name = 'verify-middleware'
export const inject = ['systemPrompt']

export function apply(ctx, config) {
  const instruction = (config?.instruction ?? '').trim()
  if (instruction === '') return

  ctx.systemPrompt.section({
    name: 'shopping-verify-middleware',
    order: 150,
    text: instruction,
  })
}
