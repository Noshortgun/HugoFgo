mod device;
mod history;
mod resources;
mod run;
mod settings;
mod storage;

use std::path::PathBuf;

use serde_json::Value;
use tauri::{AppHandle, Manager, State};

#[derive(Default)]
struct AppState {
    resources: resources::ResourceState,
}

fn development_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must have a project parent")
        .to_path_buf()
}

fn jobs_directory(app: &AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        return Ok(development_root().join("jobs"));
    }
    app.path()
        .app_data_dir()
        .map(|directory| directory.join("jobs"))
        .map_err(|error| format!("无法定位应用数据目录：{error}"))
}

#[tauri::command]
fn list_jobs(app: AppHandle) -> Result<Vec<storage::SavedJobEntry>, String> {
    storage::list_jobs_at(&jobs_directory(&app)?)
}

#[tauri::command]
fn read_job(app: AppHandle, file: String) -> Result<Value, String> {
    storage::read_job_at(&jobs_directory(&app)?, &file)
}

#[tauri::command]
fn save_job(app: AppHandle, name: String, job: Value) -> Result<storage::SaveJobResult, String> {
    storage::save_job_at(&jobs_directory(&app)?, &name, job)
}

#[tauri::command]
fn delete_job(app: AppHandle, file: String) -> Result<(), String> {
    storage::delete_job_at(&jobs_directory(&app)?, &file)
}

#[tauri::command]
fn check_resources(
    app: AppHandle,
    state: State<'_, AppState>,
    region: String,
) -> Result<resources::ResourceTaskResult, String> {
    resources::run_task(&app, &state.resources, "check", &region, false)
}

#[tauri::command]
fn update_resources(
    app: AppHandle,
    state: State<'_, AppState>,
    region: String,
    refresh: bool,
) -> Result<resources::ResourceTaskResult, String> {
    resources::run_task(&app, &state.resources, "update", &region, refresh)
}

#[tauri::command]
fn verify_resources(
    app: AppHandle,
    state: State<'_, AppState>,
    region: String,
) -> Result<resources::ResourceTaskResult, String> {
    resources::run_task(&app, &state.resources, "verify", &region, false)
}

#[tauri::command]
fn list_devices() -> Result<Vec<device::DeviceInfo>, String> {
    device::list_devices()
}

#[tauri::command]
fn get_device_info(serial: String) -> Result<device::DeviceMetrics, String> {
    device::get_device_info(&serial)
}

#[tauri::command]
fn capture_frame(serial: String) -> Result<device::CapturedFrame, String> {
    device::capture_frame(&serial)
}

#[tauri::command]
fn tap_normalized(
    serial: String,
    x: f64,
    y: f64,
    width: Option<u32>,
    height: Option<u32>,
) -> Result<device::TapResult, String> {
    device::tap_normalized(&serial, x, y, width, height)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .register_uri_scheme_protocol("hugofgo-data", resources::protocol_response)
        .manage(AppState::default())
        .manage(run::RunManager::default())
        .invoke_handler(tauri::generate_handler![
            list_devices,
            get_device_info,
            capture_frame,
            tap_normalized,
            run::start_run,
            run::stop_run,
            run::get_run_state,
            history::list_run_history,
            history::get_run_record,
            history::open_run_artifacts,
            list_jobs,
            read_job,
            save_job,
            delete_job,
            check_resources,
            update_resources,
            verify_resources,
            settings::get_run_settings,
            settings::save_run_settings
        ])
        .run(tauri::generate_context!())
        .expect("error while running HugoFgo");
}
