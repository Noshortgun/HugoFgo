use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub serial: String,
    pub state: String,
    pub model: Option<String>,
    pub product: Option<String>,
    pub device: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DeviceMetrics {
    pub serial: String,
    pub width: u32,
    pub height: u32,
    pub density: Option<u32>,
    pub model: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CapturedFrame {
    pub base64: String,
    pub path: String,
    pub width: u32,
    pub height: u32,
    pub captured_at_ms: u128,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TapResult {
    pub serial: String,
    pub x: u32,
    pub y: u32,
    pub norm_x: f64,
    pub norm_y: f64,
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn command_exists(binary: &str) -> bool {
    if Path::new(binary).is_file() {
        return true;
    }
    let checker = if cfg!(windows) { "where" } else { "which" };
    Command::new(checker)
        .arg(binary)
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

/// 优先使用 HUGOFGO_ADB，其次查找 PATH 与常见模拟器/Android SDK 路径。
pub fn adb_path() -> Result<String, String> {
    if let Ok(configured) = env::var("HUGOFGO_ADB") {
        if !configured.trim().is_empty() {
            return Ok(configured);
        }
    }

    let local_app_data = env::var("LOCALAPPDATA").unwrap_or_default();
    let candidates = [
        "adb".to_string(),
        format!("{local_app_data}\\Android\\Sdk\\platform-tools\\adb.exe"),
        "C:\\Program Files\\Netease\\MuMuPlayer-12.0\\shell\\adb.exe".to_string(),
        "C:\\Program Files\\Netease\\MuMu Player 12\\shell\\adb.exe".to_string(),
        "C:\\LDPlayer\\LDPlayer9\\adb.exe".to_string(),
        "C:\\LDPlayer\\LDPlayer14\\adb.exe".to_string(),
        "D:\\leidian\\LDPlayer14\\adb.exe".to_string(),
    ];

    for candidate in candidates {
        if command_exists(&candidate) {
            return Ok(candidate);
        }
    }

    Err(
        "未找到 adb。请安装 Android platform-tools，或设置环境变量 HUGOFGO_ADB 指向 adb.exe"
            .to_string(),
    )
}

fn run_adb(serial: Option<&str>, args: &[&str]) -> Result<String, String> {
    let adb = adb_path()?;
    let mut command = Command::new(adb);
    if let Some(serial) = serial {
        command.args(["-s", serial]);
    }
    command.args(args);

    let output = command
        .output()
        .map_err(|error| format!("无法启动 adb：{error}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("adb 执行失败：{}", stderr.trim()));
    }

    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

pub fn list_devices() -> Result<Vec<DeviceInfo>, String> {
    let text = run_adb(None, &["devices", "-l"])?;
    let mut devices = Vec::new();

    for line in text.lines().skip(1) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 2 {
            continue;
        }

        let mut info = DeviceInfo {
            serial: parts[0].to_string(),
            state: parts[1].to_string(),
            model: None,
            product: None,
            device: None,
        };
        for part in &parts[2..] {
            if let Some(value) = part.strip_prefix("model:") {
                info.model = Some(value.replace('_', " "));
            } else if let Some(value) = part.strip_prefix("product:") {
                info.product = Some(value.to_string());
            } else if let Some(value) = part.strip_prefix("device:") {
                info.device = Some(value.to_string());
            }
        }
        devices.push(info);
    }

    Ok(devices)
}

pub fn ensure_device(serial: &str) -> Result<(), String> {
    let state = run_adb(Some(serial), &["get-state"])?;
    let state = state.trim();
    if state == "device" {
        Ok(())
    } else {
        Err(format!(
            "设备 {serial} 当前状态为 {state}，请先完成授权或重新连接"
        ))
    }
}

fn parse_size(text: &str) -> Option<(u32, u32)> {
    let mut physical = None;
    let mut overridden = None;

    for line in text.lines() {
        let parsed = line.split_whitespace().find_map(|part| {
            let (width, height) = part.split_once('x')?;
            Some((width.parse::<u32>().ok()?, height.parse::<u32>().ok()?))
        });
        if let Some(size) = parsed {
            if line.contains("Override") {
                overridden = Some(size);
            } else {
                physical = Some(size);
            }
        }
    }

    overridden.or(physical)
}

fn parse_last_number(text: &str) -> Option<u32> {
    text.split(|character: char| !character.is_ascii_digit())
        .filter_map(|part| part.parse::<u32>().ok())
        .last()
}

pub fn get_device_info(serial: &str) -> Result<DeviceMetrics, String> {
    ensure_device(serial)?;

    let size_text = run_adb(Some(serial), &["shell", "wm", "size"])?;
    let (width, height) = parse_size(&size_text)
        .ok_or_else(|| format!("无法解析设备分辨率：{}", size_text.trim()))?;

    let density = run_adb(Some(serial), &["shell", "wm", "density"])
        .ok()
        .and_then(|text| parse_last_number(&text));

    let model = run_adb(Some(serial), &["shell", "getprop", "ro.product.model"])
        .ok()
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty());

    Ok(DeviceMetrics {
        serial: serial.to_string(),
        width,
        height,
        density,
        model,
    })
}

fn png_size(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() < 24 || &bytes[0..8] != b"\x89PNG\r\n\x1a\n" {
        return None;
    }
    let width = u32::from_be_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]);
    let height = u32::from_be_bytes([bytes[20], bytes[21], bytes[22], bytes[23]]);
    Some((width, height))
}

fn safe_filename(serial: &str) -> String {
    serial
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
                character
            } else {
                '_'
            }
        })
        .collect()
}

