import type { HugoFgoJob, Servant } from './types';

export interface SavedJobEntry {
  file: string;
  savedAt: number;
  job: HugoFgoJob;
}

/** 作业以首发三名队员命名；空位不参与命名。 */
export function jobNameForMembers(members: Servant[]): string {
  const names = members
    .slice(0, 3)
    .filter((member) => !member.empty && member.name.trim())
    .map((member) => member.name.trim());
  return names.length ? names.join('、') : '空编队';
}

export async function listSavedJobs(): Promise<SavedJobEntry[]> {
  const response = await fetch('/api/jobs');
  if (!response.ok) throw new Error(`读取作业文件夹失败（HTTP ${response.status}）`);
  const data = (await response.json()) as { jobs?: SavedJobEntry[] };
  return data.jobs ?? [];
}

export async function saveJobToLibrary(job: HugoFgoJob, name: string): Promise<{ file: string; savedAt: number }> {
  const response = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, job }),
  });
  const data = (await response.json().catch(() => ({}))) as { file?: string; savedAt?: number; error?: string };
  if (!response.ok) throw new Error(data.error || `写入作业文件夹失败（HTTP ${response.status}）`);
  return { file: data.file ?? `${name}.json`, savedAt: data.savedAt ?? Date.now() };
}
