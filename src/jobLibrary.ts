import { invoke, isTauri } from './tauri';
import type { HugoFgoJob, Servant } from './types';

export interface SavedJobEntry {
  file: string;
  savedAt: number;
  job: HugoFgoJob;
}

export const jobLibraryReadOnly = !isTauri;

const readOnlyMessage = '浏览器预览为只读，作业库需要桌面版。请用 npm run tauri dev 打开桌面窗口。';

/** 作业以首发三名队员命名；空位不参与命名。 */
export function jobNameForMembers(members: Servant[]): string {
  const names = members
    .slice(0, 3)
    .filter((member) => !member.empty && member.name.trim())
    .map((member) => member.name.trim());
  return names.length ? names.join('、') : '空编队';
}

export async function listSavedJobs(): Promise<SavedJobEntry[]> {
  if (!isTauri) return [];
  return invoke<SavedJobEntry[]>('list_jobs');
}

export async function readJobFromLibrary(file: string): Promise<HugoFgoJob> {
  if (!isTauri) throw new Error(readOnlyMessage);
  return invoke<HugoFgoJob>('read_job', { file });
}

export async function saveJobToLibrary(job: HugoFgoJob, name: string): Promise<{ file: string; savedAt: number }> {
  if (!isTauri) throw new Error(readOnlyMessage);
  return invoke<{ file: string; savedAt: number }>('save_job', { name, job });
}

export async function deleteJobFromLibrary(file: string): Promise<void> {
  if (!isTauri) throw new Error(readOnlyMessage);
  return invoke<void>('delete_job', { file });
}
