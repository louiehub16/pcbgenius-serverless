// PCBGenius desktop shell — Tauri v2 application entry.
//
// The heavy lifting (schematic canvas, netlist validation) lives in the
// React+Vite frontend under `../dist`. This Rust crate is the thin native
// shell that gives it a native Windows window and an installable .msi.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![greet])
        .run(tauri::generate_context!())
        .expect("error while running pcbgenius tauri application");
}

/// Minimal IPC command. Placeholder for future native commands (e.g. SPI
/// donor-board capture). Frontend calls it via `invoke("greet", {...})`.
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! Welcome to PCBGenius.", name)
}
