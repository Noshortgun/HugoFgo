mod device;
mod settings;

use std::sync::Mutex;

use tauri::State;

#[derive(Default)]
struct AppState {
    run: Mutex<Option<device::RunStatus>>,
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

/// 第一版只登记运行状态；真正的战斗循环在后续执行器阶段接入。
#[tauri::command]
fn start_run(
    serial: String,
    job_name: String,
    state: State<'_, AppState>,
) -> Result<device::RunStatus, String> {
    let mut run = state
        .run
        .lock()
        .map_err(|_| "运行状态锁不可用".to_string())?;

    if let Some(current) = run.as_ref() {
        if current.running {
            return Err(format!("已有运行中的作业：{}", current.job_name));
        }
    }

    device::ensure_device(&serial)?;
    let status = device::RunStatus::started(&serial, &job_name);
    *run = Some(status.clone());
    Ok(status)
}

#[tauri::command]
fn stop_run(state: State<'_, AppState>) -> Result<device::RunStatus, String> {
    let mut run = state
        .run
        .lock()
        .map_err(|_| "运行状态锁不可用".to_string())?;

    let status = run.take().unwrap_or_else(device::RunStatus::idle);
    Ok(status.stopped())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            list_devices,
            get_device_info,
            capture_frame,
            tap_normalized,
            start_run,
            stop_run,
            settings::get_run_settings,
            settings::save_run_settings
        ])
        .run(tauri::generate_context!())
        .expect("error while running HugoFgo");
}
