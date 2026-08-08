// build.rs — required by Tauri v2.
// Generates the platform glue (window/dialog/manifest resources, Windows
// resource .rc) at compile time so `cargo build` can link the desktop shell.
fn main() {
    tauri_build::build()
}
