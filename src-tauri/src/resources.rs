use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::thread;

use serde::Serialize;
use tauri::http::{header::CONTENT_TYPE, Response, StatusCode};
use tauri::{AppHandle, Emitter, Manager, UriSchemeContext, Wry};

const REGIONS: [&str; 3] = ["CN", "JP", "TW"];
pub const OUTPUT_EVENT: &str = "resource-task-output";

#[derive(Default)]
pub struct ResourceState {
    active: Mutex<Option<String>>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResourceTaskEvent {
    pub region: String,
    pub action: String,
    pub stream: String,
    pub text: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResourceTaskResult {
    pub success: bool,
    pub code: Option<i32>,
    pub output: String,
}

fn valid_region(region: &str) -> Result<(), String> {
    if REGIONS.contains(&region) {
        Ok(())
    } else {
        Err(format!("不支持的区服：{region}；可用区服为 CN / JP / TW"))
    }
}

fn data_root(app: &AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        return Ok(crate::paths::project_root().join("data"));
    }
    app.path()
        .app_data_dir()
        .map(|directory| directory.join("data"))
        .map_err(|error| format!("无法定位应用数据目录：{error}"))
}

fn script_root(app: &AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        return Ok(crate::paths::project_root());
    }
    app.path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录：{error}"))
}

fn node_path(root: &Path) -> PathBuf {
    if cfg!(debug_assertions) {
        return PathBuf::from(std::env::var("HUGOFGO_NODE").unwrap_or_else(|_| "node".to_string()));
    }
    root.join("runtime").join("node").join("node.exe")
}

fn emit_output(app: &AppHandle, region: &str, action: &str, stream: &str, text: String) {
    let _ = app.emit(
        OUTPUT_EVENT,
        ResourceTaskEvent {
            region: region.to_string(),
            action: action.to_string(),
            stream: stream.to_string(),
            text,
        },
    );
}

fn read_output<R: BufRead>(
    reader: R,
    app: AppHandle,
    region: String,
    action: String,
    stream: &'static str,
) -> String {
    let mut collected = String::new();
    for line in reader.lines().map_while(Result::ok) {
        collected.push_str(&line);
        collected.push('\n');
        emit_output(&app, &region, &action, stream, format!("{line}\n"));
    }
    collected
}

pub fn run_task(
    app: &AppHandle,
    state: &ResourceState,
    action: &str,
    region: &str,
    refresh: bool,
) -> Result<ResourceTaskResult, String> {
    valid_region(region)?;
    if !matches!(action, "check" | "update" | "verify") {
        return Err(format!("不支持的资源操作：{action}"));
    }

    let root = script_root(app)?;
    let script = root.join("scripts/resources.mjs");
    if !script.is_file() {
        return Err(format!("找不到资源脚本：{}", script.display()));
    }

    let node = node_path(&root);
    let mut command = Command::new(&node);
    command
        .arg(&script)
        .arg(action)
        .arg("--region")
        .arg(region)
        .current_dir(&root)
        .env("HUGOFGO_DATA_ROOT", data_root(app)?)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if action == "update" && refresh {
        command.arg("--refresh-metadata");
    }

    {
        let mut active = state
            .active
            .lock()
            .map_err(|_| "资源任务状态锁不可用".to_string())?;
        if active.is_some() {
            return Err("已有资源任务正在执行".to_string());
        }
        *active = Some(format!("{region}:{action}"));
    }

    let result = (|| {
        let mut child = command
            .spawn()
            .map_err(|error| format!("无法启动资源运行时 {}：{error}；请重新安装 HugoFgo", node.display()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "无法读取资源任务输出".to_string())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "无法读取资源任务错误输出".to_string())?;
        let stdout_handle = {
            let app = app.clone();
            let region = region.to_string();
            let action = action.to_string();
            thread::spawn(move || {
                read_output(BufReader::new(stdout), app, region, action, "stdout")
            })
        };
        let stderr_handle = {
            let app = app.clone();
            let region = region.to_string();
            let action = action.to_string();
            thread::spawn(move || {
                read_output(BufReader::new(stderr), app, region, action, "stderr")
            })
        };

        let status = child
            .wait()
            .map_err(|error| format!("等待资源任务失败：{error}"))?;
        let mut output = stdout_handle.join().unwrap_or_default();
        output.push_str(&stderr_handle.join().unwrap_or_default());
        Ok(ResourceTaskResult {
            success: status.success(),
            code: status.code(),
            output,
        })
    })();

    if let Ok(mut active) = state.active.lock() {
        *active = None;
    }
    result
}

