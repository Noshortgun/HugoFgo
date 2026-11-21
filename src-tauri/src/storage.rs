use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SavedJobEntry {
    pub file: String,
    pub saved_at: u128,
    pub job: Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveJobResult {
    pub file: String,
    pub saved_at: u128,
}

fn sanitize_job_name(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .filter(|character| {
            !matches!(
                character,
                '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|'
            ) && !character.is_control()
        })
        .collect();
    let cleaned = cleaned.trim().trim_end_matches(['.', ' ']).trim();
    let cleaned: String = cleaned.chars().take(100).collect();
    if cleaned.is_empty() {
        "未命名作业".to_string()
    } else {
        cleaned
    }
}

fn saved_at(path: &Path) -> Result<u128, String> {
    let modified = fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .map_err(|error| format!("无法读取作业时间：{error}"))?;
    Ok(modified
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("作业时间无效：{error}"))?
        .as_millis())
}

fn existing_job_path(root: &Path, file: &str) -> Result<PathBuf, String> {
    let relative = Path::new(file);
    if relative.components().count() != 1
        || relative.file_name().and_then(|name| name.to_str()) != Some(file)
        || !file.ends_with(".json")
    {
        return Err("作业文件名无效".to_string());
    }
    Ok(root.join(relative))
}

pub fn list_jobs_at(root: &Path) -> Result<Vec<SavedJobEntry>, String> {
    fs::create_dir_all(root).map_err(|error| format!("无法创建 jobs 文件夹：{error}"))?;
    let entries = fs::read_dir(root).map_err(|error| format!("无法读取 jobs 文件夹：{error}"))?;
    let mut jobs = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|error| format!("无法读取作业条目：{error}"))?;
        let path = entry.path();
        if !path.is_file() || path.extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        let Ok(job) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let (Ok(saved_at), Some(file)) = (
            saved_at(&path),
            path.file_name().and_then(|name| name.to_str()),
        ) else {
            continue;
        };
        jobs.push(SavedJobEntry {
            file: file.to_string(),
            saved_at,
            job,
        });
    }
    jobs.sort_by(|left, right| right.saved_at.cmp(&left.saved_at));
    Ok(jobs)
}

pub fn read_job_at(root: &Path, file: &str) -> Result<Value, String> {
    let path = existing_job_path(root, file)?;
    let text = fs::read_to_string(path).map_err(|error| format!("无法读取作业：{error}"))?;
    serde_json::from_str(&text).map_err(|error| format!("作业 JSON 无效：{error}"))
}

pub fn save_job_at(root: &Path, name: &str, job: Value) -> Result<SaveJobResult, String> {
    if job.get("version").and_then(Value::as_u64) != Some(1) {
        return Err("作业格式无效".to_string());
    }
    fs::create_dir_all(root).map_err(|error| format!("无法创建 jobs 文件夹：{error}"))?;
    let file = format!("{}.json", sanitize_job_name(name));
    let path = existing_job_path(root, &file)?;
    let text =
        serde_json::to_string_pretty(&job).map_err(|error| format!("无法序列化作业：{error}"))?;
    fs::write(&path, text).map_err(|error| format!("无法保存作业：{error}"))?;
    Ok(SaveJobResult {
        file,
        saved_at: saved_at(&path)?,
    })
}

pub fn delete_job_at(root: &Path, file: &str) -> Result<(), String> {
    let path = existing_job_path(root, file)?;
    fs::remove_file(path).map_err(|error| format!("无法删除作业：{error}"))
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    use serde_json::json;

    use super::{delete_job_at, list_jobs_at, read_job_at, save_job_at};

    fn test_directory(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "hugofgo-storage-{name}-{}-{unique}",
            std::process::id()
        ))
    }

    #[test]
    fn saved_job_can_be_listed_read_and_deleted() {
        let root = test_directory("crud");
        let job = json!({ "version": 1, "name": "测试编队", "waves": [] });

        let saved = save_job_at(&root, "测试/编队: 一", job.clone()).unwrap();
        assert_eq!(saved.file, "测试编队 一.json");
        assert_eq!(read_job_at(&root, &saved.file).unwrap(), job);
        assert_eq!(list_jobs_at(&root).unwrap()[0].job, job);

        delete_job_at(&root, &saved.file).unwrap();
        assert!(list_jobs_at(&root).unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn job_paths_cannot_escape_the_jobs_directory() {
        let root = test_directory("paths");
        let job = json!({ "version": 1 });
        let saved = save_job_at(&root, "../outside", job).unwrap();
        assert_eq!(saved.file, "..outside.json");
        assert!(root.join(&saved.file).is_file());
        assert!(!root.parent().unwrap().join("outside.json").exists());

        let mut invalid = vec!["../outside.json", "nested/job.json", "job.txt"];
        if cfg!(windows) {
            invalid.push("..\\outside.json");
        }
        for file in invalid {
            assert!(read_job_at(&root, file).is_err(), "accepted {file}");
            assert!(delete_job_at(&root, file).is_err(), "accepted {file}");
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn only_version_one_jobs_can_be_saved() {
        let root = test_directory("version");
        assert!(save_job_at(&root, "invalid", json!({ "version": 2 })).is_err());
        assert!(!root.exists());
    }
}
