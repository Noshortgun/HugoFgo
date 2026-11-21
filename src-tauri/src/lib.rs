mod device;
mod run;
mod settings;

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
        .manage(run::RunManager::default())
        .invoke_handler(tauri::generate_handler![
            list_devices,
            get_device_info,
            capture_frame,
            tap_normalized,
            run::start_run,
            run::stop_run,
            run::get_run_state,
            settings::get_run_settings,
            settings::save_run_settings
        ])
        .run(tauri::generate_context!())
        .expect("error while running HugoFgo");
}
