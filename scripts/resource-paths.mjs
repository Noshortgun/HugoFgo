import path from 'node:path';

export const REGIONS = ['CN', 'JP', 'TW'];

export function parseRegion(argv = process.argv.slice(2), fallback = 'CN') {
  const inline = argv.find((value) => value.startsWith('--region='));
  const index = argv.indexOf('--region');
  const region = inline ? inline.slice('--region='.length) : index >= 0 ? argv[index + 1] : fallback;
  if (!REGIONS.includes(region)) throw new Error(`不支持的区服：${region ?? '(缺少参数)'}；可用区服为 ${REGIONS.join(' / ')}`);
  return region;
}

export function resourcePaths(root, options = {}) {
  const region = parseRegion(options.argv ?? process.argv.slice(2));
  const dataRoot = path.resolve((options.env ?? process.env).HUGOFGO_DATA_ROOT || path.join(root, 'data'));
  const regionRoot = path.join(dataRoot, region);
  const regionParts = (file) => {
    const parts = file.split(/[\\/]/).filter((part) => part && part !== '.');
    if (parts[0] === 'data') parts.shift();
    if (REGIONS.includes(parts[0])) parts.shift();
    return parts;
  };
  const absolute = (file) => {
    const normalized = file.split(/[\\/]/).join(path.sep);
    if (normalized === 'data' || normalized.startsWith(`data${path.sep}`)) return path.join(regionRoot, ...regionParts(normalized));
    return path.isAbsolute(normalized) ? normalized : path.join(root, normalized);
  };
  const relative = (file) => {
    const resolved = path.resolve(file);
    const inData = path.relative(dataRoot, resolved);
    if (!inData.startsWith('..') && !path.isAbsolute(inData)) return `data/${inData.split(path.sep).join('/')}`;
    return path.relative(root, resolved).split(path.sep).join('/');
  };
  return {
    region,
    dataRoot,
    regionRoot,
    absolute,
    relative,
    regionFile: (file) => path.join(regionRoot, ...file.split(/[\\/]/)),
    webPath: (file) => `/data/${region}/${regionParts(file).join('/')}`,
  };
}
