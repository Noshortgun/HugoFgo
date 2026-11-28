import assert from 'node:assert/strict';
import fs from 'node:fs';
import { buildSecondarySelection, secondarySelectionAssetJobs } from './secondary-selection.mjs';

const tdTypeChange = { functions: [{ buffs: [{ type: 'tdTypeChange' }] }] };

assert.deepEqual(
  buildSecondarySelection(tdTypeChange, [{ script: { excludeTdChangeTypes: [3] } }]),
  {
    type: 'noblePhantasmCardType',
    label: '选择宝具卡色',
    shape: 'buttons2',
    optionCount: 2,
    options: [
      { value: 1, label: '蓝卡宝具' },
      { value: 2, label: '红卡宝具' },
    ],
  },
  '卫宫排除 Quick 后只能生成 Arts/Buster 两项',
);

assert.deepEqual(
  buildSecondarySelection(tdTypeChange, [{}]).options.map((option) => option.value),
  [3, 1, 2],
  '太空伊什塔尔按游戏画面 Quick/Arts/Buster 顺序生成',
);

assert.deepEqual(
  buildSecondarySelection({
    ...tdTypeChange,
    script: {
      selectTreasureDeviceInfo: [{
        title: '请选择宝具类型',
        treasureDevices: [
          { id: 2300601, message: '攻击类型' },
          { id: 2300698, message: '防御类型' },
        ],
      }],
    },
  }, [{ script: { excludeTdChangeTypes: [3] } }]),
  {
    type: 'noblePhantasmCardType',
    label: '请选择宝具类型',
    shape: 'buttons2',
    optionCount: 2,
    options: [
      { value: 1, label: '攻击类型', npId: 2300601 },
      { value: 2, label: '防御类型', npId: 2300698 },
    ],
  },
  'BB 迪拜是攻击/防御变体，不得生成三种卡色',
);

const selectAddInfo = {
  script: {
    SelectAddInfo: [{
      title: '请选择是否要消耗暴击星获得追加效果',
      btn: [
        { name: '不消耗', conds: [{ cond: 'NONE' }] },
        { name: '消耗10个', conds: [{ cond: 'STAR_HIGHER', value: 10 }] },
      ],
    }],
  },
};
assert.deepEqual(buildSecondarySelection(selectAddInfo, []), {
  type: 'selectAddInfo',
  label: '请选择是否要消耗暴击星获得追加效果',
  shape: 'buttons2',
  optionCount: 2,
  options: [
    { value: 0, label: '不消耗', conditions: [{ cond: 'NONE' }] },
    { value: 1, label: '消耗10个', conditions: [{ cond: 'STAR_HIGHER', value: 10 }] },
  ],
});

assert.deepEqual(secondarySelectionAssetJobs({
  script: {
    SelectAddInfo: [{
      title: '请选择宝具类型',
      btn: [
        { image: 'btn_select_003', imageUrl: 'https://static.atlasacademy.io/CN/Battle/Common/BattleAssetUIAtlas/btn_select_003.png', conds: [{ cond: 'NONE' }] },
        { image: 'btn_select_004', imageUrl: 'https://static.atlasacademy.io/CN/Battle/Common/BattleAssetUIAtlas/btn_select_004.png', conds: [{ cond: 'NONE' }] },
      ],
    }],
  },
}), [
  { url: 'https://static.atlasacademy.io/CN/Battle/Common/BattleAssetUIAtlas/btn_select_003.png', file: 'data/CN/assets/atlas/secondary/btn_select_003.png' },
  { url: 'https://static.atlasacademy.io/CN/Battle/Common/BattleAssetUIAtlas/btn_select_004.png', file: 'data/CN/assets/atlas/secondary/btn_select_004.png' },
]);

const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/secondary-selection-cn.json', import.meta.url), 'utf8'));
assert.equal(fixture.records.length, 16, '国服当前全部 16 个追加选择技能都必须有元数据用例');
for (const record of fixture.records) {
  const actual = buildSecondarySelection(record, record.noblePhantasms);
  assert.deepEqual({
    type: actual.type,
    shape: actual.shape,
    optionCount: actual.optionCount,
    values: actual.options.map((option) => option.value),
    labels: actual.options.map((option) => option.label),
  }, record.expected, `${record.svtId}/${record.skillId} 的菜单与画面顺序不符`);
}

console.log('secondary-selection.selftest: OK（16 个技能）');
