export class ModelError extends Error {
  constructor(message) { super(message); this.name = 'ModelError'; }
}
export function qwenClient(config, fetcher = fetch) {
  return async (system, input, tokens = 2100) => {
    let endpoint;
    try {
      const base = new URL(config.base);
      if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash) throw Error();
      endpoint = base.href.replace(/\/+$/, '') + '/chat/completions';
    } catch { throw new ModelError('QWEN_BASE_URL 无效：应为模型供应商提供的 HTTPS 基础地址。'); }
    let r;
    try {
      r = await fetcher(endpoint, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(23000),
        headers: {authorization: 'Bearer ' + config.key, 'content-type': 'application/json'},
        body: JSON.stringify({model: config.model, ...(typeof config.thinking === 'boolean' ? {enable_thinking: config.thinking} : {}), response_format: {type:'json_object'}, temperature: 0.2, max_tokens: tokens,
          messages:[{role:'system',content:system},{role:'user',content:JSON.stringify(input)}]})
      });
    } catch { throw new ModelError('连接 Qwen 超时或失败，请检查网络和 API 地址。'); }
    if (!r.ok) {
      const note = r.status === 401 || r.status === 403 ? '检查密钥、区域和模型权限'
        : r.status === 429 ? '检查限流和账户额度'
        : r.status === 400 || r.status === 404 ? '检查接口地址、模型名称及 JSON 输出支持' : '稍后重试';
      throw new ModelError('Qwen 请求失败（HTTP ' + r.status + '），请' + note + '。');
    }
    try {
      const data = await r.json();
      const msg = data.choices[0].message.content;
      if (typeof msg !== 'string' || msg.length > 30000) throw Error();
      const parsed = JSON.parse(msg.trim().replace(/^```(?:json)?\s*|\s*```$/gi, ''));
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw Error();
      return parsed;
    } catch { throw new ModelError('Qwen 未返回有效的结构化结果，请重试或换用支持 JSON 输出的模型。'); }
  };
}
