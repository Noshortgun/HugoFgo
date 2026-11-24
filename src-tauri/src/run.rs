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

use crate::{paths::project_root, settings::RunSettings};

const RUN_EVENT: &str = "run-event";
const STOP_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RunPhase {
    #[default]
    Idle,
    Running,
    Stopping,
    Completed,
    Stopped,
    Failed,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) struct RunState {
    pub running: bool,
    pub run_id: String,
    pub serial: String,
    pub job_name: String,
    pub job_path: String,
    pub started_at_ms: u128,
    pub status: RunPhase,
    pub screen: String,
    pub wave: u32,
    pub turn: u32,
    pub action: String,
    pub completed_runs: u32,
    pub recovery_used: BTreeMap<String, u32>,
    pub log_dir: String,
    pub error: String,
    pub notice: String,
    pub channel: String,
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
            status: RunPhase::Idle,
            screen: String::new(),
            wave: 1,
            turn: 1,
            action: String::new(),
            completed_runs: 0,
            recovery_used: BTreeMap::new(),
            log_dir: String::new(),
            error: String::new(),
            notice: String::new(),
            channel: "scrcpy".to_string(),
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

fn python_path(app: &AppHandle) -> Result<PathBuf, String> {
    if !cfg!(debug_assertions) {
        let bundled = app
            .path()
            .resource_dir()
            .map_err(|error| format!("无法定位应用资源目录：{error}"))?
            .join("runtime")
            .join("python")
            .join("python.exe");
        if bundled.is_file() {
            return Ok(bundled);
        }
    }
    if let Ok(configured) = env::var("HUGOFGO_PYTHON") {
        if !configured.trim().is_empty() {
            return Ok(PathBuf::from(configured));
        }
    }
    let local = project_root().join("cv").join(".venv");
    let candidate = if cfg!(windows) {
        local.join("Scripts").join("python.exe")
    } else {
        local.join("bin").join("python")
    };
    if candidate.is_file() {
        Ok(candidate)
    } else {
        Ok(PathBuf::from("python"))
    }
}

fn executor_root(app: &AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        return Ok(project_root());
    }
    app.path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录：{error}"))
}

fn fake_requested() -> bool {
    matches!(env::var("HUGOFGO_FAKE_RUN").as_deref(), Ok("1") | Ok("true"))
}

#[derive(Clone, Debug, Serialize)]
pub(crate) struct JobDiagnostic {
    pub path: String,
    pub message: String,
    pub wave: Option<u32>,
    pub turn: Option<u32>,
}

#[derive(Clone, Debug, Serialize)]
pub(crate) struct JobValidation {
    pub ok: bool,
    pub summary: String,
    pub diagnostics: Vec<JobDiagnostic>,
}

fn resolve_job_path(app: &AppHandle, job_path: &str) -> Result<PathBuf, String> {
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位应用数据目录：{error}"))?;
    let requested = PathBuf::from(job_path);
    let candidate = if requested.is_absolute() {
        requested
    } else if cfg!(debug_assertions) && project_root().join(&requested).is_file() {
        project_root().join(&requested)
    } else {
        app_data.join(&requested)
    };
    let job = fs::canonicalize(&candidate)
        .map_err(|error| format!("无法读取作业文件 {}：{error}", candidate.display()))?;
    if !job.is_file() {
        return Err(format!("作业路径不是文件：{}", job.display()));
    }
    Ok(job)
}

fn parse_job_diagnostic(line: &str) -> Option<JobDiagnostic> {
    let rest = line.trim().strip_prefix("- [")?;
    let (location, rest) = rest.split_once("] ")?;
    let (path, message) = rest.split_once(": ")?;
    let wave = location
        .split("第 ")
        .nth(1)
        .and_then(|value| value.split(' ').next())
        .and_then(|value| value.parse::<u32>().ok());
    let turn = location
        .split("回合 ")
        .nth(1)
        .and_then(|value| value.trim().parse::<u32>().ok());
    Some(JobDiagnostic {
        path: path.to_string(),
        message: message.to_string(),
        wave,
        turn,
    })
}

