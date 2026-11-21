use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RunRecord {
    pub id: String,
    pub job_name: String,
    pub job_file: String,
    pub serial: String,
    pub region: String,
    pub started_at: u64,
    pub finished_at: u64,
    pub duration_ms: u64,
    pub result: String,
    #[serde(default)]
    pub reason: String,
    pub battles_completed: u32,
    pub recovery_used: BTreeMap<String, u32>,
    pub run_dir: String,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RunArtifacts {
    pub directory: String,
    pub log_file: String,
    pub screenshots: Vec<String>,
}

fn history_path(app: &AppHandle) -> Result<PathBuf, String> {
    use tauri::Manager;

    app.path()
        .app_data_dir()
        .map(|directory| directory.join("logs").join("run-history.jsonl"))
        .map_err(|error| format!("无法定位应用数据目录：{error}"))
}

pub(crate) fn list_run_history_at(path: &Path, limit: Option<usize>) -> Result<Vec<RunRecord>, String> {
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
        Err(error) => return Err(format!("无法读取运行记录：{error}")),
    };
    let mut records = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<RunRecord>(line).map_err(|error| format!("运行记录格式无效：{error}")))
        .collect::<Result<Vec<_>, _>>()?;
    records.reverse();
    if let Some(limit) = limit {
        records.truncate(limit.min(500));
    }
    Ok(records)
}

pub(crate) fn get_run_record_at(path: &Path, id: &str) -> Result<RunRecord, String> {
    list_run_history_at(path, None)?
        .into_iter()
        .find(|record| record.id == id)
        .ok_or_else(|| "找不到这条运行记录".to_string())
}

pub(crate) fn run_artifacts_at(path: &Path, id: &str) -> Result<RunArtifacts, String> {
    let record = get_run_record_at(path, id)?;
    let directory = PathBuf::from(record.run_dir);
    if !directory.is_dir() {
        return Err(format!("运行证据目录不存在：{}", directory.display()));
    }
    let screenshots = ["failure.png", "failure.bgr24"]
        .iter()
        .map(|name| directory.join(name))
        .filter(|path| path.is_file())
        .map(|path| path.to_string_lossy().to_string())
        .collect();
    Ok(RunArtifacts {
        log_file: directory.join("events.jsonl").to_string_lossy().to_string(),
        directory: directory.to_string_lossy().to_string(),
        screenshots,
    })
}

#[tauri::command]
pub(crate) fn list_run_history(app: AppHandle, limit: Option<usize>) -> Result<Vec<RunRecord>, String> {
    list_run_history_at(&history_path(&app)?, Some(limit.unwrap_or(100)))
}

#[tauri::command]
pub(crate) fn get_run_record(app: AppHandle, id: String) -> Result<RunRecord, String> {
    get_run_record_at(&history_path(&app)?, &id)
}

#[tauri::command]
pub(crate) fn open_run_artifacts(app: AppHandle, id: String) -> Result<RunArtifacts, String> {
    run_artifacts_at(&history_path(&app)?, &id)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    use serde_json::json;

    use super::{get_run_record_at, list_run_history_at, run_artifacts_at};

    fn test_directory(name: &str) -> PathBuf {
        let unique = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        std::env::temp_dir().join(format!("hugofgo-history-{name}-{}-{unique}", std::process::id()))
    }

    fn write_record(root: &PathBuf, id: &str, result: &str) {
        fs::create_dir_all(root.join("logs/farm").join(id)).unwrap();
        let record = json!({
            "id": id, "jobName": "测试作业", "jobFile": "jobs/test.json",
            "serial": "emulator-5554", "region": "CN", "startedAt": 1000,
            "finishedAt": 2500, "durationMs": 1500, "result": result,
            "reason": result, "battlesCompleted": 2, "recoveryUsed": {"bronze": 1},
            "runDir": root.join("logs/farm").join(id).to_string_lossy(), "error": null
        });
        let history = root.join("logs/run-history.jsonl");
        fs::create_dir_all(history.parent().unwrap()).unwrap();
        let line = serde_json::to_string(&record).unwrap() + "\n";
        use std::io::Write;
        fs::OpenOptions::new().create(true).append(true).open(history).unwrap().write_all(line.as_bytes()).unwrap();
    }

    #[test]
    fn history_survives_reload_and_is_returned_newest_first_with_a_limit() {
        let root = test_directory("reload");
        let path = root.join("logs/run-history.jsonl");
        write_record(&root, "run-old", "completed");
        write_record(&root, "run-new", "stopped");

        let records = list_run_history_at(&path, Some(1)).unwrap();
        assert_eq!(1, records.len());
        assert_eq!("run-new", records[0].id);
        assert_eq!("stopped", get_run_record_at(&path, "run-new").unwrap().result);
        assert_eq!(1, get_run_record_at(&path, "run-old").unwrap().recovery_used["bronze"]);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_history_is_empty_and_artifacts_include_real_failure_evidence() {
        let root = test_directory("artifacts");
        let path = root.join("logs/run-history.jsonl");
        assert!(list_run_history_at(&path, None).unwrap().is_empty());
        write_record(&root, "run-failed", "failed");
        let run_dir = root.join("logs/farm/run-failed");
        fs::write(run_dir.join("events.jsonl"), "{}\n").unwrap();
        fs::write(run_dir.join("failure.png"), "frame").unwrap();

        let artifacts = run_artifacts_at(&path, "run-failed").unwrap();
        assert_eq!(run_dir.join("events.jsonl").to_string_lossy().into_owned(), artifacts.log_file);
        assert_eq!(vec![run_dir.join("failure.png").to_string_lossy().into_owned()], artifacts.screenshots);
        fs::remove_dir_all(root).unwrap();
    }
}
