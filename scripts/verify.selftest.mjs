import assert from 'node:assert/strict';
import { createVerificationSteps } from './verify.mjs';

const steps = createVerificationSteps('python');
const unit = steps.find((step) => step.name === 'executor-unit');
assert(unit, '缺少全部执行器单测');
assert.deepEqual(unit.args, ['-m', 'unittest', 'discover', '-s', 'executor', '-t', '.', '-v']);
for (const name of ['frontend-build', 'run-state', 'run-settings', 'job-roundtrip', 'device-selftest', 'references', 'references-selftest', 'fixtures', 'resources-selftest', 'desktop-selftest', 'scrcpy-resources', 'replay', 'issues-selftest']) {
  assert(steps.some((step) => step.name === name), `验证清单缺少 ${name}`);
}
assert(!steps.some((step) => step.args.includes('executor/domain/tests')), '不得只跑 domain 测试');
assert(!steps.some((step) => step.args.includes('executor/perception/tests')), '不得只跑 perception 测试');
console.log('验证入口自检通过');