fn percent_decode(text: &str) -> Result<String, String> {
    let bytes = text.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' {
            if index + 2 >= bytes.len() {
                return Err("资源路径编码无效".to_string());
            }
            let hex = std::str::from_utf8(&bytes[index + 1..index + 3])
                .map_err(|_| "资源路径编码无效".to_string())?;
            let value = u8::from_str_radix(hex, 16).map_err(|_| "资源路径编码无效".to_string())?;
            decoded.push(value);
            index += 3;
        } else {
            decoded.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8(decoded).map_err(|_| "资源路径不是有效 UTF-8".to_string())
}

fn content_type(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "json" => "application/json; charset=utf-8",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        _ => "application/octet-stream",
    }
}

fn resource_target(root: &Path, request_path: &str) -> Result<PathBuf, String> {
    let decoded = percent_decode(request_path.trim_start_matches('/'))?;
    let (region, relative) = decoded
        .split_once('/')
        .ok_or_else(|| "资源路径缺少区服".to_string())?;
    valid_region(region)?;
    let relative = Path::new(relative);
    if relative
        .components()
        .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err("资源路径无效".to_string());
    }
    Ok(root.join(region).join(relative))
}

fn read_resource(root: &Path, request_path: &str) -> Result<(Vec<u8>, &'static str), String> {
    let path = resource_target(root, request_path)?;
    let bytes =
        fs::read(&path).map_err(|error| format!("无法读取资源 {}：{error}", path.display()))?;
    Ok((bytes, content_type(&path)))
}

pub fn protocol_response(
    context: UriSchemeContext<'_, Wry>,
    request: tauri::http::Request<Vec<u8>>,
) -> Response<Vec<u8>> {
    let app = context.app_handle();
    let result = data_root(app)
        .and_then(|root| read_resource(&root, request.uri().path()))
        .and_then(|(bytes, mime)| {
            Response::builder()
                .status(StatusCode::OK)
                .header(CONTENT_TYPE, mime)
                .header("Access-Control-Allow-Origin", "*")
                .body(bytes)
                .map_err(|error| error.to_string())
        });
    match result {
        Ok(response) => response,
        Err(error) => Response::builder()
            .status(StatusCode::NOT_FOUND)
            .header(CONTENT_TYPE, "text/plain; charset=utf-8")
            .body(error.into_bytes())
            .expect("static response is valid"),
    }
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::{content_type, percent_decode, resource_target, valid_region};

    #[test]
    fn resource_regions_are_explicitly_limited() {
        assert!(valid_region("CN").is_ok());
        assert!(valid_region("JP").is_ok());
        assert!(valid_region("TW").is_ok());
        assert!(valid_region("../CN").is_err());
        assert!(valid_region("KR").is_err());
    }

    #[test]
    fn resource_paths_are_decoded_and_typed() {
        assert_eq!(
            percent_decode("CN/assets/%E4%B8%AD.png").unwrap(),
            "CN/assets/中.png"
        );
        assert_eq!(
            content_type(Path::new("index.json")),
            "application/json; charset=utf-8"
        );
        assert_eq!(content_type(Path::new("face.PNG")), "image/png");
    }

    #[test]
    fn resource_paths_cannot_escape_the_region_directory() {
        let root = Path::new("T:/hugofgo-data");
        assert_eq!(
            resource_target(root, "/CN/assets/atlas/index.json").unwrap(),
            root.join("CN")
                .join("assets")
                .join("atlas")
                .join("index.json")
        );
        assert!(resource_target(root, "/CN/../outside.png").is_err());
        assert!(resource_target(root, "/CN/%2e%2e/outside.png").is_err());
        assert!(resource_target(root, "/KR/assets/atlas/index.json").is_err());
        assert!(resource_target(root, "/CN").is_err());
    }
}
