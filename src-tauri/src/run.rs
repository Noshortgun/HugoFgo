use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::settings::RunSettings;

const RUN_EVENT: &str = "run-event";
const STOP_GRACE: Duration = Duration::from_millis(750);

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) struct RunState {
    pub running: bool,
    pub run_id: String,
    pub serial: String,
    pub job_name: String,
    pub job_path: String,
    pub started_at_ms: u128,
    pub status: String,
    pub screen: String,
    pub wave: u32,
    pub turn: u32,
    pub action: String,
    pub completed_runs: u32,
    pub recovery_used: BTreeMap<String, u32>,
    pub log_dir: String,
    pub error: String,
    pub exit_code: Option<i32>,
}

impl Default for RunState {
    fn default() -> Self {
        Self {
            running: false,
            run_id: String::new(),
            serial: String::new(),
            job_name: String::new(),
            job_path: String::new(),
            started_at_ms: 0,
            status: "idle".to_string(),
            screen: String::new(),
            wave: 1,
            turn: 1,
            action: String::new(),
            completed_runs: 0,
            recovery_used: BTreeMap::new(),
            log_dir: String::new(),
            error: String::new(),
            exit_code: None,
        }
    }
}

#[derive(Debug)]
struct RunInner {
    child: Option<Arc<Mutex<Child>>>,
    state: RunState,
}