#[tauri::command]
pub(crate) fn validate_job(app: AppHandle, job_path: String) -> Result<JobValidation, String> {
    let job = resolve_job_path(&app, &job_path)?;
    let root = executor_root(&app)?;
    let python = python_path(&app)?;
    let output = Command::new(&python)
        .args(["-m", "executor.domain", &job.to_string_lossy()])
        .current_dir(&root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
        .map_err(|error| format!("无法启动作业检查器 {}：{error}", python.display()))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if output.status.success() {
        return Ok(JobValidation {
            ok: true,
            summary: stdout,
            diagnostics: Vec::new(),
        });
    }
    let diagnostics = stderr
        .lines()
        .filter_map(parse_job_diagnostic)
        .collect::<Vec<_>>();
    let summary = if diagnostics.is_empty() {
        if stderr.is_empty() { stdout } else { stderr }
    } else {
        format!("作业不可执行，共 {} 个问题", diagnostics.len())
    };
    Ok(JobValidation {
        ok: false,
        summary,
        diagnostics,
    })
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
    args.push("--channel".to_string());
    args.push(settings.device_channel.as_str().to_string());
    if settings.allow_adb_fallback && settings.device_channel == crate::settings::DeviceChannel::Scrcpy {
        args.push("--allow-adb-fallback".to_string());
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
            "run_started" => {
                state.channel = value_text(&event.data, "channel").unwrap_or(state.channel.clone());
            }
            "channel_changed" => {
                state.channel = value_text(&event.data, "to").unwrap_or(state.channel.clone());
                state.notice = value_text(&event.data, "message").unwrap_or(state.notice.clone());
            }
            "warning" => {
                state.notice = value_text(&event.data, "message").unwrap_or(state.notice.clone());
            }
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
            "battle_completed" => {
                state.completed_runs =
                    value_u32(&event.data, "completed_runs").unwrap_or(state.completed_runs);
            }
            "finished" => {
                state.running = false;
                state.status = RunPhase::Completed;
                state.completed_runs =
                    value_u32(&event.data, "completed_runs").unwrap_or(state.completed_runs);
            }
            "stopped" => {
                state.running = false;
                state.status = RunPhase::Stopped;
            }
            "failed" => {
                state.running = false;
                state.status = RunPhase::Failed;
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
        region: String,
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
        let job = resolve_job_path(&app, &job_path)?;
        let fake = fake_requested();
        let serial = if fake && serial.trim().is_empty() {
            "fake".to_string()
        } else {
            serial.trim().to_string()
        };
        if serial.is_empty() {
            return Err("请先在“准备”中选择设备".to_string());
        }
        if !fake {
            crate::device::ensure_device(&serial)?;
        }
        let region = region.trim().to_string();
        if region != "CN" {
            return Err("执行器当前仅支持 CN；请在“准备”中选择国服后再运行".to_string());
        }
        let run_id = format!("run-{}-{}", now_ms(), std::process::id());
        let log_root = if cfg!(debug_assertions) {
            project_root().join("logs")
        } else {
            app_data.join("logs")
        };
        let run_dir = log_root.join("farm").join(&run_id);
        fs::create_dir_all(&run_dir).map_err(|error| format!("无法创建运行目录：{error}"))?;
        let job_name = job
            .file_stem()
            .and_then(|name| name.to_str())
            .unwrap_or("未命名作业")
            .to_string();
        let args = executor_args(&job, &serial, &region, &run_dir, &settings, fake);
        let root = executor_root(&app)?;
        if !root.join("executor").join("__main__.py").is_file() {
            return Err(format!("找不到随包执行器：{}；请重新安装 HugoFgo", root.display()));
        }
        let python = python_path(&app)?;
        let requirements = root.join("cv").join("requirements.txt");
        let data_root = if cfg!(debug_assertions) {
            project_root().join("data")
        } else {
            app_data.join("data")
        };
        let mut command = Command::new(&python);
        command
            .args(args)
            .current_dir(&root)
            .env("HUGOFGO_DATA_ROOT", data_root)
            .env("HUGOFGO_REGION", &region)
            .env("HUGOFGO_SCRCPY_RESOURCE_DIR", root.join("resources").join("scrcpy"))
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command
            .spawn()
            .map_err(|error| format!("无法启动 Python 执行器 {}：{error}；请安装 Python 3.12 并运行 py -m pip install -r \"{}\"，或用 HUGOFGO_PYTHON 指定解释器", python.display(), requirements.display()))?;
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
            status: RunPhase::Running,
            channel: settings.device_channel.as_str().to_string(),
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
                    if inner.state.status == RunPhase::Stopping {
                        inner.state.status = RunPhase::Stopped;
                    } else if exit.success() && inner.state.status != RunPhase::Completed {
                        inner.state.status = RunPhase::Failed;
                        inner.state.error = "执行器已退出，但没有发送完成事件".to_string();
                    } else if !exit.success() && inner.state.status == RunPhase::Running {
                        inner.state.status = RunPhase::Failed;
                        let mut detail = if stderr.is_empty() {
                            String::new()
                        } else {
                            format!("：{stderr}")
                        };
                        if stderr.contains("No module named") || stderr.contains("Python was not found") {
                            detail.push_str("；请按 README 安装 cv/requirements.txt 中的依赖，或用 HUGOFGO_PYTHON 指定可用解释器");
                        }
                        inner.state.error = format!(
                            "执行器异常退出（代码 {}）{detail}",
                            exit.code().map(|code| code.to_string()).unwrap_or_else(|| "未知".to_string())
                        );
                    }
                }
                Err(error) => {
                    inner.state.status = RunPhase::Failed;
                    inner.state.error = error;
                }
            }
            if inner.state.status == RunPhase::Failed && !inner.state.error.is_empty() {
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
            inner.state.status = RunPhase::Stopping;
            let child = inner
                .child
                .clone()
                .ok_or_else(|| "运行状态存在，但执行器进程句柄已丢失".to_string())?;
            (inner.state.run_id.clone(), child)
        };

        let mut stop_error = None;
        {
            let mut process = child
                .lock()
                .map_err(|_| "执行器进程锁不可用".to_string())?;
            if let Some(stdin) = process.stdin.as_mut() {
                let _ = stdin.write_all(b"stop\n");
                let _ = stdin.flush();
            }
            let deadline = Instant::now() + STOP_TIMEOUT;
            let mut exited = process
                .try_wait()
                .map_err(|error| format!("无法检查执行器状态：{error}"))?
                .is_some();
            let mut terminal = false;
            while !terminal && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(25));
                if !exited {
                    exited = process
                        .try_wait()
                        .map_err(|error| format!("无法检查执行器状态：{error}"))?
                        .is_some();
                }
                terminal = self.inner.lock().map(|inner| {
                    inner.state.run_id == run_id
                        && matches!(inner.state.status, RunPhase::Completed | RunPhase::Stopped | RunPhase::Failed)
                }).unwrap_or(false);
            }
            if !terminal {
                if exited {
                    eprintln!("执行器退出但未发送终端事件；停止流程异常");
                    stop_error = Some("执行器退出时未发送终端事件；请查看运行日志".to_string());
                } else {
                    eprintln!("执行器超过 30 秒未发送终端事件；强制终止进程树（异常停止）");
                    terminate_process_tree(&mut process)?;
                    process
                        .wait()
                        .map_err(|error| format!("无法确认执行器已终止：{error}"))?;
                    stop_error = Some("执行器未在 30 秒内确认安全停止，已强制终止；请查看运行日志".to_string());
                }
            }
        }

        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "运行状态锁不可用".to_string())?;
        if inner.state.run_id == run_id {
            inner.state.running = false;
            if inner.state.status == RunPhase::Stopping {
                inner.state.status = RunPhase::Stopped;
            }
            if let Some(error) = stop_error {
                inner.state.error = error;
            }
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
    region: String,
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
            device_channel: crate::settings::DeviceChannel::Adb,
            allow_adb_fallback: false,
            ..RunSettings::default()
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
        assert!(args.windows(2).any(|pair| pair == ["--channel", "adb"]));
        assert!(!args.contains(&"--allow-adb-fallback".to_string()));
    }

    #[test]
    fn launch_args_include_selected_recovery_and_fake_mode() {
        let settings = RunSettings {
            battles: 1,
            recovery: RecoveryPolicy {
                allowed: vec![RecoveryItem::Copper, RecoveryItem::Gold],
                drain_after_turns: false,
            },
            ..RunSettings::default()
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

    #[test]
    fn channel_fallback_remains_visible_in_run_state() {
        let manager = RunManager::default();
        manager.inner.lock().unwrap().state.run_id = "run-1".to_string();
        manager.apply_event(&RunEvent::new("run-1", "run_started", 1.0,
                                           json!({"channel": "scrcpy"})));
        manager.apply_event(&RunEvent::new("run-1", "channel_changed", 2.0,
                                           json!({"from": "scrcpy", "to": "adb", "message": "用户已确认回退"})));
        let state = manager.snapshot().unwrap();
        assert_eq!(state.channel, "adb");
        assert_eq!(state.notice, "用户已确认回退");
    }

    #[test]
    fn launch_args_pass_explicit_scrcpy_and_confirmed_fallback() {
        let settings = RunSettings {
            device_channel: crate::settings::DeviceChannel::Scrcpy,
            allow_adb_fallback: true,
            ..RunSettings::default()
        };
        let args = executor_args(Path::new("job.json"), "emulator-5554", "CN", Path::new("run"),
                                 &settings, false);
        assert!(args.windows(2).any(|pair| pair == ["--channel", "scrcpy"]));
        assert!(args.contains(&"--allow-adb-fallback".to_string()));
    }

    #[test]
    fn job_domain_diagnostic_is_parsed_with_wave_and_turn() {
        let diagnostic = parse_job_diagnostic(
            "- [第 2 面 · 回合 3] $.waves[1].turns[2].attack.cards: 固定卡序需要三张卡",
        )
        .unwrap();
        assert_eq!(diagnostic.wave, Some(2));
        assert_eq!(diagnostic.turn, Some(3));
        assert_eq!(diagnostic.path, "$.waves[1].turns[2].attack.cards");
        assert_eq!(diagnostic.message, "固定卡序需要三张卡");
    }
}
