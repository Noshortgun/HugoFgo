use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RecoveryItem {
    SaintQuartz,
    Gold,
    Silver,
    Bronze,
    Copper,
}

impl RecoveryItem {
    pub(crate) fn as_str(&self) -> &'static str {
        match self {
            Self::SaintQuartz => "saint_quartz",
            Self::Gold => "gold",
            Self::Silver => "silver",
            Self::Bronze => "bronze",
            Self::Copper => "copper",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub(crate) struct RecoveryPolicy {
    pub(crate) allowed: Vec<RecoveryItem>,
    pub(crate) drain_after_turns: bool,
}

impl Default for RecoveryPolicy {
    fn default() -> Self {
        Self {
            allowed: vec![RecoveryItem::Bronze],
            drain_after_turns: false,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum DeviceChannel {
    Adb,
    Scrcpy,
}

impl DeviceChannel {
    pub(crate) fn as_str(&self) -> &'static str {
        match self {
            Self::Adb => "adb",
            Self::Scrcpy => "scrcpy",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub(crate) struct RunSettings {
    pub(crate) battles: u32,
    pub(crate) recovery: RecoveryPolicy,
    pub(crate) device_channel: DeviceChannel,
    pub(crate) allow_adb_fallback: bool,
}

impl Default for RunSettings {
    fn default() -> Self {
        Self {
            battles: 1,
            recovery: RecoveryPolicy::default(),
            device_channel: DeviceChannel::Scrcpy,
            allow_adb_fallback: true,
        }
    }
}

fn settings_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|directory| directory.join("run-settings.json"))
        .map_err(|error| format!("无法定位应用数据目录：{error}"))
}

#[tauri::command]
pub(crate) fn get_run_settings(app: AppHandle) -> Result<RunSettings, String> {
    let path = settings_path(&app)?;
    match fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text).map_err(|error| format!("运行设置无法解析：{error}")),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(RunSettings::default()),
        Err(error) => Err(format!("运行设置读取失败：{error}")),
    }
}

#[tauri::command]
pub(crate) fn save_run_settings(app: AppHandle, settings: RunSettings) -> Result<RunSettings, String> {
    if settings.battles < 1 {
        return Err("连续出击场数必须至少为 1".to_string());
    }
    let path = settings_path(&app)?;
    let directory = path
        .parent()
        .ok_or_else(|| "运行设置路径无效".to_string())?;
    fs::create_dir_all(directory).map_err(|error| format!("无法创建应用数据目录：{error}"))?;
    let text = serde_json::to_string_pretty(&settings)
        .map_err(|error| format!("运行设置序列化失败：{error}"))?;
    let temporary = path.with_extension("json.tmp");
    fs::write(&temporary, text).map_err(|error| format!("运行设置写入失败：{error}"))?;
    fs::rename(&temporary, &path).map_err(|error| format!("运行设置保存失败：{error}"))?;
    Ok(settings)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_settings_allow_only_bronze() {
        let settings = RunSettings::default();

        assert_eq!(1, settings.battles);
        assert_eq!(vec![RecoveryItem::Bronze], settings.recovery.allowed);
        assert!(!settings.recovery.drain_after_turns);
        assert_eq!(settings.device_channel.as_str(), "scrcpy");
        assert!(settings.allow_adb_fallback);
    }
}