impl Default for RunInner {
    fn default() -> Self {
        Self {
            child: None,
            state: RunState::default(),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct RunManager {
    inner: Arc<Mutex<RunInner>>,
}

#[derive(Debug, Deserialize)]
struct IncomingEvent {
    #[serde(rename = "type")]
    kind: String,
    timestamp: f64,
    #[serde(default)]
    data: Value,
}

#[derive(Clone, Debug, Serialize)]
struct RunEvent {
    run_id: String,
    #[serde(rename = "type")]
    kind: String,
    timestamp: f64,
    data: Value,
}

impl RunEvent {
    fn new(run_id: &str, kind: &str, timestamp: f64, data: Value) -> Self {
        let data = if data.is_object() { data } else { json!({}) };
        Self {
            run_id: run_id.to_string(),
            kind: kind.to_string(),
            timestamp,
            data,
        }
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn python_path() -> String {
    if let Ok(configured) = env::var("HUGOFGO_PYTHON") {
        if !configured.trim().is_empty() {
            return configured;
        }
    }
    let local = project_root().join("cv").join(".venv");
    let candidate = if cfg!(windows) {
        local.join("Scripts").join("python.exe")
    } else {
        local.join("bin").join("python")
    };
    if candidate.is_file() {
        candidate.to_string_lossy().to_string()
    } else {
        "python".to_string()
    }
}

fn fake_requested() -> bool {
    matches!(env::var("HUGOFGO_FAKE_RUN").as_deref(), Ok("1") | Ok("true"))
}

fn executor_args(
    job: &Path,
    serial: &str,
    region: &str,
    run_dir: &Path,
    settings: &RunSettings,
    fake: bool,
) -> Vec<String> {
    let mut args = vec![
        "-m".to_string(),
        "executor".to_string(),
        job.to_string_lossy().to_string(),
        "--serial".to_string(),
        serial.to_string(),
        "--region".to_string(),
        region.to_string(),
        "--run-dir".to_string(),
        run_dir.to_string_lossy().to_string(),
        "--battles".to_string(),
        settings.battles.to_string(),
        "--recovery".to_string(),
    ];
    args.extend(
        settings
            .recovery
            .allowed
            .iter()
            .map(|item| item.as_str().to_string()),
    );
    if settings.recovery.drain_after_turns {
        args.push("--drain-after-turns".to_string());
    }
    if fake {
        args.push("--fake".to_string());
    }
    args
}

fn value_text(data: &Value, key: &str) -> Option<String> {
    data.get(key).and_then(Value::as_str).map(str::to_string)
}

fn value_u32(data: &Value, key: &str) -> Option<u32> {
    data.get(key)
        .and_then(Value::as_u64)
        .and_then(|value| u32::try_from(value).ok())
}

impl RunManager {
    fn snapshot(&self) -> Result<RunState, String> {
        self.inner
            .lock()
            .map(|inner| inner.state.clone())
            .map_err(|_| "运行状态锁不可用".to_string())
    }

    fn apply_event(&self, event: &RunEvent) {
        let Ok(mut inner) = self.inner.lock() else {
            return;
        };
        if inner.state.run_id != event.run_id {
            return;
        }
        let state = &mut inner.state;
        state.wave = value_u32(&event.data, "wave").unwrap_or(state.wave);
        state.turn = value_u32(&event.data, "turn").unwrap_or(state.turn);
        if let Some(directory) = value_text(&event.data, "run_dir")
            .or_else(|| value_text(&event.data, "evidence_dir"))
        {
            state.log_dir = directory;
        }
        match event.kind.as_str() {
            "state_changed" => {
                state.screen = value_text(&event.data, "state").unwrap_or_default();
            }
            "action" => {
                state.action = value_text(&event.data, "control").unwrap_or_default();
            }
            "recovery_used" => {
                if let (Some(item), Some(count)) = (
                    value_text(&event.data, "item"),
                    value_u32(&event.data, "count"),
                ) {
                    state.recovery_used.insert(item, count);
                }
            }
            "finished" => {
                state.running = false;
                state.status = "completed".to_string();
                state.completed_runs =
                    value_u32(&event.data, "completed_runs").unwrap_or(state.completed_runs);
            }
            "stopped" => {
                state.running = false;
                state.status = "stopped".to_string();
            }
            "failed" => {
                state.running = false;
                state.status = "failed".to_string();
                state.error = value_text(&event.data, "message")
                    .or_else(|| value_text(&event.data, "reason"))
                    .unwrap_or_else(|| "执行器报告失败".to_string());
                state.exit_code = value_u32(&event.data, "exit_code").map(|value| value as i32);
            }
            _ => {}
        }
    }

    fn start(
        &self,
        app: AppHandle,
        serial: String,
        job_path: String,
        settings: RunSettings,
        region: Option<String>,
    ) -> Result<RunState, String> {
        if settings.battles < 1 {
            return Err("连续出击场数必须至少为 1".to_string());
        }
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "运行状态锁不可用".to_string())?;
        if inner.child.is_some() {
            let message = if inner.state.job_name.is_empty() {
                "已有执行器进程正在运行或退出".to_string()
            } else {
                format!("已有运行中的作业：{}", inner.state.job_name)
            };
            return Err(message);
        }

        let app_data = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("无法定位应用数据目录：{error}"))?;
        let requested = PathBuf::from(&job_path);
        let candidate = if requested.is_absolute() {
            requested
        } else {
            let development = project_root().join(&requested);
            if development.is_file() {
                development
            } else {
                app_data.join(&requested)
            }
        };
        let job = fs::canonicalize(&candidate)
            .map_err(|error| format!("无法读取作业文件 {}：{error}", candidate.display()))?;
        if !job.is_file() {
            return Err(format!("作业路径不是文件：{}", job.display()));
        }
        let fake = fake_requested();
        let serial = if fake && serial.trim().is_empty() {
            "fake".to_string()
        } else {
            serial.trim().to_string()
        };
        if serial.is_empty() {
            return Err("请先在“设置与资源”中选择设备".to_string());
        }
        if !fake {
            crate::device::ensure_device(&serial)?;
        }
        let region = region.unwrap_or_else(|| "CN".to_string());
        let run_id = format!("run-{}-{}", now_ms(), std::process::id());
        let run_dir = app_data
            .join("logs")
            .join("farm")
            .join(&run_id);
        fs::create_dir_all(&run_dir).map_err(|error| format!("无法创建运行目录：{error}"))?;
        let job_name = job
            .file_stem()
            .and_then(|name| name.to_str())
            .unwrap_or("未命名作业")
            .to_string();
        let args = executor_args(&job, &serial, &region, &run_dir, &settings, fake);
        let root = project_root();
        let mut command = Command::new(python_path());
        command
            .args(args)
            .current_dir(&root)
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command
            .spawn()
            .map_err(|error| format!("无法启动 Python 执行器：{error}"))?;
        let stdout = child.stdout.take().ok_or_else(|| "无法读取执行器 stdout".to_string())?;
        let stderr = child.stderr.take().ok_or_else(|| "无法读取执行器 stderr".to_string())?;
        let child = Arc::new(Mutex::new(child));
        let state = RunState {
            running: true,
            run_id: run_id.clone(),
            serial,
            job_name,
            job_path: job.to_string_lossy().to_string(),
            started_at_ms: now_ms(),
            status: "running".to_string(),
            log_dir: run_dir.to_string_lossy().to_string(),
            ..RunState::default()
        };
        inner.state = state.clone();
        inner.child = Some(child.clone());
        drop(inner);

        let stderr_reader = thread::spawn(move || {
            let mut bytes = Vec::new();
            let mut reader = BufReader::new(stderr);
            let _ = reader.read_to_end(&mut bytes);
            String::from_utf8_lossy(&bytes).trim().to_string()
        });
        let manager = self.clone();
        let monitor_app = app.clone();
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut line = Vec::new();
                match reader.read_until(b'\n', &mut line) {
                    Ok(0) => break,
                    Ok(_) => {
                        while matches!(line.last(), Some(b'\n' | b'\r')) {
                            line.pop();
                        }
                        if line.is_empty() {
                            continue;
                        }
                        let event = match serde_json::from_slice::<IncomingEvent>(&line) {
                            Ok(incoming) => RunEvent::new(
                                &run_id,
                                &incoming.kind,
                                incoming.timestamp,
                                incoming.data,
                            ),
                            Err(error) => RunEvent::new(
                                &run_id,
                                "warning",
                                0.0,
                                json!({"reason": "invalid_executor_event", "message": error.to_string()}),
                            ),
                        };
                        manager.apply_event(&event);
                        let _ = monitor_app.emit(RUN_EVENT, &event);
                    }
                    Err(error) => {
                        let event = RunEvent::new(
                            &run_id,
                            "warning",
                            0.0,
                            json!({"reason": "stdout_read_error", "message": error.to_string()}),
                        );
                        let _ = monitor_app.emit(RUN_EVENT, &event);
                        break;
                    }
                }
            }
            let status = child
                .lock()
                .map_err(|_| "执行器进程锁不可用".to_string())
                .and_then(|mut child| {
                    child
                        .wait()
                        .map_err(|error| format!("无法等待执行器退出：{error}"))
                });
            let stderr = stderr_reader.join().unwrap_or_else(|_| "stderr 读取线程异常".to_string());
            manager.finish(&run_id, status, stderr, &monitor_app);
        });
        Ok(state)
    }

