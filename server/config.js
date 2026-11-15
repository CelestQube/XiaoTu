import {loadEnvFile} from 'node:process';
import {fileURLToPath} from 'node:url';
try { loadEnvFile(fileURLToPath(new URL('../.env', import.meta.url))); }
catch (error) { if(error.code !== 'ENOENT') throw new Error('无法读取项目 .env，请检查权限。'); }
export function getConfig(env = process.env) {
  return {
    key: env.DASHSCOPE_API_KEY?.trim() || '',
    model: env.QWEN_MODEL?.trim() || 'qwen-plus',
    thinking: /^(true|false)$/.test(env.QWEN_ENABLE_THINKING || '') ? env.QWEN_ENABLE_THINKING === 'true' : undefined,
    base: (env.QWEN_BASE_URL?.trim() || 'https://dashscope.aliyuncs.com/compatible-mode/v1').replace(/\/+$/, ''),
    amap: env.AMAP_MAPS_API_KEY?.trim() || '',
    amapWeb: env.AMAP_WEB_SERVICE_KEY?.trim() || env.AMAP_MAPS_API_KEY?.trim() || '',
    port: /^\d+$/.test(env.PORT || '') && +env.PORT >= 1024 && +env.PORT <= 65535 ? +env.PORT : 3210
  };
}