pub fn capture_frame(serial: &str) -> Result<CapturedFrame, String> {
    ensure_device(serial)?;

    let adb = adb_path()?;
    let output = Command::new(adb)
        .args(["-s", serial, "exec-out", "screencap", "-p"])
        .output()
        .map_err(|error| format!("无法启动 adb 截图：{error}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("adb 截图失败：{}", stderr.trim()));
    }

    let bytes = output.stdout;
    if bytes.len() < 24 {
        return Err("adb 截图返回为空或格式不正确".to_string());
    }

    let (width, height) = png_size(&bytes).ok_or_else(|| "截图不是有效的 PNG".to_string())?;
    let captured_at_ms = now_ms();
    let directory = env::temp_dir().join("hugofgo-frames");
    fs::create_dir_all(&directory).map_err(|error| format!("无法创建截图目录：{error}"))?;
    let path = directory.join(format!("{}-{}.png", safe_filename(serial), captured_at_ms));
    fs::write(&path, &bytes).map_err(|error| format!("无法保存截图：{error}"))?;

    Ok(CapturedFrame {
        base64: STANDARD.encode(&bytes),
        path: path.to_string_lossy().to_string(),
        width,
        height,
        captured_at_ms,
    })
}

pub fn tap_normalized(
    serial: &str,
    x: f64,
    y: f64,
    width: Option<u32>,
    height: Option<u32>,
) -> Result<TapResult, String> {
    ensure_device(serial)?;

    let (width, height) = match (width, height) {
        (Some(width), Some(height)) if width > 0 && height > 0 => (width, height),
        _ => {
            let metrics = get_device_info(serial)?;
            (metrics.width, metrics.height)
        }
    };

    let norm_x = x.clamp(0.0, 1.0);
    let norm_y = y.clamp(0.0, 1.0);
    let px = ((norm_x * width as f64).round() as u32).min(width.saturating_sub(1));
    let py = ((norm_y * height as f64).round() as u32).min(height.saturating_sub(1));

    let px_text = px.to_string();
    let py_text = py.to_string();
    run_adb(Some(serial), &["shell", "input", "tap", &px_text, &py_text])?;

    Ok(TapResult {
        serial: serial.to_string(),
        x: px,
        y: py,
        norm_x,
        norm_y,
    })
}