    fn finish(
        &self,
        run_id: &str,
        status: Result<std::process::ExitStatus, String>,
        stderr: String,
        app: &AppHandle,
    ) {
        let mut synthetic = None;
        if let Ok(mut inner) = self.inner.lock() {
            if inner.state.run_id != run_id {
                return;
            }
            inner.child = None;
            inner.state.running = false;
            match status {
                Ok(exit) => {
                    inner.state.exit_code = exit.code();
                    if inner.state.status == "stopping" {
                        inner.state.status = "stopped".to_string();
                    } else if exit.success() && inner.state.status != "completed" {
                        inner.state.status = "failed".to_string();
                        inner.state.error = "执行器已退出，但没有发送完成事件".to_string();
                    } else if !exit.success() && inner.state.status == "running" {
                        inner.state.status = "failed".to_string();
                        let detail = if stderr.is_empty() {
                            String::new()
                        } else {
                            format!("：{stderr}")
                        };
                        inner.state.error = format!(
                            "执行器异常退出（代码 {}）{detail}",
                            exit.code().map(|code| code.to_string()).unwrap_or_else(|| "未知".to_string())
                        );
                    }
                }
                Err(error) => {
                    inner.state.status = "failed".to_string();
                    inner.state.error = error;
                }
            }
            if inner.state.status == "failed" && !inner.state.error.is_empty() {
                synthetic = Some(RunEvent::new(
                    run_id,
                    "failed",
                    0.0,
                    json!({
                        "reason": "executor_exit",
                        "message": inner.state.error.clone(),
                        "exit_code": inner.state.exit_code,
                        "evidence_dir": inner.state.log_dir.clone(),
                    }),
                ));
            }
        }
        if let Some(event) = synthetic {
            let _ = app.emit(RUN_EVENT, &event);
        }
    }

