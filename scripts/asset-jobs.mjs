import path from 'node:path';

export const ASCENSION_CARD_IDS = new Set([800100, 4000100]);
export const CLASS_ICON_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 23, 25, 28, 33];
export const REQUIRED_NONEMPTY_CATEGORIES = [
  'faces.ascension',
  'commands.ascension',
  'ce',
  'skills.servant',
];

function assetJob(category, url, file) {
  return url && file ? { category, url, file } : null;
}

export function servantAssetJobs(servant) {
  const jobs = [];
  const add = (category, url, file) => {
    const job = assetJob(category, url, file);
    if (job) jobs.push(job);
  };
  const assets = servant.extraAssets ?? {};
  add('faces.base', assets.faces?.ascension?.['1'] ?? assets.faces?.ascension?.['0'], `data/assets/atlas/faces/${servant.id}.png`);
  add('cards.base', assets.charaGraph?.ascension?.['1'] ?? assets.charaGraph?.ascension?.['0'], `data/assets/atlas/cards/${servant.id}.png`);
  for (let stage = 1; stage <= 4; stage++) {
    add('faces.ascension', assets.faces?.ascension?.[String(stage)], `data/assets/atlas/faces/${servant.id}-${stage}.png`);
    if (ASCENSION_CARD_IDS.has(servant.id)) {
      add('cards.ascension', assets.charaGraph?.ascension?.[String(stage)], `data/assets/atlas/cards/${servant.id}-${stage}.png`);
    }
  }
  for (const [stage, url] of Object.entries(assets.commands?.ascension ?? {})) {
    add('commands.ascension', url, `data/assets/atlas/commands/${servant.id}/${stage}.png`);
  }
  for (const [costume, url] of Object.entries(assets.commands?.costume ?? {})) {
    add('commands.costume', url, `data/assets/atlas/commands/${servant.id}/costume-${costume}.png`);
  }
  if (servant.id === 800100) {
    add('faces.costume', assets.faces?.costume?.['800200'], 'data/assets/atlas/faces/800100-paladin.png');
    add('cards.costume', assets.charaGraph?.costume?.['800200'], 'data/assets/atlas/cards/800100-paladin.png');
    add('faces.costume', assets.faces?.costume?.['800190'], 'data/assets/atlas/faces/800100-ortenaus.png');
    add('cards.costume', assets.charaGraph?.costume?.['800190'], 'data/assets/atlas/cards/800100-ortenaus.png');
  }
  for (const skill of servant.skills ?? []) {
    if (skill.icon) {
      add('skills.servant', skill.icon, `data/assets/atlas/skills/${path.basename(new URL(skill.icon).pathname)}`);
    }
  }
  return jobs;
}

export function buildAssetJobs(raw) {
  const jobs = new Map();
  for (const servant of raw.servants) {
    for (const job of servantAssetJobs(servant)) jobs.set(job.file, job);
  }
  for (const equip of raw.equips) {
    if (equip.type !== 'servantEquip') continue;
    const job = assetJob('ce', equip.extraAssets?.equipFace?.equip?.[String(equip.id)], `data/assets/atlas/ce/${equip.id}.png`);
    if (job) jobs.set(job.file, job);
  }
  for (const code of raw.mystics) {
    const assets = code.extraAssets ?? {};
    const add = (category, url, file) => {
      const job = assetJob(category, url, file);
      if (job) jobs.set(job.file, job);
    };
    add('mystic.item', assets.item?.female, `data/assets/atlas/mystic/items/female-${code.id}.png`);
    add('mystic.item', assets.item?.male, `data/assets/atlas/mystic/items/male-${code.id}.png`);
    add('mystic.figure', assets.masterFigure?.female, `data/assets/atlas/mystic/figures/female-${code.id}.png`);
    add('mystic.figure', assets.masterFigure?.male, `data/assets/atlas/mystic/figures/male-${code.id}.png`);
    for (const skill of code.skills ?? []) {
      if (skill.icon) {
        add('skills.mystic', skill.icon, `data/assets/atlas/skills/${path.basename(new URL(skill.icon).pathname)}`);
      }
    }
  }
  for (const id of CLASS_ICON_IDS) {
    const job = assetJob('classes', 'https://static.atlasacademy.io/CN/ClassIcons/class3_' + id + '.png', `data/assets/classes/${id}.png`);
    if (job) jobs.set(job.file, job);
  }
  return [...jobs.values()];
}

export function countAssetCategories(jobs) {
  const counts = {};
  for (const job of jobs) counts[job.category] = (counts[job.category] ?? 0) + 1;
  return Object.fromEntries(Object.entries(counts).sort(([left], [right]) => left.localeCompare(right)));
}

export function assetCategoryGaps(manifest) {
  const categories = manifest?.assetCategories;
  if (!categories || typeof categories !== 'object') return ['assetCategories'];
  return REQUIRED_NONEMPTY_CATEGORIES.filter((category) => !(categories[category] > 0));
}
