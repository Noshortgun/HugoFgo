import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { checkReferences } from './check-references.mjs';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hugofgo-references-'));
try {
  fs.mkdirSync(path.join(root, 'docs'), { recursive: true });
  fs.mkdirSync(path.join(root, 'cv'), { recursive: true });
  fs.mkdirSync(path.join(root, 'executor/perception/templates/cn'), { recursive: true });
  fs.writeFileSync(path.join(root, 'README.md'), '[存在](docs/ok.md) [缺失](docs/missing.md)');
  fs.writeFileSync(path.join(root, 'docs/ok.md'), '# OK');
  fs.writeFileSync(path.join(root, 'executor/perception/templates/cn/manifest.json'), JSON.stringify({
    templates: {
      present: { file: 'present.png', source: 'captures/tracked.png' },
      missing: { file: 'missing.png', source: 'captures/local.png' },
    },
  }));
  fs.writeFileSync(path.join(root, 'executor/perception/templates/cn/present.png'), 'png');
  fs.writeFileSync(path.join(root, 'cv/build_templates.py'), '');
  fs.writeFileSync(path.join(root, 'executor/perception/build_templates.py'), '');

  const isTracked = (_root, file) => file === 'captures/tracked.png';
  const result = checkReferences({ root, isTracked });
  assert(result.errors.some((error) => error.includes('docs/missing.md')), '应发现文档断链');
  assert(result.errors.some((error) => error.includes('missing.png')), '应发现模板缺失');
  assert(result.errors.some((error) => error.includes('captures/tracked.png')), '应发现入库来源缺失');
  assert(!result.errors.some((error) => error.includes('captures/local.png')), '默认不应要求本机来源');
  assert.equal(result.skippedLocal, 1);

  const local = checkReferences({ root, includeLocal: true, isTracked });
  assert(local.errors.some((error) => error.includes('captures/local.png')), '本机检查应发现来源缺失');
  console.log('文档与模板引用检查自检通过');
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
