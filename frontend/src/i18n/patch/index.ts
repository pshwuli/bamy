// 各页面组的补充文案：运行时用 mergeLocaleMessage 深合并，不影响主文案的类型推断
type Msgs = Record<string, unknown>
const mods = import.meta.glob<{ default: { zh: Msgs; en: Msgs } }>('./*.ts', { eager: true })

export function applyPatches(merge: (locale: string, msgs: Msgs) => void) {
  for (const [path, m] of Object.entries(mods)) {
    if (path.endsWith('/index.ts')) continue
    merge('zh-CN', m.default.zh ?? {})
    merge('en-US', m.default.en ?? {})
  }
}
