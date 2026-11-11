#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Sidecar(Mutex<Option<Child>>);

fn free_port() -> std::io::Result<u16> {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
}

fn show_start_error(window: &tauri::WebviewWindow, message: &str) {
    let text = serde_json::to_string(message).unwrap_or_else(|_| "\"启动失败\"".into());
    let script = format!("(function show(){{const e=document.querySelector('[data-start-status]');if(!e){{setTimeout(show,100);return}}e.textContent={text};document.body.classList.add('failed');document.querySelector('[data-retry]').disabled=false}})()");
    let _ = window.eval(script);
}

fn stop_sidecar(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<Sidecar>() {
        if let Ok(mut slot) = state.0.lock() {
            if let Some(mut child) = slot.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn healthy(port: u16) -> bool {
    let addr = match format!("127.0.0.1:{port}").parse() {
        Ok(addr) => addr,
        Err(_) => return false,
    };
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let request = "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buffer = [0u8; 64];
    match stream.read(&mut buffer) {
        Ok(read) if read > 0 => String::from_utf8_lossy(&buffer).contains("200"),
        _ => false,
    }
}

#[cfg(windows)]
fn paint_caption(window: &tauri::WebviewWindow) {
    #[link(name = "dwmapi")]
    extern "system" {
        fn DwmSetWindowAttribute(hwnd: isize, attr: u32, value: *const core::ffi::c_void, size: u32) -> i32;
    }
    const DWMWA_CAPTION_COLOR: u32 = 35;
    const DWMWA_TEXT_COLOR: u32 = 36;
    if let Ok(hwnd) = window.hwnd() {
        let handle = hwnd.0 as isize;
        let caption: u32 = 0x001D1911; // #11191D in COLORREF (BBGGRR)
        let text: u32 = 0x00F2EEE8; // #E8EEF2
        unsafe {
            DwmSetWindowAttribute(handle, DWMWA_CAPTION_COLOR, &caption as *const u32 as *const _, 4);
            DwmSetWindowAttribute(handle, DWMWA_TEXT_COLOR, &text as *const u32 as *const _, 4);
        }
    }
}

fn start_service(app: &tauri::AppHandle, window: &tauri::WebviewWindow) {
            let state = app.state::<Sidecar>();
            let mut slot = match state.0.lock() {
                Ok(slot) => slot,
                Err(_) => { show_start_error(window, "无法启动服务，请关闭程序后重试。"); return; }
            };
            if let Some(child) = slot.as_mut() {
                if matches!(child.try_wait(), Ok(None)) { return; }
            }
            *slot = None;
            let _ = window.eval("document.body.classList.remove('failed');document.querySelector('[data-start-status]').textContent='正在启动本地服务…'");
            let resource_dir = match app.path().resource_dir() {
                Ok(path) => path,
                Err(_) => { show_start_error(window, "无法读取安装目录，请重新安装应用。"); return; }
            };
            let runtime = resource_dir.join("runtime");
            let executable = runtime.join("stepeye-runtime.exe");
            let data_dir = match app.path().app_data_dir() {
                Ok(path) => path,
                Err(_) => { show_start_error(window, "无法读取数据目录，请检查系统账户配置。"); return; }
            };
            if std::fs::create_dir_all(&data_dir).is_err() {
                show_start_error(window, "无法创建数据目录，请检查磁盘空间和目录权限。");
                return;
            }

            let port = match free_port() {
                Ok(port) => port,
                Err(_) => { show_start_error(window, "无法分配本机端口，请检查网络配置。"); return; }
            };
            let mut command = Command::new(&executable);
            command
                .current_dir(&runtime)
                .args([
                    "serve",
                    "_internal/configs/demo_sim.yaml",
                    "--port",
                    &port.to_string(),
                    "--data",
                ])
                .arg(&data_dir);

            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
            }

            let child = match command.spawn() {
                Ok(child) => child,
                Err(_) => { show_start_error(window, "本地服务无法启动，请检查安装文件。"); return; }
            };
            *slot=Some(child);
            drop(slot);
            let handle = app.clone();
            let window = window.clone();
            std::thread::spawn(move || {
                let started = Instant::now();
                while started.elapsed() < Duration::from_secs(90) {
                    let exited = match handle.try_state::<Sidecar>() {
                        Some(state) => match state.0.lock() {
                            Ok(mut slot) => match slot.as_mut() {
                                Some(child) => child.try_wait().map(|status| status.is_some()).unwrap_or(true),
                                None => return,
                            },
                            Err(_) => true,
                        },
                        None => return,
                    };
                    if exited {
                        stop_sidecar(&handle);
                        show_start_error(&window, "本地服务已退出，请检查安装文件和数据目录。");
                        return;
                    }
                    if healthy(port) {
                        if let Ok(url) = format!("http://127.0.0.1:{port}").parse() {
                            if window.navigate(url).is_err() {
                                stop_sidecar(&handle);
                                show_start_error(&window, "页面打开失败，请重新尝试。");
                            }
                        }
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(400));
                }
                stop_sidecar(&handle);
                show_start_error(&window, "本地服务启动超时，请检查设备资源。");
            });
}

#[tauri::command]
fn retry_start(app: tauri::AppHandle, window: tauri::WebviewWindow) {
    start_service(&app, &window);
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Sidecar(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![retry_start])
        .setup(|app| {
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("StepEye")
                .theme(Some(tauri::Theme::Dark))
                .inner_size(1440.0, 900.0)
                .min_inner_size(900.0, 600.0)
                .build()?;
            #[cfg(windows)]
            paint_caption(&window);
            start_service(app.handle(), &window);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build StepEye shell");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_sidecar(handle);
        }
    });
}
