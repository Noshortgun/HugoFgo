const CARD_OPTIONS = [
  { value: 3, label: '绿卡宝具' },
  { value: 1, label: '蓝卡宝具' },
  { value: 2, label: '红卡宝具' },
];

function buttonShape(optionCount) {
  return `buttons${optionCount}`;
}

export function buildSecondarySelection(skill, noblePhantasms = []) {
  const select = skill.script?.SelectAddInfo?.[0];
  if (select) {
    const options = (select.btn ?? []).map((button, index) => ({
      value: index,
      label: button.name || `选项 ${index + 1}`,
      ...(button.imageUrl ? { imageUrl: button.imageUrl } : {}),
      ...(button.conds ? { conditions: button.conds } : {}),
    }));
    return {
      type: 'selectAddInfo',
      label: select.title || '技能追加选择',
      shape: buttonShape(options.length),
      optionCount: options.length,
      options,
    };
  }

  if (!skill.functions?.some((func) => func.buffs?.some((buff) => buff.type === 'tdTypeChange'))) return undefined;

  const selectTreasureDevice = skill.script?.selectTreasureDeviceInfo?.[0];
  if (selectTreasureDevice) {
    const options = (selectTreasureDevice.treasureDevices ?? []).map((treasureDevice, index) => ({
      value: index + 1,
      label: treasureDevice.message,
      npId: treasureDevice.id,
    }));
    return {
      type: 'noblePhantasmCardType',
      label: selectTreasureDevice.title || '请选择宝具类型',
      shape: buttonShape(options.length),
      optionCount: options.length,
      options,
    };
  }

  const excluded = new Set(noblePhantasms.flatMap((np) => np.script?.excludeTdChangeTypes ?? []));
  const options = CARD_OPTIONS.filter((option) => !excluded.has(option.value));
  return {
    type: 'noblePhantasmCardType',
    label: '选择宝具卡色',
    shape: buttonShape(options.length),
    optionCount: options.length,
    options,
  };
}

export function secondarySelectionAssetJobs(skill, region = 'CN') {
  const buttons = skill.script?.SelectAddInfo?.flatMap((select) => select.btn ?? []) ?? [];
  const byUrl = new Map();
  for (const button of buttons) {
    if (!button.imageUrl || byUrl.has(button.imageUrl)) continue;
    const name = button.image || new URL(button.imageUrl).pathname.split('/').at(-1);
    byUrl.set(button.imageUrl, {
      url: button.imageUrl,
      file: `data/${region}/assets/atlas/secondary/${name}.png`,
    });
  }
  return [...byUrl.values()];
}
