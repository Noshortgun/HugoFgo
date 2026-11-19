// @ts-nocheck
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const jobsDir = path.join(root, 'jobs');
let resourceRunning = false;

function sanitizeJobFileName(name) {
  const cleaned = String(name ?? '')
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '')
    .replace(/[. ]+$/g, '')
    .trim();
  return (cleaned || '未命名作业').slice(0, 100);
}

function readRequestBody(request, limit = 5 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let data = '';
    request.on('data', (chunk) => {
      data += chunk;
      if (data.length > limit) {
        reject(new Error('请求内容过大'));
        request.destroy();
      }
    });
    request.on('end', () => resolve(data));
    request.on('error', reject);
  });
}

function runResource(action: string, response: import('node:http').ServerResponse) {
  if (!['check', 'update', 'verify', 'refresh'].includes(action)) {
    response.statusCode = 400;
    response.end(JSON.stringify({ success: false, output: '不支持的资源操作' }));
    return;
  }
  if (resourceRunning) {
    response.statusCode = 409;
    response.end(JSON.stringify({ success: false, output: '已有资源任务正在执行' }));
    return;
  }
  resourceRunning = true;
  const args = [path.join(root, 'scripts/resources.mjs'), action === 'refresh' ? 'update' : action];
  if (action === 'refresh') args.push('--refresh-metadata');
  const child = spawn(process.execPath, args, { cwd: root });
  let output = '';
  child.stdout.on('data', (data) => { output += data.toString(); });
  child.stderr.on('data', (data) => { output += data.toString(); });
  child.on('error', (error) => {
    resourceRunning = false;
    response.statusCode = 500;
    response.end(JSON.stringify({ success: false, output: error.message }));
  });
  child.on('close', (code) => {
    resourceRunning = false;
    response.statusCode = code === 0 ? 200 : 500;
    response.setHeader('Content-Type', 'application/json; charset=utf-8');
    response.end(JSON.stringify({ success: code === 0, output }));
  });
}

export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'hugofgo-resource-api',
      configureServer(server) {
        server.middlewares.use('/api/resources', (request, response) => {
          if (request.method !== 'POST') {
            response.statusCode = 405;
            response.end(JSON.stringify({ success: false, output: '仅支持 POST' }));
            return;
          }
          runResource(request.url?.replace(/^\//, '') ?? '', response);
        });
      },
    },
    {
      name: 'hugofgo-job-library',
      configureServer(server) {
        server.middlewares.use('/api/jobs', async (request, response) => {
          response.setHeader('Content-Type', 'application/json; charset=utf-8');
          try {
            if (request.method === 'GET') {
              await fs.mkdir(jobsDir, { recursive: true });
              const files = (await fs.readdir(jobsDir)).filter((file) => file.endsWith('.json'));
              const jobs = [];
              for (const file of files) {
                const filePath = path.join(jobsDir, file);
                try {
                  const [text, stat] = await Promise.all([fs.readFile(filePath, 'utf8'), fs.stat(filePath)]);
                  jobs.push({ file, savedAt: stat.mtimeMs, job: JSON.parse(text) });
                } catch {
                  // 跳过损坏或不是作业的 JSON，不影响其他作业读取。
                }
              }
              jobs.sort((left, right) => right.savedAt - left.savedAt);
              response.end(JSON.stringify({ jobs }));
              return;
            }
            if (request.method === 'POST') {
              const body = JSON.parse(await readRequestBody(request));
              if (!body?.job || body.job.version !== 1) throw new Error('作业格式无效');
              const name = sanitizeJobFileName(body.name || body.job.name);
              await fs.mkdir(jobsDir, { recursive: true });
              const target = path.resolve(jobsDir, `${name}.json`);
              if (!target.startsWith(jobsDir + path.sep)) throw new Error('作业名称无效');
              await fs.writeFile(target, JSON.stringify(body.job, null, 2), 'utf8');
              const stat = await fs.stat(target);
              response.end(JSON.stringify({ saved: true, file: path.basename(target), savedAt: stat.mtimeMs }));
              return;
            }
            response.statusCode = 405;
            response.end(JSON.stringify({ error: '仅支持 GET 或 POST' }));
          } catch (error) {
            response.statusCode = 500;
            response.end(JSON.stringify({ error: error instanceof Error ? error.message : '作业读写失败' }));
          }
        });
      },
    },
  ],
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
  },
});