    fn stop(&self) -> Result<RunState, String> {
        let (run_id, child) = {
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| "运行状态锁不可用".to_string())?;
            if !inner.state.running {
                return Ok(inner.state.clone());
            }
            inner.state.running = false;
            inner.state.status = "stopping".to_string();
            let child = inner
                .child
                .clone()
                .ok_or_else(|| "运行状态存在，但执行器进程句柄已丢失".to_string())?;
            (inner.state.run_id.clone(), child)
        };

        {
            let mut process = child
                .lock()
                .map_err(|_| "执行器进程锁不可用".to_string())?;
            if let Some(stdin) = process.stdin.as_mut() {
                let _ = stdin.write_all(b"stop\n");
                let _ = stdin.flush();
            }
            let deadline = Instant::now() + STOP_GRACE;
            let mut exited = process
                .try_wait()
                .map_err(|error| format!("无法检查执行器状态：{error}"))?
                .is_some();
            while !exited && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(25));
                exited = process
                    .try_wait()
                    .map_err(|error| format!("无法检查执行器状态：{error}"))?
                    .is_some();
            }
            if !exited {
                terminate_process_tree(&mut process)?;
                process
                    .wait()
                    .map_err(|error| format!("无法确认执行器已终止：{error}"))?;
            }
        }

        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "运行状态锁不可用".to_string())?;
        if inner.state.run_id == run_id {
            inner.state.running = false;
            inner.state.status = "stopped".to_string();
        }
        Ok(inner.state.clone())
    }
}

#[cfg(windows)]
fn terminate_process_tree(child: &mut Child) -> Result<(), String> {
    let pid = child.id();
    let result = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    if child
        .try_wait()
        .map_err(|error| format!("无法检查执行器终止状态：{error}"))?
        .is_none()
    {
        child
            .kill()
            .map_err(|error| format!("无法终止执行器：{error}；taskkill 结果：{result:?}"))?;
    }
    Ok(())
}

#[cfg(not(windows))]
fn terminate_process_tree(child: &mut Child) -> Result<(), String> {
    child
        .kill()
        .map_err(|error| format!("无法终止执行器：{error}"))
}

#[tauri::command]
pub(crate) fn start_run(
    app: AppHandle,
    manager: State<'_, RunManager>,
    serial: String,
    job_path: String,
    settings: RunSettings,
    region: Option<String>,
) -> Result<RunState, String> {
    manager.start(app, serial, job_path, settings, region)
}

#[tauri::command]
pub(crate) fn stop_run(manager: State<'_, RunManager>) -> Result<RunState, String> {
    manager.stop()
}

#[tauri::command]
pub(crate) fn get_run_state(manager: State<'_, RunManager>) -> Result<RunState, String> {
    manager.snapshot()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::{RecoveryItem, RecoveryPolicy};

    #[test]
    fn launch_args_include_job_device_run_directory_and_empty_whitelist() {
        let settings = RunSettings {
            battles: 3,
            recovery: RecoveryPolicy {
                allowed: vec![],
                drain_after_turns: true,
            },
        };
        let args = executor_args(
            Path::new("jobs/test.json"),
            "emulator-5554",
            "CN",
            Path::new("logs/farm/run-1"),
            &settings,
            false,
        );
        assert_eq!("3", args[args.iter().position(|arg| arg == "--battles").unwrap() + 1]);
        assert!(args.contains(&"--recovery".to_string()));
        assert!(!args.iter().any(|arg| arg == "bronze"));
        assert!(args.contains(&"--drain-after-turns".to_string()));
        assert!(!args.contains(&"--fake".to_string()));
    }

    #[test]
    fn launch_args_include_selected_recovery_and_fake_mode() {
        let settings = RunSettings {
            battles: 1,
            recovery: RecoveryPolicy {
                allowed: vec![RecoveryItem::Copper, RecoveryItem::Gold],
                drain_after_turns: false,
            },
        };
        let args = executor_args(
            Path::new("job.json"),
            "fake",
            "CN",
            Path::new("run"),
            &settings,
            true,
        );
        assert!(args.windows(2).any(|pair| pair == ["--recovery", "copper"]));
        assert!(args.contains(&"gold".to_string()));
        assert!(args.contains(&"--fake".to_string()));
    }
}
